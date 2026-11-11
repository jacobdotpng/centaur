from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

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


def test_combines_agent_results_and_delivers_mention_in_requested_thread():
    ctx = context()
    result = asyncio.run(handler(Input("U12345678", "C12345678", "123.456"), ctx))
    assert result["output"] == "poop <@U12345678>"
    assert ctx.run_agents.call_args.kwargs == {"max_concurrency": 2}
    ctx.post_to_slack.assert_awaited_once_with(
        "C12345678",
        "poop <@U12345678>",
        thread_ts="123.456",
        mrkdwn=True,
    )
    assert ctx.step.call_args.args[0] == "post_result"


def test_no_delivery_without_channel():
    ctx = context()
    asyncio.run(handler(Input("U12345678"), ctx))
    ctx.post_to_slack.assert_not_awaited()


def test_failed_agent_does_not_post_success():
    ctx = context(failed=True)
    with pytest.raises(RuntimeError, match="agents failed"):
        asyncio.run(handler(Input("U12345678", "C12345678"), ctx))
    ctx.post_to_slack.assert_not_awaited()


@pytest.mark.parametrize("user_id", ["C12345678", "<@U12345678>", "", "bad"])
def test_invalid_user_id_rejected_before_agents(user_id):
    ctx = context()
    with pytest.raises(ValueError, match="Slack user ID"):
        asyncio.run(handler(Input(user_id), ctx))
    ctx.run_agents.assert_not_awaited()
