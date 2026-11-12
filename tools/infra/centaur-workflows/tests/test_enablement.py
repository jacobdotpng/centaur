import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('enablement', Path(__file__).parents[1] / 'enablement.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
PR = 'https://github.com/jacobdotpng/centaur/pull/123'


class EnablementTests(unittest.TestCase):
    def test_plan_preserves_existing_grants_and_triggers_without_mutation(self):
        manifest = {'schema': 1, 'workflows': [
            {'name': 'existing', 'enabled': True, 'githubAccess': True,
             'slackTriggers': [{'id': 'keep', 'channel': 'C12345678', 'phrase': 'keep'}]}]}
        before = copy.deepcopy(manifest)
        plan = module.creation_plan(manifest, 'new_workflow', PR, 'Example User', modelAccess=True)
        self.assertEqual(manifest, before)
        self.assertEqual(plan['manifest']['workflows'][0], manifest['workflows'][0])
        self.assertEqual(plan['manifest']['workflows'][1],
                         {'name': 'new_workflow', 'enabled': True, 'modelAccess': True})
        self.assertIn(f'Enablement depends on {PR} deploying first.', plan['config_pr_body'])
        self.assertIn('across all admitted channels', plan['config_pr_body'])
        self.assertIn('Prompted by: Example User\n', plan['config_pr_body'])
        self.assertTrue(any('both PR URLs in the originating Slack thread' in s for s in plan['next_steps']))

    def test_duplicate_or_invalid_inputs_cannot_silently_change_access(self):
        manifest = {'schema': 1, 'workflows': [{'name': 'existing', 'enabled': False}]}
        for name, pr, requester, access in [
            ('existing', PR, 'User', {}), ('Bad Name', PR, 'User', {}),
            ('new', PR.replace('/centaur/', '/other/'), 'User', {}),
            ('new', PR, 'User\ninjected', {}), ('new', PR, 'User', {'githubAccess': 'yes'}),
            ('new', PR, 'User', {'adminAccess': True}),
        ]:
            with self.assertRaises(ValueError):
                module.creation_plan(manifest, name, pr, requester, **access)
