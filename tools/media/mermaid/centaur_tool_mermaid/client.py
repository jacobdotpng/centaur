"""Render Mermaid diagrams with the sandbox's baked mermaid-cli and Chrome."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

MAX_SOURCE_BYTES = 256 * 1024
MAX_SCALE = 4.0
RENDER_TIMEOUT_SECONDS = 120
UPLOAD_TIMEOUT_SECONDS = 180
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Chrome runs as the sandbox's non-root user inside a container without
# unprivileged user namespaces, so Chrome's own sandbox cannot start.
CHROME_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
_CHROME_VERSION_DIR = re.compile(r"^chrome-(\d+(?:\.\d+)*)$")
# Browser-side frames look like `Parser.parse (https://...:1708:16)`.
_BROWSER_FRAME = re.compile(r"^[\w.$<>\[\] ]+ \((?:https?|file):.*\)$")
_SYSTEM_CHROMES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")


class MermaidError(RuntimeError):
    """The diagram could not be rendered or delivered."""


class MermaidRenderError(MermaidError):
    """mermaid-cli rejected the diagram or failed to produce a PNG."""


def _version_key(path: Path) -> tuple[int, ...]:
    match = _CHROME_VERSION_DIR.match(path.parent.name)
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def find_chrome(env: Mapping[str, str] | None = None, home: Path | None = None) -> str:
    """Locate the baked Chrome for Testing binary (no second browser download)."""
    env = os.environ if env is None else env
    for name in ("MERMAID_CHROME_PATH", "PUPPETEER_EXECUTABLE_PATH"):
        configured = env.get(name, "").strip()
        if configured and Path(configured).is_file():
            return configured
    browsers = (home or Path.home()) / ".agent-browser" / "browsers"
    candidates = sorted(
        (path for path in browsers.glob("chrome-*/chrome") if path.is_file()),
        key=_version_key,
    )
    if candidates:
        return str(candidates[-1])
    for name in _SYSTEM_CHROMES:
        found = shutil.which(name)
        if found:
            return found
    raise MermaidError(
        "no Chrome binary found; set MERMAID_CHROME_PATH or install Chrome under "
        "~/.agent-browser/browsers"
    )


def find_mmdc(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    configured = env.get("MERMAID_MMDC", "").strip()
    if configured:
        return configured
    found = shutil.which("mmdc")
    if not found:
        raise MermaidError("mermaid-cli (mmdc) is not installed in this sandbox")
    return found


def _is_stack_frame(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("at ") or bool(_BROWSER_FRAME.match(stripped))


def _clean_error(stderr: str, stdout: str) -> str:
    """Keep mermaid-cli's message and drop Node and browser stack frames."""
    lines = [
        line.rstrip()
        for line in (stderr or stdout).splitlines()
        if line.strip() and not _is_stack_frame(line)
    ]
    if lines and lines[0].startswith("Error: Error: "):
        lines[0] = lines[0].removeprefix("Error: ")
    return "\n".join(lines[:20]).strip() or "mermaid-cli failed without an error message"


def _validate_source(source: str) -> str:
    if not source.strip():
        raise MermaidRenderError("Mermaid source is empty")
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise MermaidRenderError(f"Mermaid source exceeds {MAX_SOURCE_BYTES} bytes")
    return source


def render(
    source: str,
    output: Path,
    *,
    scale: float = 2.0,
    background: str = "white",
    width: int | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Render Mermaid ``source`` to a PNG at ``output``.

    The PNG is produced in a private temp directory and moved into place only
    after mermaid-cli succeeds, so an invalid diagram never leaves a file at
    ``output``.
    """
    _validate_source(source)
    if not 0.1 <= scale <= MAX_SCALE:
        raise MermaidRenderError(f"scale must be between 0.1 and {MAX_SCALE}")
    if output.suffix.lower() != ".png":
        raise MermaidRenderError("output must be a .png path")

    mmdc = find_mmdc(env)
    chrome = find_chrome(env)
    with tempfile.TemporaryDirectory(prefix="mermaid-render-") as work:
        work_dir = Path(work)
        input_path = work_dir / "diagram.mmd"
        rendered_path = work_dir / "diagram.png"
        config_path = work_dir / "puppeteer.json"
        input_path.write_text(source, encoding="utf-8")
        config_path.write_text(
            json.dumps({"executablePath": chrome, "headless": True, "args": CHROME_ARGS})
        )
        command = [
            mmdc,
            "--input",
            str(input_path),
            "--output",
            str(rendered_path),
            "--puppeteerConfigFile",
            str(config_path),
            "--backgroundColor",
            background,
            "--scale",
            str(scale),
            "--quiet",
        ]
        if width:
            command += ["--width", str(width)]
        child_env = dict(os.environ if env is None else env)
        child_env["PUPPETEER_SKIP_DOWNLOAD"] = "1"
        try:
            result = run(
                command,
                capture_output=True,
                text=True,
                timeout=RENDER_TIMEOUT_SECONDS,
                env=child_env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise MermaidRenderError(
                f"mermaid-cli timed out after {RENDER_TIMEOUT_SECONDS}s"
            ) from exc
        if result.returncode != 0:
            raise MermaidRenderError(_clean_error(result.stderr, result.stdout))
        if not rendered_path.is_file():
            raise MermaidRenderError("mermaid-cli exited successfully but wrote no PNG")
        data = rendered_path.read_bytes()
        if not data.startswith(PNG_SIGNATURE):
            raise MermaidRenderError("mermaid-cli output is not a PNG")
        output.parent.mkdir(parents=True, exist_ok=True)
        staged = output.with_name(f".{output.name}.part")
        shutil.copyfile(rendered_path, staged)
        os.replace(staged, output)
    return {"ok": True, "format": "png", "output": str(output), "bytes": len(data)}


def current_slack_destination() -> dict[str, str]:
    """Return the current Slack channel/thread from API-owned session context."""
    from centaur_sdk.tool_sdk import current_chat_destination

    destination = current_chat_destination()
    if destination.get("platform") != "slack":
        raise MermaidError(
            f"current chat surface is {destination.get('platform')!r}, not Slack; "
            "render with `mermaid render` and share it another way"
        )
    channel_id = str(destination.get("channel_id") or "")
    thread_ts = str(destination.get("thread_ts") or "")
    if not channel_id or not thread_ts:
        raise MermaidError("current Slack destination is missing channel_id or thread_ts")
    return {"channel_id": channel_id, "thread_ts": thread_ts}


def upload_to_slack(
    png: Path,
    destination: Mapping[str, str],
    *,
    comment: str | None = None,
    alt_text: str | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """Upload ``png`` through the existing `slack upload` proxy command.

    `slack upload` posts through the Centaur API server's
    `/api/slack/files/upload` proxy with the sandbox's principal-scoped
    credentials, so this tool needs no Slack credentials of its own.
    """
    slack = shutil.which("slack")
    if not slack:
        raise MermaidError("the `slack` tool is not installed in this sandbox")
    command = [
        slack,
        "upload",
        destination["channel_id"],
        str(png),
        "--thread",
        destination["thread_ts"],
        "--content-type",
        "image/png",
    ]
    if comment:
        command += ["--comment", comment]
    if alt_text:
        command += ["--alt-text", alt_text]
    try:
        result = run(
            command, capture_output=True, text=True, timeout=UPLOAD_TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise MermaidError(f"slack upload timed out after {UPLOAD_TIMEOUT_SECONDS}s") from exc
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip() or f"exit {result.returncode}"
        raise MermaidError(f"slack upload failed: {detail}")
    return result.stdout.strip()


def post(
    source: str,
    *,
    filename: str = "diagram.png",
    comment: str | None = None,
    alt_text: str | None = None,
    keep: Path | None = None,
    scale: float = 2.0,
    destination: Mapping[str, str] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Render ``source`` and upload the PNG to the current Slack thread."""
    _validate_source(source)
    name = Path(filename).name
    if not name.lower().endswith(".png"):
        name = f"{name}.png"
    target = destination or current_slack_destination()
    with tempfile.TemporaryDirectory(prefix="mermaid-post-") as work:
        png = Path(work) / name
        rendered = render(source, png, scale=scale, run=run, env=env)
        upload_output = upload_to_slack(png, target, comment=comment, alt_text=alt_text, run=run)
        if keep is not None:
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(png, keep)
    return {
        "ok": True,
        "bytes": rendered["bytes"],
        "filename": name,
        "channel_id": target["channel_id"],
        "thread_ts": target["thread_ts"],
        "kept": str(keep) if keep is not None else None,
        "upload": upload_output,
    }


class MermaidClient:
    """Catalog entry point for the tool host."""

    def render(self, source: str, output: str, scale: float = 2.0) -> dict[str, Any]:
        """Render Mermaid source to a PNG file at ``output``."""
        return render(source, Path(output), scale=scale)

    def post(
        self, source: str, comment: str | None = None, filename: str = "diagram.png"
    ) -> dict[str, Any]:
        """Render Mermaid source and upload the PNG to the current Slack thread."""
        return post(source, comment=comment, filename=filename)


def _client() -> MermaidClient:
    return MermaidClient()
