import asyncio
import unittest
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "workflow-python"))

from workflows.admin_action import Input, handler


class AdminActionWorkflowTest(unittest.TestCase):
    def test_delivers_receipt_with_same_run_and_stable_named_agent(self):
        ctx = AsyncMock()
        ctx.run_id, ctx.task_id = 'run-original', 'task-original'
        ctx.agent_turn.return_value = {'status': 'completed', 'result_text': '{"applied":true}', 'admin_actions': [{
            'id': 'aar_Request', 'status': 'completed', 'operation': 'principal.read', 'target': 'prn_Target', 'arguments': {}, 'receipt': {'applied': True}}]}
        async def step(name, fn):
            return await fn()
        ctx.step.side_effect = step
        result = asyncio.run(handler(Input('principal.read', 'prn_Target', channel='C12345678', thread_ts='1.2'), ctx))
        self.assertEqual(result['run_id'], 'run-original')
        self.assertEqual(ctx.agent_turn.call_args.kwargs['name'], 'perform_action')
        self.assertIn('ADMIN_APPROVAL_REQUIRED', ctx.agent_turn.call_args.args[0])
        self.assertIn('run-original', ctx.post_to_slack.call_args.args[1])

    def test_failed_agent_never_posts_success(self):
        ctx = AsyncMock()
        ctx.agent_turn.return_value = {'status': 'failed'}
        async def step(name, fn):
            return await fn()
        ctx.step.side_effect = step
        with self.assertRaisesRegex(RuntimeError, 'No matching'):
            asyncio.run(handler(Input('principal.read', 'prn_Target', channel='C12345678'), ctx))
        self.assertIn('stopped', ctx.post_to_slack.call_args.args[1])
