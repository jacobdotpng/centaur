import unittest

from workflows.slack_messages import error_summary


class SlackMessageTests(unittest.TestCase):
    def test_service_failures_are_actionable_without_response_bodies(self):
        examples = [
            ('HTTP 401 Unauthorized secret response body', 'sign in'),
            ('HTTP 403 Forbidden secret response body', 'denied access'),
            ('DNS resolution error for private-host', 'reach'),
            ('HTTP 500 <!doctype html><svg>private response</svg>', 'returned an error'),
            ('agent turn exe_Internal ended with session.execution_failed', 'agent stopped'),
        ]
        for error, expected in examples:
            with self.subTest(error=error):
                text = error_summary(error)
                self.assertIn(expected, text)
                self.assertNotIn('secret', text)
                self.assertNotIn('private', text)
                self.assertNotIn('exe_', text)

    def test_json_and_stack_details_remain_private(self):
        for value in ['{"token":"sensitive-test-value","error":"failed"}',
                      'Traceback (most recent call last):\nFile "private.py", line 1',
                      'Request aar_Internal failed in run 00000000-0000-0000-0000-000000000001']:
            text = error_summary(value)
            self.assertLess(len(text), 100)
            self.assertNotIn('sensitive-test-value', text)
            self.assertNotIn('private.py', text)
            self.assertNotIn('aar_', text)

    def test_plain_repair_reason_is_preserved_and_unsafe_mentions_are_neutralized(self):
        self.assertEqual(error_summary('CI cannot read required jobs.'), 'CI cannot read required jobs.')
        self.assertNotIn('<!channel>', error_summary('<!channel> Restore access.'))
        self.assertNotIn('private log line', error_summary('CI is unavailable.\nprivate log line'))
