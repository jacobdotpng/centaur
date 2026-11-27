"""Behavior tests with real mmdc/Chrome and the existing Slack upload CLI.

Run with the Slack tool's dependencies and tools/productivity on PYTHONPATH.
Only the final HTTP transport is replaced: these tests never post to Slack.
"""

import base64
import io
import json
import os
import shutil
import struct
import subprocess
import urllib.error
import urllib.parse
from pathlib import Path

import pytest
from centaur_tool_mermaid import client
from centaur_tool_mermaid.cli import app
from typer.testing import CliRunner

VALID = "flowchart LR\n  Slack --> API --> Sandbox\n"
INVALID = "flowchart LR\n  A -->> -- B[\n"
PNG_FIXTURE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII="
)


@pytest.fixture
def renderer():
    try:
        client.find_mmdc()
        client.find_chrome()
    except client.MermaidError as exc:
        pytest.skip(str(exc))


def save_evidence(name, data):
    directory = os.environ.get("MERMAID_TEST_EVIDENCE_DIR")  # noqa: TID251 - test artifact directory
    if directory:
        output = Path(directory) / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data if isinstance(data, bytes) else data.encode())


def assert_png(data):
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert data[12:16] == b"IHDR"
    width, height = struct.unpack(">II", data[16:24])
    assert width > 0 and height > 0
    assert data.endswith(b"IEND\xaeB`\x82")


@pytest.mark.parametrize("stdin", [False, True], ids=["file", "stdin"])
def test_render_cli_writes_exact_stubbed_png_and_size(tmp_path, monkeypatch, stdin):
    chrome = tmp_path / "chrome"
    chrome.touch()
    monkeypatch.setenv("MERMAID_CHROME_PATH", str(chrome))
    monkeypatch.setenv("MERMAID_MMDC", "/test/mmdc")
    sources = []

    def run(command, **kwargs):
        assert command[0] == "/test/mmdc"
        sources.append(Path(command[command.index("--input") + 1]).read_text())
        Path(command[command.index("--output") + 1]).write_bytes(PNG_FIXTURE)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setitem(client.render.__kwdefaults__, "run", run)
    source = tmp_path / "architecture.mmd"
    source.write_text(VALID)
    output = tmp_path / "nested" / "architecture.png"
    result = CliRunner().invoke(
        app,
        ["render", "-" if stdin else str(source), "-o", str(output)],
        input=VALID if stdin else None,
    )
    assert result.exit_code == 0, result.output
    assert sources == [VALID]
    assert output.read_bytes() == PNG_FIXTURE
    assert json.loads(result.stdout) == {
        "ok": True,
        "format": "png",
        "output": str(output),
        "bytes": len(PNG_FIXTURE),
    }
    save_evidence(f"stub-render-{'stdin' if stdin else 'file'}.png", output.read_bytes())
    save_evidence(f"stub-render-{'stdin' if stdin else 'file'}.json", result.stdout)


def test_render_cli_cleans_subprocess_parse_error_and_discards_partial_png(tmp_path, monkeypatch):
    chrome = tmp_path / "chrome"
    chrome.touch()
    monkeypatch.setenv("MERMAID_CHROME_PATH", str(chrome))
    monkeypatch.setenv("MERMAID_MMDC", "/test/mmdc")
    temporary_outputs = []

    def run(command, **kwargs):
        assert command[0] == "/test/mmdc"
        temporary = Path(command[command.index("--output") + 1])
        temporary.write_bytes(PNG_FIXTURE)
        temporary_outputs.append(temporary)
        return subprocess.CompletedProcess(
            command, 1, "",
            "Error: Error: Parse error on line 2:\nExpecting 'NODE', got 'EOF'\n"
            "Parser.parseError (https://renderer.invalid/chunk.mjs:1:2)\n"
            "    at async render (file:///renderer/index.js:3:4)\n",
        )

    monkeypatch.setitem(client.render.__kwdefaults__, "run", run)
    output = tmp_path / "bad.png"
    result = CliRunner().invoke(app, ["render", "-", "-o", str(output)], input=INVALID)
    assert result.exit_code == 1
    assert "Parse error on line 2" in result.output
    assert "Expecting 'NODE', got 'EOF'" in result.output
    assert "Error: Error: Error:" not in result.output
    assert "renderer.invalid" not in result.output
    assert "index.js" not in result.output
    assert not output.exists()
    assert not list(tmp_path.glob("*.part"))
    assert len(temporary_outputs) == 1
    assert not temporary_outputs[0].parent.exists()
    save_evidence("stub-render-parse-error.txt", f"exit={result.exit_code}\n{result.output}")


@pytest.mark.parametrize("stdin", [False, True], ids=["file", "stdin"])
def test_render_cli_produces_real_png(renderer, tmp_path, stdin):
    source = tmp_path / "architecture.mmd"
    source.write_text(VALID)
    output = tmp_path / "nested" / "architecture.png"
    result = CliRunner().invoke(
        app,
        ["render", "-" if stdin else str(source), "-o", str(output), "--scale", "1"],
        input=VALID if stdin else None,
    )
    assert result.exit_code == 0, result.output
    data = output.read_bytes()
    assert_png(data)
    metadata = json.loads(result.stdout)
    assert metadata["ok"] is True
    assert metadata["bytes"] == len(data)
    save_evidence(f"render-{'stdin' if stdin else 'file'}.png", data)
    save_evidence("render-source.mmd", VALID)


def test_invalid_render_cli_reports_parse_error_without_output(renderer, tmp_path):
    source = tmp_path / "invalid.mmd"
    source.write_text(INVALID)
    output = tmp_path / "bad.png"
    result = CliRunner().invoke(app, ["render", str(source), "-o", str(output)])
    assert result.exit_code != 0
    assert "Parse error" in result.output
    assert not output.exists()
    assert not list(tmp_path.glob("*.part"))
    save_evidence("invalid-render.txt", f"exit={result.exit_code}\n{result.output}")


@pytest.fixture
def slack_transport(monkeypatch):
    slack_client = pytest.importorskip("slack.client")
    slack_cli = pytest.importorskip("slack.cli")
    import centaur_sdk.tool_sdk as sdk

    # API-owned context, rather than an explicit destination passed to post().
    monkeypatch.setattr(
        sdk,
        "current_session_context",
        lambda: {
            "platform": "slack",
            "slack": {"channel_id": "C1234567890", "thread_ts": "1700000000.000100"},
        },
    )
    monkeypatch.setenv("CENTAUR_API_URL", "http://api.invalid:8000")
    monkeypatch.setenv("CENTAUR_API_BEARER_TOKEN", "CENTAUR_API_BEARER_TOKEN")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    proxy_client = slack_client.SlackClient(bot_token="test-placeholder-unused-by-proxy")
    monkeypatch.setattr(slack_client, "_client", lambda: proxy_client)
    original_which = shutil.which
    monkeypatch.setattr(
        client.shutil,
        "which",
        lambda name: "/test/slack" if name == "slack" else original_which(name),
    )
    requests = []
    failure = []

    def urlopen(request, **kwargs):
        requests.append(request)
        if failure:
            raise urllib.error.HTTPError(
                request.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"message":"upload denied"}')
            )
        return io.BytesIO(b'{"ok":true,"file_id":"FTEST123"}')

    monkeypatch.setattr(slack_client.urllib.request, "urlopen", urlopen)

    def dispatch(command, **kwargs):
        if command[0] != "/test/slack":
            return subprocess.run(command, **kwargs)
        # Execute the actual existing CLI parser and upload_file_proxy client.
        result = CliRunner().invoke(slack_cli.app, command[1:])
        return subprocess.CompletedProcess(command, result.exit_code, result.output, "")

    return dispatch, requests, failure


@pytest.fixture
def upload_transport(slack_transport, tmp_path, monkeypatch):
    dispatch, requests, failure = slack_transport
    # Isolate upload behavior from browser availability with a fixed PNG.
    # post(), Slack's CLI parser, file reading, and HTTP client all remain real.
    chrome = tmp_path / "chrome"
    chrome.touch()
    monkeypatch.setenv("MERMAID_CHROME_PATH", str(chrome))
    monkeypatch.setenv("MERMAID_MMDC", "/test/mmdc")

    def run(command, **kwargs):
        if command[0] == "/test/mmdc":
            Path(command[command.index("--output") + 1]).write_bytes(PNG_FIXTURE)
            return subprocess.CompletedProcess(command, 0, "", "")
        return dispatch(command, **kwargs)

    return run, requests, failure


def test_post_sends_png_through_existing_upload_http_path(tmp_path, upload_transport):
    dispatch, requests, _ = upload_transport
    keep = tmp_path / "architecture.png"
    result = client.post(
        VALID,
        comment="Proposed architecture",
        alt_text="Slack to API to sandbox",
        keep=keep,
        run=dispatch,
    )
    assert result["ok"] is True
    assert "FTEST123" in result["upload"]
    [request] = requests
    url = urllib.parse.urlparse(request.full_url)
    query = urllib.parse.parse_qs(url.query)
    assert request.get_method() == "POST"
    assert url.path == "/api/slack/files/upload"
    assert query["channel_id"] == ["C1234567890"]
    assert query["thread_ts"] == ["1700000000.000100"]
    assert query["filename"] == ["diagram.png"]
    assert query["initial_comment"] == ["Proposed architecture"]
    assert query["alt_txt"] == ["Slack to API to sandbox"]
    assert request.get_header("Content-type") == "image/png"
    # The SDK supplies the existing proxy placeholder, never a new credential.
    assert request.get_header("Authorization") == "Bearer CENTAUR_API_BEARER_TOKEN"
    assert request.data == keep.read_bytes()
    assert_png(request.data)
    save_evidence("upload-body.png", request.data)
    save_evidence(
        "upload-request.json",
        json.dumps(
            {
                "method": request.get_method(),
                "url": request.full_url,
                "headers": dict(request.header_items()),
                "bytes": len(request.data),
                "result": result,
                "transport": "HTTP intercepted locally, fixed PNG renderer fixture, no live Slack request",
            },
            indent=2,
        ),
    )


def test_post_invalid_source_never_calls_upload(renderer, slack_transport):
    dispatch, requests, _ = slack_transport
    with pytest.raises(client.MermaidRenderError, match="Parse error"):
        client.post(INVALID, run=dispatch)
    assert requests == []


def test_post_reports_real_upload_cli_proxy_denial(upload_transport):
    dispatch, requests, failure = upload_transport
    failure.append(True)
    with pytest.raises(client.MermaidError, match="upload denied"):
        client.post(VALID, run=dispatch)
    assert len(requests) == 1


@pytest.mark.parametrize("missing", ["channel_id", "thread_ts"])
def test_post_missing_current_destination_does_not_render_or_upload(
    missing, upload_transport, monkeypatch
):
    import centaur_sdk.tool_sdk as sdk

    dispatch, requests, _ = upload_transport
    destination = {"channel_id": "C1234567890", "thread_ts": "1700000000.000100"}
    del destination[missing]
    monkeypatch.setattr(
        sdk, "current_session_context", lambda: {"platform": "slack", "slack": destination}
    )
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return dispatch(command, **kwargs)

    with pytest.raises(RuntimeError, match="Slack thread"):
        client.post(VALID, run=run)
    assert calls == []
    assert requests == []


@pytest.mark.parametrize("denied", [False, True], ids=["success", "denied"])
def test_post_cleans_temporary_png_after_upload(upload_transport, denied):
    dispatch, requests, failure = upload_transport
    paths = []
    if denied:
        failure.append(True)

    def run(command, **kwargs):
        if command[0] == "/test/slack":
            paths.append(Path(command[3]))
            assert_png(paths[-1].read_bytes())
        return dispatch(command, **kwargs)

    if denied:
        with pytest.raises(client.MermaidError, match="upload denied"):
            client.post(VALID, run=run)
    else:
        assert client.post(VALID, run=run)["ok"] is True
    assert len(requests) == 1
    assert len(paths) == 1
    assert not paths[0].exists()
    assert not paths[0].parent.exists()
