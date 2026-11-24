import importlib.util
import pathlib
import unittest

import httpx

spec = importlib.util.spec_from_file_location('console_client', pathlib.Path(__file__).with_name('client.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AdminActionClientTest(unittest.TestCase):
    def test_pending_access_ends_turn_with_existing_menu_identifier(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(403, json={'error': {
            'code': 'admin_approval_required', 'resolution': {'id': 'aar_Test', 'status': 'waiting_for_access'}}}))
        with module.ConsoleClient(transport=transport) as client:
            with self.assertRaisesRegex(RuntimeError, 'ADMIN_APPROVAL_REQUIRED aar_Test'):
                client._admin_request('/api/v1/sandbox/admin/principals/prn_Test/default_skill', method='PATCH', json={'data': {'skill': 'full-send'}})

    def test_approved_operation_returns_the_recorded_readback_without_blanket_permission(self):
        calls = []
        def reply(request):
            calls.append(request)
            return httpx.Response(403, json={'error': {'code': 'admin_approval_required', 'resolution': {
                'status': 'completed', 'receipt': {'id': 'prn_Test', 'labels': {'slack_default_skill': 'full-send'}}}}})
        with module.ConsoleClient(transport=httpx.MockTransport(reply)) as client:
            result = client._admin_request('/api/v1/sandbox/admin/principals/prn_Test/default_skill', method='PATCH', json={'data': {'skill': 'full-send'}})
        self.assertEqual(result['data']['labels']['slack_default_skill'], 'full-send')
        self.assertEqual(len(calls), 1)

    def test_generic_provider_denial_never_becomes_an_approval(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(403, json={'error': {'message': 'denied'}}))
        with module.ConsoleClient(transport=transport) as client:
            with self.assertRaisesRegex(RuntimeError, 'admin request failed'):
                client._admin_request('/api/v1/sandbox/admin/principals')
