from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "workflow-python"))

from workflows.full_send import Input, handler

PR = "https://github.com/example/project/pull/12"
SHA = "a" * 40
NEW = "b" * 40


def inputs(**changes):
    return Input(**{
        "repository": "example/project", "scope": "Add a button; clicking it works",
        "deployment": "Staging via the repository release pipeline; click the live button",
        "channel": "C12345678", "thread_ts": "123.456", "requester": "Example User",
        "ship_it": "Ship it", "confirmation_ts": "124.456", **changes,
    })


def review(sha=SHA, status="clean"):
    return {"run_id": "child-1", "result": {"status": status, "pr_url": PR,
            "rounds": [{"review": {"head_sha": sha, "findings": []}}]}}


def shipped(sha=SHA):
    return {"status": "shipped", "reviewed_sha": sha, "merge_sha": "c" * 40,
            "deployment_revision": "revision-123", "evidence": ["https://ci.example/run/1", "Live button clicked"]}


def context(agents, reviews=None):
    ctx = AsyncMock()
    ctx.run_id, ctx.task_id = "run-1", "task-1"
    ctx.agent_turn.side_effect = [{"status": "completed", "result_text": json.dumps(v)} for v in agents]
    children = iter(reviews or [review()])
    cache = {}

    async def step(name, fn):
        if name not in cache:
            cache[name] = await fn()
        return cache[name]

    async def child(name, workflow, inp):
        if name not in cache:
            cache[name] = next(children)
        return cache[name]

    ctx.step.side_effect = step
    ctx.run_workflow.side_effect = child
    return ctx


class FullSendTests(unittest.TestCase):
    def test_ship_it_required_before_any_side_effect(self):
        for changes in [{"ship_it": "go ahead"}, {"scope": ""}, {"deployment": ""},
                        {"confirmation_ts": ""}, {"channel": "U12345678"}]:
            ctx = context([])
            with self.assertRaises(ValueError):
                asyncio.run(handler(inputs(**changes), ctx))
            ctx.step.assert_not_awaited()
            ctx.run_workflow.assert_not_awaited()

    def test_diagnostics_only_checks_access_without_shipping(self):
        verified = {"status": "verified", "deployment_revision": SHA,
                    "deployment": True, "pipelines": True, "health": True, "logs": True}
        ctx = context([verified])
        self.assertEqual(asyncio.run(handler(inputs(diagnostics_only=True), ctx)), verified)
        self.assertEqual(ctx.agent_turn.await_count, 1)
        for path in ("/v1/deployment", "/v1/pipelines?repo=source", "/v1/pipelines?repo=config"):
            self.assertIn(path, ctx.agent_turn.call_args.args[0])
        ctx.post_to_slack.assert_not_awaited()
        ctx.run_workflow.assert_not_awaited()

    def test_operator_diagnostics_url_is_given_to_all_agent_turns(self):
        ctx = context([{"pr_url": PR}, {"head_sha": SHA, "checks_green": True}, shipped()])
        endpoint = "http://diagnostics.example:19876"
        with patch.dict("os.environ", {"CENTAUR_DIAGNOSTICS_URL": endpoint}):
            asyncio.run(handler(inputs(), ctx))
        for call in ctx.agent_turn.await_args_list:
            self.assertIn(f"diagnostics API at {endpoint}", call.args[0])
            for path in ("/v1/deployment", "/v1/pipelines?repo=source", "/v1/pipelines?repo=config"):
                self.assertIn(path, call.args[0])

    def test_diagnostics_only_rejects_missing_live_evidence(self):
        ctx = context([{"status": "verified", "deployment_revision": SHA}])
        with self.assertRaisesRegex(RuntimeError, "diagnostics"):
            asyncio.run(handler(inputs(diagnostics_only=True), ctx))
        ctx.post_to_slack.assert_not_awaited()
        ctx.run_workflow.assert_not_awaited()

    def test_real_named_child_result_and_replay(self):
        ctx = context([{"pr_url": PR}, {"head_sha": SHA, "checks_green": True}, shipped()])
        first = asyncio.run(handler(inputs(), ctx))
        second = asyncio.run(handler(inputs(), ctx))
        self.assertEqual(first, second)
        self.assertEqual(first["review_run_id"], "child-1")
        self.assertEqual(ctx.agent_turn.await_count, 3)
        self.assertEqual(ctx.post_to_slack.await_count, 3)
        self.assertEqual(ctx.run_workflow.call_args.args, ("review_0", "agentic_review", {"pr_url": PR}))
        prompt = ctx.agent_turn.call_args_list[-1].args[0]
        self.assertIn(f"--match-head-commit {SHA}", prompt)

    def test_ci_repairs_require_fresh_review(self):
        ctx = context([{"pr_url": PR}, {"head_sha": NEW, "checks_green": True},
                       {"head_sha": NEW, "checks_green": True}, shipped(NEW)],
                      [review(), review(NEW)])
        out = asyncio.run(handler(inputs(), ctx))
        self.assertEqual(out["reviewed_sha"], NEW)
        self.assertEqual(ctx.run_workflow.await_count, 2)

    def test_head_changes_before_merge_require_fresh_review(self):
        ctx = context([{"pr_url": PR}, {"head_sha": SHA, "checks_green": True},
                       {"status": "head_changed"}, {"head_sha": NEW, "checks_green": True},
                       shipped(NEW)], [review(), review(NEW)])
        self.assertEqual(asyncio.run(handler(inputs(), ctx))["reviewed_sha"], NEW)
        self.assertEqual(ctx.run_workflow.await_count, 2)

    def test_unresolved_wrong_pr_or_malformed_review_never_reaches_ci_or_merge(self):
        for child in [review(status="unresolved"), {"run_id": "bad", "result": {}},
                      {"run_id": "bad", "result": {"status": "clean", "pr_url": PR, "rounds": []}},
                      {"run_id": "bad", "result": {**review()["result"], "pr_url": PR + "3"}}]:
            ctx = context([{"pr_url": PR}], [child])
            with self.assertRaises(RuntimeError):
                asyncio.run(handler(inputs(), ctx))
            self.assertEqual(ctx.agent_turn.await_count, 1)

    def test_child_failure_never_merges(self):
        ctx = context([{"pr_url": PR}])
        ctx.run_workflow.side_effect = RuntimeError("child failed")
        with self.assertRaisesRegex(RuntimeError, "child failed"):
            asyncio.run(handler(inputs(), ctx))
        self.assertEqual(ctx.agent_turn.await_count, 1)

    def test_failed_checks_or_unverified_deployment_cannot_report_shipped(self):
        for agents in [
            [{"pr_url": PR}, {"head_sha": SHA, "checks_green": False}],
            [{"pr_url": PR}, {"head_sha": SHA, "checks_green": True}, {"status": "shipped"}],
            [{"pr_url": PR}, {"head_sha": SHA, "checks_green": True}, {"status": "blocked", "reason": "No deployment access"}],
        ]:
            ctx = context(agents)
            with self.assertRaises(RuntimeError):
                asyncio.run(handler(inputs(), ctx))
            self.assertFalse(any("Full send shipped:" in c.args[1] for c in ctx.post_to_slack.call_args_list))

    def test_three_head_changes_stop(self):
        ctx = context([{"pr_url": PR}] + [{"head_sha": NEW, "checks_green": True}] * 3,
                      [review()] * 3)
        with self.assertRaisesRegex(RuntimeError, "three times"):
            asyncio.run(handler(inputs(), ctx))
        self.assertEqual(ctx.agent_turn.await_count, 4)

    def test_wrong_repository_pr_is_rejected(self):
        ctx = context([{"pr_url": "https://github.com/other/repo/pull/1"}])
        with self.assertRaises(RuntimeError):
            asyncio.run(handler(inputs(), ctx))
        ctx.run_workflow.assert_not_awaited()
