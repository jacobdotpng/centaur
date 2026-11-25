"""Perform one typed administrative action with durable Slack access resolution."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from api.workflow_engine import WorkflowContext
from workflows.slack_messages import error_summary

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
    if not (re.fullmatch(r"prn_[A-Za-z0-9]+", inp.target) or
            inp.operation == f"catalog.{inp.target}" and inp.target in {"principals", "roles", "credentials"} or
            inp.operation == "catalog.role" and inp.target == "roles"):
        raise ValueError("Resolve the exact principal OID through authorized tools before starting admin_action")
    document = {"operation": inp.operation, "target": inp.target, "arguments": inp.arguments}
    try:
        result = await ctx.step("perform_action", lambda: ctx.agent_turn(
            "Perform this single typed Console operation through centaur-console request-action: "
            + json.dumps(document, sort_keys=True)
            + ". Do not change any other setting or seek broader permission. If it returns "
              "ADMIN_APPROVAL_REQUIRED, end this turn immediately; the same durable workflow "
              "will wait and resume after the Slack decision. When resumed, retry that exact "
              "operation to obtain its recorded receipt. Return only its JSON receipt.",
            name="perform_action",
        ))
        # These receipts are attached by api-rs after reading Console, not parsed
        # from the model's answer. A completed model turn is not proof of an action.
        actions = result.get("admin_actions", [])
        matching = [action for action in actions if action.get("status") == "completed"
                    and action.get("operation") == inp.operation and action.get("target") == inp.target
                    and action.get("arguments") == inp.arguments]
        unexpected = [action for action in actions if action not in matching and action.get("approval_scope") != "origin_read"]
        if result.get("status") != "completed" or len(matching) != 1 or unexpected:
            raise RuntimeError("No matching server-verified action receipt; success is not established.")
        receipt = matching[0]
    except Exception as exc:
        if inp.channel:
            await ctx.step("deliver_blocked", lambda: ctx.post_to_slack(
                inp.channel, f"{error_summary(exc)}\nAsk me to check this request before retrying.",
                thread_ts=inp.thread_ts or None,
            ))
        raise
    if inp.channel:
        message = receipt.get("slack_message") or {}
        completion = receipt.get("presentation", {}).get("completion")
        if not isinstance(completion, str) or not completion or len(completion) > 1000:
            completion = "The requested action is complete."
        text = f"*Done*\n{completion}"
        if message.get("ts"):
            if message.get("channel") != inp.channel or not isinstance(message["ts"], str) or not re.fullmatch(r"\d+\.\d+", message["ts"]):
                raise RuntimeError("The approval card does not belong to this request's channel")
            blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}},
                      {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Details"},
                        "action_id": f"centaur.admin-action:{receipt['id']}:details", "value": receipt["id"]}]}]
            await ctx.step("deliver_receipt", lambda: ctx.update_slack(
                inp.channel, message["ts"], text, blocks=blocks,
            ))
        else:
            # Metadata reads and records from older releases may have no card.
            await ctx.step("deliver_receipt", lambda: ctx.post_to_slack(
                inp.channel, text, thread_ts=inp.thread_ts or None,
            ))
    return {"run_id": ctx.run_id, "task_id": ctx.task_id, "receipt": receipt}
