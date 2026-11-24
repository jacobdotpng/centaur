"""Perform one typed administrative action with durable Slack access resolution."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from api.workflow_engine import WorkflowContext

WORKFLOW_NAME = "admin_action"
WORKFLOW_PRINCIPAL = True
WORKFLOW_STARTERS = {"admin", "console", "principal", "ingress"}
AGENT_DEFAULTS = {
    "principal": "workflow-admin-action", "harness": "codex", "model": "gpt-6.1-sol",
    "reasoning": "low", "release_sandbox_after_turn": True,
}


@dataclass
class Input:
    operation: str
    target: str
    arguments: dict[str, Any] = field(default_factory=dict)
    channel: str = ""
    thread_ts: str = ""


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    # Console owns the closed registry and authorization, including validation of
    # these untrusted arguments. The worker cannot supply an originating identity.
    document = {"operation": inp.operation, "target": inp.target, "arguments": inp.arguments}
    result = await ctx.step("perform_action", lambda: ctx.agent_turn(
        "Perform this single typed Console operation through centaur-console request-action: "
        + json.dumps(document, sort_keys=True)
        + ". Do not change any other setting or seek broader permission. If it returns "
          "ADMIN_APPROVAL_REQUIRED, end this turn immediately; the same durable workflow "
          "will wait and resume after the Slack decision. When resumed, retry that exact "
          "operation to obtain its recorded receipt. Return only its JSON receipt.",
        name="perform_action",
    ))
    if result.get("status") != "completed":
        raise RuntimeError("Administrative agent did not complete; inspect the recorded access request.")
    receipt = result.get("result_text", "")
    if inp.channel:
        await ctx.step("deliver_receipt", lambda: ctx.post_to_slack(
            inp.channel, f"Administrative action completed in the same run `{ctx.run_id}`.\n"
            f"Operation: `{inp.operation}` · Target: `{inp.target}`\n{receipt}",
            thread_ts=inp.thread_ts or None,
        ))
    return {"run_id": ctx.run_id, "task_id": ctx.task_id, "receipt": receipt}
