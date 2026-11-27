import re

from centaur_tool_mermaid import client
from centaur_tool_mermaid.cli import app
from centaur_tool_mermaid.client import MermaidRenderError
from typer.testing import CliRunner

runner = CliRunner()
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _text(output: str) -> str:
    return " ".join(ANSI_ESCAPE.sub("", output).split())


def test_help_lists_render_and_post():
    result = runner.invoke(app, ["--help"])
    text = _text(result.stdout)
    assert result.exit_code == 0
    assert "render" in text and "post" in text
    assert "mermaid render diagram.mmd -o diagram.png" in text


def test_post_help_explains_fallback():
    result = runner.invoke(app, ["post", "--help"])
    text = _text(result.stdout)
    assert result.exit_code == 0
    assert "current Slack thread" in text
    assert "code block" in text


def test_render_invalid_exits_nonzero_with_parse_error_and_no_file(tmp_path, monkeypatch):
    source = tmp_path / "bad.mmd"
    source.write_text("flowchart LR\n  A -->> -- B[\n")
    output = tmp_path / "bad.png"

    def fail(*args, **kwargs):
        raise MermaidRenderError("Error: Parse error on line 2:")

    monkeypatch.setattr(client, "render", fail)
    result = runner.invoke(app, ["render", str(source), "-o", str(output)])

    assert result.exit_code == 1
    assert "Parse error on line 2" in result.output
    assert not output.exists()


def test_render_reads_stdin(tmp_path, monkeypatch):
    seen = {}

    def fake_render(source, output, **kwargs):
        seen["source"] = source
        return {"ok": True, "output": str(output), "bytes": 9}

    monkeypatch.setattr(client, "render", fake_render)
    result = runner.invoke(
        app, ["render", "-", "-o", str(tmp_path / "d.png")], input="flowchart LR\n A-->B\n"
    )

    assert result.exit_code == 0
    assert seen["source"] == "flowchart LR\n A-->B\n"
    assert '"ok": true' in result.stdout


def test_post_render_failure_points_to_code_block_fallback(tmp_path, monkeypatch):
    source = tmp_path / "bad.mmd"
    source.write_text("flowchart LR\n  A -->> -- B[\n")

    def fail(*args, **kwargs):
        raise MermaidRenderError("Error: Parse error on line 2:")

    monkeypatch.setattr(client, "post", fail)
    result = runner.invoke(app, ["post", str(source), "--comment", "Plan"])

    assert result.exit_code == 1
    text = _text(result.output)
    assert "nothing was uploaded" in text
    assert "Parse error on line 2" in text
    assert "```mermaid code block" in text


def test_post_prints_upload_result(tmp_path, monkeypatch):
    source = tmp_path / "ok.mmd"
    source.write_text("flowchart LR\n A-->B\n")
    seen = {}

    def fake_post(text, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "channel_id": "C1", "thread_ts": "1.2"}

    monkeypatch.setattr(client, "post", fake_post)
    result = runner.invoke(app, ["post", str(source), "-c", "Proposed architecture"])

    assert result.exit_code == 0
    assert seen["comment"] == "Proposed architecture"
    assert seen["alt_text"] == "Mermaid architecture diagram"
    assert '"thread_ts": "1.2"' in result.stdout
