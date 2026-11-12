"""Prepare a reviewed activation change; never mutate GitHub or live grants."""
import copy
import re

IMPLEMENTATION_REPO = "jacobdotpng/centaur"
CONFIG_REPO = "jacobdotpng/centaur-config"


def creation_plan(manifest, name, implementation_pr, requester, **access):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
        raise ValueError("Invalid workflow name")
    if not re.fullmatch(r"https://github.com/" + IMPLEMENTATION_REPO + r"/pull/[1-9]\d*", implementation_pr):
        raise ValueError("Expected implementation PR in " + IMPLEMENTATION_REPO)
    if not requester.strip() or "\n" in requester or "\r" in requester:
        raise ValueError("Expected one attribution identity")
    if not isinstance(manifest, dict) or manifest.get("schema") != 1 or not isinstance(manifest.get("workflows"), list):
        raise ValueError("Expected workflow manifest schema 1")
    if any(w.get("name") == name for w in manifest["workflows"]):
        raise ValueError("Workflow already exists; inspect the existing activation instead")
    allowed = {"modelAccess", "githubAccess", "workflowAccess", "diagnosticsAccess"}
    if set(access) - allowed or any(type(value) is not bool for value in access.values()):
        raise ValueError("Expected explicit supported access booleans")
    entry = {"name": name, "enabled": True, **{k: v for k, v in access.items() if v}}
    updated = copy.deepcopy(manifest)
    updated["workflows"].append(entry)
    body = (
        f"Enable `{name}` across all admitted channels through the global workflow allowlist.\n\n"
        f"Implementation PR: {implementation_pr}\n\n"
        f"Enablement depends on {implementation_pr} deploying first. "
        "Do not merge this configuration PR until that implementation is verified on the live deployment.\n\n"
        "Use the existing managed channel access and a dedicated workflow principal; "
        "grant only the selected capabilities. Do not add channel triggers or broaden channel permissions.\n\n"
        f"Prompted by: {requester}\n"
    )
    return {"config_repository": CONFIG_REPO, "manifest_path": "deployment/workflows.json",
            "manifest": updated, "config_pr_body": body,
            "implementation_pr": implementation_pr,
            "next_steps": [
                "Validate with the configuration repository's workflow-settings and configuration checks.",
                "Search for an existing companion PR before creating one; retain its branch on retry.",
                "Open the companion PR with config_pr_body and link both PR URLs in the originating Slack thread.",
                "Verify implementation deployment before merging enablement; review exact heads and pass CI for both PRs.",
                "Verify the live catalog, dedicated principal binding and access from admitted channels.",
            ]}
