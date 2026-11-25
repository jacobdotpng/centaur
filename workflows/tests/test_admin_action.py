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
        self.assertNotIn('run-original', ctx.post_to_slack.call_args.args[1])
        self.assertNotIn('aar_Request', ctx.post_to_slack.call_args.args[1])

    def test_approved_action_updates_its_existing_card_instead_of_posting_a_json_receipt(self):
        ctx = AsyncMock()
        ctx.run_id, ctx.task_id = 'run-original', 'task-original'
        receipt = {'id': 'aar_Request', 'status': 'completed', 'operation': 'principal.sandbox_access',
                   'target': 'prn_Target', 'arguments': {'sandbox_observability_enabled': False},
                   'approval_scope': 'once', 'receipt': {'sandbox_observability_enabled': False},
                   'presentation': {'completion': 'Updated access for Review agent.'},
                   'slack_message': {'channel': 'C12345678', 'ts': '2.3'}}
        ctx.agent_turn.return_value = {'status': 'completed', 'admin_actions': [receipt]}
        async def step(name, fn):
            return await fn()
        ctx.step.side_effect = step
        result = asyncio.run(handler(Input('principal.sandbox_access', 'prn_Target',
            {'sandbox_observability_enabled': False}, channel='C12345678', thread_ts='1.2'), ctx))
        ctx.post_to_slack.assert_not_awaited()
        self.assertEqual(ctx.update_slack.call_args.args[:2], ('C12345678', '2.3'))
        self.assertIn('Updated access for Review agent.', ctx.update_slack.call_args.args[2])
        self.assertNotIn('sandbox_observability_enabled', ctx.update_slack.call_args.args[2])
        self.assertEqual(result['receipt'], receipt)

    def test_service_errors_keep_html_and_internal_identifiers_out_of_the_reply(self):
        ctx = AsyncMock()
        ctx.run_id, ctx.task_id = 'run-original', 'task-original'
        ctx.agent_turn.side_effect = RuntimeError('HTTP 500 <!doctype html><svg>raw service output</svg>')
        async def step(name, fn):
            return await fn()
        ctx.step.side_effect = step
        with self.assertRaisesRegex(RuntimeError, 'HTTP 500'):
            asyncio.run(handler(Input('principal.read', 'prn_Target', channel='C12345678'), ctx))
        text = ctx.post_to_slack.call_args.args[1]
        self.assertNotIn('svg', text)
        self.assertNotIn('run-original', text)
        self.assertLess(len(text), 250)

    def test_a_card_for_another_channel_is_never_edited(self):
        ctx = AsyncMock()
        ctx.run_id, ctx.task_id = 'run-original', 'task-original'
        ctx.agent_turn.return_value = {'status': 'completed', 'admin_actions': [{
            'id': 'aar_Request', 'status': 'completed', 'operation': 'principal.read',
            'target': 'prn_Target', 'arguments': {}, 'slack_message': {'channel': 'COTHER001', 'ts': '2.3'}}]}
        async def step(name, fn):
            return await fn()
        ctx.step.side_effect = step
        with self.assertRaisesRegex(RuntimeError, 'does not belong'):
            asyncio.run(handler(Input('principal.read', 'prn_Target', channel='C12345678'), ctx))
        ctx.update_slack.assert_not_awaited()
        ctx.post_to_slack.assert_not_awaited()

    def test_failed_agent_never_posts_success(self):
        ctx = AsyncMock()
        ctx.agent_turn.return_value = {'status': 'failed'}
        async def step(name, fn):
            return await fn()
        ctx.step.side_effect = step
        with self.assertRaisesRegex(RuntimeError, 'No matching'):
            asyncio.run(handler(Input('principal.read', 'prn_Target', channel='C12345678'), ctx))
        self.assertIn("couldn't verify", ctx.post_to_slack.call_args.args[1])
