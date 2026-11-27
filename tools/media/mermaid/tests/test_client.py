import json
import os
import subprocess
from pathlib import Path

import pytest
from centaur_tool_mermaid import client
from centaur_tool_mermaid.client import (
    CHROME_ARGS,
    PNG_SIGNATURE,
    MermaidError,
    MermaidRenderError,
    find_chrome,
    post,
    render,
)

VALID = "flowchart LR\n  Slack --> API --> Sandbox\n"
PARSE_ERROR = (
    "Error: Error: Parse error on line 2:\n"
    "flowchart LR  A -->> -- B[\n"
    "-------------------^\n"
    "Expecting 'AMP', 'COLON', got 'TAGEND'\n"
    "Parser.parseError (https://mermaid-cli-intercept.invalid/x/chunk.mjs:1636:21)\n"
    "    at async render (file:///usr/lib/node_modules/x/src/index.js:10:2)\n"
)


class FakeRun:
    """Stand-in for subprocess.run that emulates mmdc and `slack upload`."""

    def __init__(self, *, mmdc_code=0, mmdc_stderr="", png=PNG_SIGNATURE + b"data", slack_code=0):
        self.calls: list[list[str]] = []
        self.mmdc_code = mmdc_code
        self.mmdc_stderr = mmdc_stderr
        self.png = png
        self.slack_code = slack_code
        self.puppeteer_config: dict | None = None
        self.uploaded: bytes | None = None

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        if command[0].endswith("mmdc"):
            config = Path(command[command.index("--puppeteerConfigFile") + 1])
            self.puppeteer_config = json.loads(config.read_text())
            assert kwargs["env"]["PUPPETEER_SKIP_DOWNLOAD"] == "1"
            if self.mmdc_code == 0:
                Path(command[command.index("--output") + 1]).write_bytes(self.png)
            return subprocess.CompletedProcess(command, self.mmdc_code, "", self.mmdc_stderr)
        self.uploaded = Path(command[3]).read_bytes()
        out = "✓ Uploaded diagram.png\nF123" if self.slack_code == 0 else "Error: not_in_channel"
        return subprocess.CompletedProcess(command, self.slack_code, out, "")

    def commands(self, name):
        return [call for call in self.calls if call[0].endswith(name)]


@pytest.fixture
def tools_env(tmp_path, monkeypatch):
    chrome = tmp_path / "chrome"
    chrome.write_text("")
    monkeypatch.setattr(client.shutil, "which", lambda name: f"/usr/bin/{name}")
    return {"MERMAID_MMDC": "/usr/lib/node_modules/.bin/mmdc", "MERMAID_CHROME_PATH": str(chrome)}


def test_find_chrome_prefers_explicit_path(tmp_path):
    chrome = tmp_path / "custom-chrome"
    chrome.write_text("")
    assert find_chrome({"MERMAID_CHROME_PATH": str(chrome)}, home=tmp_path) == str(chrome)


def test_find_chrome_uses_newest_baked_chrome_for_testing(tmp_path):
    for version in ("99.0.1.2", "158.0.8127.2", "157.0.8103.38"):
        binary = tmp_path / ".agent-browser" / "browsers" / f"chrome-{version}" / "chrome"
        binary.parent.mkdir(parents=True)
        binary.write_text("")
    found = find_chrome({}, home=tmp_path)
    assert found.endswith("chrome-158.0.8127.2/chrome")


def test_find_chrome_reports_missing_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(client.shutil, "which", lambda name: None)
    with pytest.raises(MermaidError, match="no Chrome binary"):
        find_chrome({"MERMAID_CHROME_PATH": str(tmp_path / "missing")}, home=tmp_path)


def test_render_writes_png_using_baked_chrome_without_sandbox(tmp_path, tools_env):
    run = FakeRun()
    output = tmp_path / "out" / "diagram.png"

    result = render(VALID, output, run=run, env=tools_env)

    assert output.read_bytes().startswith(PNG_SIGNATURE)
    assert result == {
        "ok": True,
        "format": "png",
        "output": str(output),
        "bytes": output.stat().st_size,
    }
    assert run.puppeteer_config == {
        "executablePath": tools_env["MERMAID_CHROME_PATH"],
        "headless": True,
        "args": CHROME_ARGS,
    }
    assert "--no-sandbox" in CHROME_ARGS
    assert not list(output.parent.glob(".*.part"))


def test_render_parse_error_raises_cleaned_message_and_writes_nothing(tmp_path, tools_env):
    output = tmp_path / "diagram.png"

    with pytest.raises(MermaidRenderError) as excinfo:
        render(
            "flowchart LR\n  A -->> -- B[\n",
            output,
            run=FakeRun(mmdc_code=1, mmdc_stderr=PARSE_ERROR),
            env=tools_env,
        )

    message = str(excinfo.value)
    assert message.startswith("Error: Parse error on line 2:")
    assert "Expecting 'AMP'" in message
    assert "mermaid-cli-intercept" not in message
    assert "index.js" not in message
    assert not output.exists()
    assert [p.name for p in tmp_path.iterdir()] == ["chrome"]


def test_render_rejects_non_png_output(tmp_path, tools_env):
    output = tmp_path / "diagram.png"
    with pytest.raises(MermaidRenderError, match="not a PNG"):
        render(VALID, output, run=FakeRun(png=b"<svg/>"), env=tools_env)
    assert not output.exists()


@pytest.mark.parametrize(
    ("source", "output", "message"),
    [
        ("   \n", "d.png", "empty"),
        (VALID, "d.svg", ".png"),
    ],
)
def test_render_validates_inputs_before_running(tmp_path, tools_env, source, output, message):
    run = FakeRun()
    with pytest.raises(MermaidRenderError, match=message):
        render(source, tmp_path / output, run=run, env=tools_env)
    assert run.calls == []


def test_post_uploads_png_to_thread_through_slack_upload(tmp_path, tools_env):
    run = FakeRun()
    keep = tmp_path / "kept.png"

    result = post(
        VALID,
        comment="Proposed architecture",
        alt_text="Architecture diagram",
        keep=keep,
        destination={"channel_id": "C0123456789", "thread_ts": "1700000000.000100"},
        run=run,
        env=tools_env,
    )

    [upload] = run.commands("slack")
    assert upload[:3] == ["/usr/bin/slack", "upload", "C0123456789"]
    assert Path(upload[3]).name == "diagram.png"
    assert upload[4:] == [
        "--thread",
        "1700000000.000100",
        "--content-type",
        "image/png",
        "--comment",
        "Proposed architecture",
        "--alt-text",
        "Architecture diagram",
    ]
    assert run.uploaded.startswith(PNG_SIGNATURE)
    assert keep.read_bytes() == run.uploaded
    assert result["ok"] is True
    assert result["thread_ts"] == "1700000000.000100"
    assert "F123" in result["upload"]
    assert not Path(upload[3]).exists()


def test_post_does_not_upload_when_render_fails(tools_env):
    run = FakeRun(mmdc_code=1, mmdc_stderr=PARSE_ERROR)
    with pytest.raises(MermaidRenderError):
        post(
            "flowchart LR\n  A -->> -- B[\n",
            destination={"channel_id": "C1", "thread_ts": "1.2"},
            run=run,
            env=tools_env,
        )
    assert run.commands("slack") == []


def test_post_reports_upload_failure(tools_env):
    run = FakeRun(slack_code=1)
    with pytest.raises(MermaidError, match="slack upload failed: Error: not_in_channel"):
        post(VALID, destination={"channel_id": "C1", "thread_ts": "1.2"}, run=run, env=tools_env)


def test_post_resolves_destination_from_session_context(monkeypatch, tools_env):
    import centaur_sdk.tool_sdk as tool_sdk

    monkeypatch.setattr(
        tool_sdk,
        "current_chat_destination",
        lambda: {"platform": "slack", "channel_id": "C0AJ07U8Z1N", "thread_ts": "1712.000001"},
    )
    run = FakeRun()

    post(VALID, run=run, env=tools_env)

    [upload] = run.commands("slack")
    assert upload[2] == "C0AJ07U8Z1N"
    assert upload[upload.index("--thread") + 1] == "1712.000001"


def test_post_refuses_non_slack_surfaces(monkeypatch, tools_env):
    import centaur_sdk.tool_sdk as tool_sdk

    monkeypatch.setattr(
        tool_sdk, "current_chat_destination", lambda: {"platform": "linear", "issue_id": "X"}
    )
    run = FakeRun()
    with pytest.raises(MermaidError, match="not Slack"):
        post(VALID, run=run, env=tools_env)
    assert run.calls == []


def _real_renderer_env() -> dict[str, str] | None:
    try:
        client.find_mmdc()
        client.find_chrome()
    except MermaidError:
        return None
    return dict(os.environ)


@pytest.mark.skipif(_real_renderer_env() is None, reason="mmdc and Chrome are not installed")
def test_real_mermaid_cli_renders_valid_and_rejects_invalid(tmp_path):
    good = tmp_path / "good.png"
    result = render(VALID, good, scale=1)
    assert good.read_bytes().startswith(PNG_SIGNATURE)
    assert result["bytes"] > 0

    bad = tmp_path / "bad.png"
    with pytest.raises(MermaidRenderError, match="Parse error"):
        render("flowchart LR\n  A -->> -- B[\n", bad, scale=1)
    assert not bad.exists()
