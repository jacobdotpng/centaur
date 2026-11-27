"""CLI for rendering Mermaid diagrams and posting them to Slack."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="mermaid",
    help="Render Mermaid diagrams to PNG with the sandbox's baked Chrome, and post them to Slack.",
    epilog=(
        "Mermaid syntax reference: https://mermaid.js.org/intro/syntax-reference.html\n\n"
        "Render locally: mermaid render diagram.mmd -o diagram.png\n\n"
        "Render and post to the current Slack thread: mermaid post diagram.mmd "
        '--comment "Proposed architecture"'
    ),
    no_args_is_help=True,
)

DEFAULT_ALT_TEXT = "Mermaid architecture diagram"


def _read_source(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _fail(message: str) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1)


@app.command("render")
def render(
    source_path: Annotated[str, typer.Argument(help="Mermaid .mmd file, or - for stdin")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Output .png path")],
    scale: Annotated[float, typer.Option(help="PNG scale factor (0.1 to 4.0)")] = 2.0,
    width: Annotated[int | None, typer.Option(help="Page width in CSS pixels")] = None,
    background: Annotated[str, typer.Option(help="Background color")] = "white",
) -> None:
    """Render SOURCE_PATH to a PNG at --output.

    Invalid Mermaid exits non-zero with mermaid's parse error and writes no
    file. On success, prints JSON metadata.
    """
    from .client import MermaidError
    from .client import render as render_png

    try:
        result = render_png(
            _read_source(source_path), output, scale=scale, width=width, background=background
        )
    except (MermaidError, OSError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(result))


@app.command("post")
def post(
    source_path: Annotated[str, typer.Argument(help="Mermaid .mmd file, or - for stdin")],
    comment: Annotated[
        str | None, typer.Option("--comment", "-c", help="Message posted with the image")
    ] = None,
    filename: Annotated[str, typer.Option(help="Uploaded PNG filename")] = "diagram.png",
    alt_text: Annotated[str, typer.Option("--alt-text", help="Image alt text")] = DEFAULT_ALT_TEXT,
    keep: Annotated[
        Path | None, typer.Option("--keep", help="Also save the rendered PNG here")
    ] = None,
    scale: Annotated[float, typer.Option(help="PNG scale factor (0.1 to 4.0)")] = 2.0,
) -> None:
    """Render SOURCE_PATH and upload the PNG to the current Slack thread.

    The destination comes from the API-owned session context, and the upload
    goes through the existing `slack upload` proxy, so no extra credentials
    are needed. If rendering fails, nothing is uploaded and the command exits
    non-zero: put the Mermaid source in a ```mermaid code block in your reply
    and say the diagram could not be rendered.
    """
    from .client import MermaidError, MermaidRenderError
    from .client import post as post_png

    try:
        result = post_png(
            _read_source(source_path),
            comment=comment,
            filename=filename,
            alt_text=alt_text,
            keep=keep,
            scale=scale,
        )
    except MermaidRenderError as exc:
        _fail(
            f"render failed, nothing was uploaded: {exc}\n"
            "Fallback: include the Mermaid source in a ```mermaid code block in your reply "
            "and say the diagram could not be rendered."
        )
    except (MermaidError, OSError) as exc:
        _fail(str(exc))
    typer.echo(json.dumps(result))


@app.command("health")
def health() -> None:
    """Verify that mermaid-cli can render a PNG with the baked Chrome."""
    import tempfile

    from .client import render as render_png

    try:
        with tempfile.TemporaryDirectory(prefix="mermaid-health-") as work:
            result = render_png("flowchart LR\n  A --> B\n", Path(work) / "health.png", scale=1)
        payload = {"ok": True, "tool": "mermaid", "error": None, "details": result}
    except Exception as exc:
        payload = {"ok": False, "tool": "mermaid", "error": str(exc), "details": {}}
        typer.echo(json.dumps(payload, ensure_ascii=False))
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    app()
