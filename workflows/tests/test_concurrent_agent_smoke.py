from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import unittest

from workflows.concurrent_agent_smoke import Input, handler


def context(*, failed=False):
    ctx = AsyncMock()
    ctx.run_agents.return_value = {
        "failed": int(failed),
        "succeeded": 1 if failed else 2,
        "results": [
            {"name": "add_poop", "ok": True, "result": {"result_text": "poop"}},
            {
                "name": "tag_user",
                "ok": not failed,
                "result": {"result_text": "<@U12345678>"},
            },
        ],
    }

    async def step(name, fn):
        return await fn()

    ctx.step.side_effect = step
    return ctx


class ConcurrentWorkflowTests(unittest.TestCase):
    def test_combines_agent_results_and_delivers_mention_in_requested_thread(self):
        ctx = context()
        result = asyncio.run(handler(Input("U12345678", "C12345678", "123.456"), ctx))
        assert result["output"] == "poop <@U12345678>"
        assert ctx.run_agents.call_args.kwargs == {"max_concurrency": 2}
        assert all(agent["principal"] == "workflow-concurrent-agent-smoke" for agent in ctx.run_agents.call_args.args[0])
        ctx.post_to_slack.assert_awaited_once_with(
            "C12345678",
            "poop <@U12345678>",
            thread_ts="123.456",
            mrkdwn=True,
        )
        assert ctx.step.call_args.args[0] == "post_result"


    def test_no_delivery_without_channel(self):
        ctx = context()
        asyncio.run(handler(Input("U12345678"), ctx))
        ctx.post_to_slack.assert_not_awaited()


    def test_failed_agent_does_not_post_success(self):
        ctx = context(failed=True)
        with self.assertRaisesRegex(RuntimeError, "agents failed"):
            asyncio.run(handler(Input("U12345678", "C12345678"), ctx))
        ctx.post_to_slack.assert_not_awaited()


    def test_invalid_user_id_rejected_before_agents(self):
        for user_id in ["C12345678", "<@U12345678>", "", "bad"]:
            ctx = context()
            with self.assertRaisesRegex(ValueError, "Slack user ID"):
                asyncio.run(handler(Input(user_id), ctx))
            ctx.run_agents.assert_not_awaited()
