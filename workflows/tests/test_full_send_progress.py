import asyncio
import unittest

from test_full_send import PR, SHA, context, inputs, shipped
from workflows.full_send import handler


class FullSendProgressTests(unittest.TestCase):
    def test_one_status_message_tracks_real_stages_and_replay_does_not_duplicate(self):
        evidence = 'Detailed audit evidence which belongs in the workflow result. ' * 60
        result = {**shipped(), 'summary': 'The live button works.',
                  'deployment_url': 'https://github.com/example/project/actions/runs/42',
                  'evidence': [evidence, 'https://github.com/example/project/actions/runs/42']}
        ctx = context([{'pr_url': PR}, {'head_sha': SHA, 'checks_green': True}, result])
        first = asyncio.run(handler(inputs(), ctx))
        updates = list(ctx.update_slack.await_args_list)
        self.assertEqual(asyncio.run(handler(inputs(), ctx)), first)
        self.assertEqual(ctx.update_slack.await_args_list, updates)
        self.assertEqual(ctx.post_to_slack.await_count, 2)
        texts = [c.args[2] for c in updates]
        for stage in ('Review', 'CI', 'Deploy', 'Shipped'):
            self.assertTrue(any(stage in text for text in texts), stage)
        for call in updates:
            self.assertEqual(call.args[:2], ('C12345678', '125.456'))
        final = ctx.post_to_slack.await_args_list[-1]
        self.assertEqual(final.kwargs['thread_ts'], '123.456')
        self.assertIn(PR, final.args[1])
        self.assertIn(result['deployment_url'], final.args[1])
        self.assertIn('The live button works.', final.args[1])
        self.assertLess(len(final.args[1]), 1000)
        self.assertNotIn(evidence, final.args[1])
        self.assertEqual(first['evidence'], result['evidence'])
        self.assertTrue(any(PR + '/checks' in text for text in texts))

    def test_ci_blocker_names_stage_and_preserves_known_review_without_claiming_rollback(self):
        ctx = context([{'pr_url': PR}, {'status': 'blocked', 'reason': 'CI cannot read required jobs.',
                                      'next_step': 'Restore Actions read permission for this repository, then recheck the same head.'}])
        with self.assertRaisesRegex(RuntimeError, 'cannot read'):
            asyncio.run(handler(inputs(), ctx))
        text = ctx.update_slack.await_args_list[-1].args[2]
        self.assertIn('CI', text)
        self.assertIn(PR, text)
        self.assertIn('Review passed', text)
        self.assertIn('CI cannot read required jobs', text)
        self.assertIn('Restore Actions read permission', text)
        self.assertNotIn('run-1', text)
        self.assertEqual(ctx.post_to_slack.await_count, 1)
        self.assertNotIn('shipped', text.lower())
        self.assertNotIn('rollback succeeded', text.lower())

    def test_shipping_failure_does_not_claim_nothing_merged_or_verified(self):
        ctx = context([{'pr_url': PR}, {'head_sha': SHA, 'checks_green': True},
                       {'status': 'blocked', 'reason': 'Merge succeeded; live verification unavailable.'}])
        with self.assertRaises(RuntimeError):
            asyncio.run(handler(inputs(), ctx))
        text = ctx.update_slack.await_args_list[-1].args[2]
        self.assertIn('Deploy', text)
        self.assertIn('CI passed', text)
        self.assertIn('Merge succeeded', text)
        self.assertIn('not verified', text)
        self.assertNotIn('nothing merged', text.lower())

    def test_untrusted_failure_text_is_bounded_redacted_and_cannot_ping_channel(self):
        secret = 'ghp_NOT_A_REAL_CREDENTIAL_FOR_TESTS_ONLY'
        reason = '<!channel> Authorization: Bearer ' + secret + '\n' + 'x' * 10000
        ctx = context([{'status': 'blocked', 'reason': reason}])
        with self.assertRaises(RuntimeError):
            asyncio.run(handler(inputs(), ctx))
        text = ctx.update_slack.await_args_list[-1].args[2]
        self.assertNotIn(secret, text)
        self.assertNotIn('<!channel>', text)
        self.assertLess(len(text), 1500)

    def test_service_html_is_not_copied_into_a_workflow_failure_card(self):
        ctx = context([{'status': 'blocked', 'reason': 'HTTP 500 <!doctype html><svg>service debug dump</svg>'}])
        with self.assertRaisesRegex(RuntimeError, 'HTTP 500'):
            asyncio.run(handler(inputs(), ctx))
        text = ctx.update_slack.await_args_list[-1].args[2]
        self.assertNotIn('svg', text)
        self.assertNotIn('service debug dump', text)
        self.assertNotIn('run-1', text)
        self.assertEqual(ctx.post_to_slack.await_count, 1)

    def test_failure_to_update_status_still_posts_one_readable_failure(self):
        ctx = context([{'status': 'blocked', 'reason': 'CI cannot read required jobs.'}])
        ctx.update_slack.side_effect = RuntimeError('Slack edit unavailable')
        with self.assertRaises(RuntimeError):
            asyncio.run(handler(inputs(), ctx))
        self.assertEqual(ctx.post_to_slack.await_count, 2)
        self.assertIn('CI cannot read', ctx.post_to_slack.await_args_list[-1].args[1])

    def test_technical_next_steps_are_kept_out_of_the_primary_card(self):
        ctx = context([{'status': 'blocked', 'reason': 'A service denied access.',
                       'next_step': 'Give role_Internal to prn_Internal for run 00000000-0000-0000-0000-000000000001.'}])
        with self.assertRaises(RuntimeError):
            asyncio.run(handler(inputs(), ctx))
        text = ctx.update_slack.await_args_list[-1].args[2]
        self.assertNotIn('prn_Internal', text)
        self.assertNotIn('00000000-', text)
        self.assertIn('Ask me', text)
