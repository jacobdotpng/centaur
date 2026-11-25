"""Small user-facing summaries; full failures remain in durable workflow state."""
from __future__ import annotations

import re


def slack_text(value: str, limit: int = 240) -> str:
    """Bound untrusted text, redact common credentials, and neutralize mentions."""
    value = re.sub(r"\b(?:github_pat_|gh[pousr]_|xox[baprs]-|sk-)[A-Za-z0-9_-]{12,}", "[redacted]", value)
    value = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+|basic\s+)?[^\s,;]+", r"\1[redacted]", value)
    value = re.sub(r"(?i)((?:[\w-]*(?:token|password|secret|api[_-]?key)[\w-]*)\s*[=:]\s*)[\"']?[^\s,;\"']+[\"']?", r"\1[redacted]", value)
    value = re.sub(r"([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", value, flags=re.I)
    value = re.sub(r"(?i)([?&][^=&\s]*(?:signature|credential|token|secret)[^=&\s]*=)[^&\s]+", r"\1[redacted]", value)
    value = " ".join(value.split())
    if len(value) > limit:
        value = value[:limit-1] + "…"
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def error_summary(error: object) -> str:
    value = str(error)
    if "No matching server-verified" in value:
        return "I couldn't verify that the change was applied."
    if re.search(r"HTTP\s*401|Unauthorized|invalid or missing API key", value, re.I):
        return "I couldn't sign in to a required service."
    if re.search(r"HTTP\s*403|Forbidden", value, re.I):
        return "A required service denied access."
    if re.search(r"DNS|resolution error|timed?\s*out|connection refused|Cannot inspect access approvals", value, re.I):
        return "I couldn't reach a required service."
    if re.search(r"<!doctype|<html|<svg|HTTP\s*5\d\d", value, re.I):
        return "A required service returned an error."
    if value.lstrip().startswith(("{", "[")):
        return "A required step returned an error."
    if re.search(r"session\.execution_failed|agent turn exe_|Traceback|\b[A-Za-z]+::[A-Za-z]+", value):
        return "The agent stopped before this step finished."
    if re.search(r"\b(?:aar|prn|exe|prx|grant|aap)_[A-Za-z0-9]+\b|\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", value):
        return "I couldn't finish this step."
    return slack_text(value.strip().splitlines()[0] if value.strip() else "I couldn't finish this step.", 180)
