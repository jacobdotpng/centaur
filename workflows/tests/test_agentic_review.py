from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "services" / "workflow-python"),
)

from workflows.agentic_review import Input, handler

PR = "https://github.com/jacobdotpng/centaur/pull/12"
SHA = "a" * 40
NEW = "b" * 40


def result(value):
    return {"status": "completed", "result_text": json.dumps(value)}


def context(values):
    ctx = AsyncMock()
    ctx.run_id = "run-123"
    ctx.agent_turn.side_effect = [result(v) for v in values]
    cache = {}

    async def step(name, fn):
        if name not in cache:
            cache[name] = await fn()
        return cache[name]

    ctx.step.side_effect = step
    return ctx


CLEAN = {"head_repository": "jacobdotpng/centaur", "head_sha": SHA, "findings": []}
ISSUE = {"head_repository": "jacobdotpng/centaur", "head_sha": SHA, "findings": ["app.py:12 — redundant branch obscures logic"]}
FIX = {"head_sha": NEW, "summary": "Removed branch; unit checks passed"}
COMMENT = {"comment_url": PR + "#issuecomment-123"}


class AgenticReviewTests(unittest.TestCase):
    def test_clean_stops_and_replay_reuses_all_side_effects(self):
        ctx = context([CLEAN, COMMENT])
        first = asyncio.run(handler(Input(PR), ctx))
        second = asyncio.run(handler(Input(PR), ctx))
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "clean")
        self.assertEqual(ctx.agent_turn.await_count, 2)
        args = ctx.agent_turn.call_args_list[0].kwargs
        self.assertEqual(args["model"], "gpt-6.1-sol")
        self.assertEqual(args["reasoning"], "medium")
        self.assertEqual(args["principal"], "workflow-agentic-review")

    def test_three_repairs_then_final_review_stops_even_with_findings(self):
        ctx = context([ISSUE, FIX, ISSUE, FIX, ISSUE, FIX, ISSUE, COMMENT])
        out = asyncio.run(handler(Input(PR), ctx))
        self.assertEqual(out["repairs"], 3)
        self.assertEqual(out["status"], "unresolved")
        self.assertEqual(len(out["rounds"]), 4)
        self.assertEqual(ctx.agent_turn.await_count, 8)
        keys = [c.kwargs["thread_key"] for c in ctx.agent_turn.call_args_list]
        self.assertEqual(len(set(keys)), 8)

    def test_reviewer_configuration_and_early_convergence(self):
        ctx = context([ISSUE, FIX, {"head_sha": NEW, "findings": []}, COMMENT])
        out = asyncio.run(handler(Input(PR, reviewer_model="custom", reviewer_reasoning="high"), ctx))
        self.assertEqual(out["repairs"], 1)
        self.assertEqual(out["status"], "clean")
        self.assertEqual(ctx.agent_turn.call_args_list[0].kwargs["model"], "custom")
        self.assertEqual(ctx.agent_turn.call_args_list[1].kwargs["model"], "gpt-6.1-sol")

    def test_declined_findings_do_not_loop(self):
        ctx = context([ISSUE, {"head_sha": SHA, "summary": "Declined subjective finding"}, COMMENT])
        out = asyncio.run(handler(Input(PR), ctx))
        self.assertEqual(out["status"], "unresolved")
        self.assertEqual(out["repairs"], 1)

    def test_all_allowed_repositories(self):
        for repo in ("centaur", "centaur-config", "agent-platform"):
            pr = f"https://github.com/jacobdotpng/{repo}/pull/12"
            ctx = context([CLEAN, {"comment_url": pr + "#issuecomment-123"}])
            self.assertEqual(asyncio.run(handler(Input(pr), ctx))["status"], "clean")
            self.assertIn(f"--repo jacobdotpng/{repo}", ctx.agent_turn.call_args_list[0].args[0])

    def test_unrelated_public_repository_rejected_before_side_effects(self):
        ctx = context([])
        with self.assertRaises(ValueError):
            asyncio.run(handler(Input("https://github.com/python/cpython/pull/12"), ctx))
        ctx.agent_turn.assert_not_awaited()
        ctx.step.assert_not_awaited()
        ctx.call_tool.assert_not_awaited()

    def test_out_of_list_fork_cannot_start_repair(self):
        for head in ("outsider/centaur", None, ""):
            ctx = context([{**ISSUE, "head_repository": head}])
            with self.assertRaises(ValueError):
                asyncio.run(handler(Input(PR), ctx))
            self.assertEqual(ctx.agent_turn.await_count, 1)

    def test_allowed_fork_can_start_repair(self):
        ctx = context([{**ISSUE, "head_repository": "jacobdotpng/agent-platform"}, FIX, CLEAN, COMMENT])
        self.assertEqual(asyncio.run(handler(Input(PR), ctx))["repairs"], 1)
        prompt = ctx.agent_turn.call_args_list[1].args[0]
        self.assertIn("Never push to an", prompt)
        self.assertIn("git push remote", prompt)

    def test_invalid_inputs_do_not_start_agents(self):
        for inp in [Input("bad"), Input(PR, 4), Input(PR, 0), Input(PR, True)]:
            ctx = context([])
            with self.assertRaises(ValueError):
                asyncio.run(handler(inp, ctx))
            ctx.agent_turn.assert_not_awaited()

    def test_failed_or_malformed_review_does_not_start_repair_or_publish(self):
        for bad in [{"status": "failed"}, {"status": "completed", "result_text": "bad"}, result({"head_sha": SHA, "findings": "clean"})]:
            ctx = context([])
            ctx.agent_turn.side_effect = [bad]
            with self.assertRaises(RuntimeError):
                asyncio.run(handler(Input(PR), ctx))
            self.assertEqual(ctx.agent_turn.await_count, 1)
