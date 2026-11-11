"""Two independent agents, with optional delivery of their combined output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from api.workflow_engine import WorkflowContext

WORKFLOW_NAME = "concurrent_agent_smoke"
WORKFLOW_PRINCIPAL = True
WORKFLOW_STARTERS = {"admin", "console", "principal", "ingress"}
AGENT_PRINCIPAL = "workflow-concurrent-agent-smoke"


@dataclass
class Input:
    slack_user_id: str
    channel: str = ""
    thread_ts: str = ""


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    if not re.fullmatch(r"[UW][A-Z0-9]{8,}", inp.slack_user_id):
        raise ValueError("slack_user_id must be a Slack user ID")
    if inp.channel and not re.fullmatch(r"[CG][A-Z0-9]{8,}", inp.channel):
        raise ValueError("channel must be a Slack channel ID")
    if inp.thread_ts and not inp.channel:
        raise ValueError("thread_ts requires channel")

    batch = await ctx.run_agents(
        [
            {"name": "add_poop", "text": "Reply with exactly: poop", "principal": AGENT_PRINCIPAL},
            {
                "name": "tag_user",
                "principal": AGENT_PRINCIPAL,
                "text": f"Reply with exactly this Slack mention: <@{inp.slack_user_id}>",
            },
        ],
        max_concurrency=2,
    )
    if batch["failed"]:
        raise RuntimeError(f"Concurrent smoke agents failed: {batch['results']}")
    output = " ".join(
        item["result"]["result_text"].strip() for item in batch["results"]
    )
    if output != f"poop <@{inp.slack_user_id}>":
        raise RuntimeError("Concurrent smoke agents returned unexpected output")
    delivery = None
    if inp.channel:
        delivery = await ctx.step(
            "post_result",
            lambda: ctx.post_to_slack(
                inp.channel,
                output,
                thread_ts=inp.thread_ts or None,
                mrkdwn=True,
            ),
        )
    return {"output": output, "agents": batch, "delivery": delivery}
