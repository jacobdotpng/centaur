import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("workflow_client", Path(__file__).parents[1] / "client.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WorkflowClientTests(unittest.TestCase):
    def test_catalog_and_history_are_distinct(self):
        client = module.WorkflowClient("http://example.test")
        client.request = Mock(return_value={})
        client.catalog()
        client.request.assert_called_once_with("GET", "/api/workflows")
        client.request.reset_mock()
        client.runs("name & other")
        client.request.assert_called_once_with("GET", "/api/workflows/runs?limit=20&workflow_name=name+%26+other")

    def test_wait_requires_durable_completion(self):
        client = module.WorkflowClient()
        client.run = Mock(side_effect=[{"status": "running"}, {"status": "completed", "result": {"output": "yes"}}])
        sleep = Mock()
        result = client.wait("run-1", sleep=sleep, clock=lambda: 0)
        self.assertEqual(result["status"], "completed")
        sleep.assert_called_once_with(2)

    def test_wait_returns_failure_instead_of_success(self):
        client = module.WorkflowClient()
        client.run = Mock(return_value={"status": "failed", "failure": "child agent failed"})
        self.assertEqual(client.wait("run-1")["status"], "failed")

    def test_wait_is_bounded(self):
        client = module.WorkflowClient()
        client.run = Mock(return_value={"status": "running"})
        with self.assertRaises(TimeoutError):
            client.wait("run-1", timeout=1, clock=Mock(side_effect=[0, 1]))
