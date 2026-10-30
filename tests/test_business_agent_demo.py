import json
from pathlib import Path
import tempfile
import unittest

from enterprise_ai.business_agent_demo import run_demo
from enterprise_ai.workflow_graph import WorkflowLab
from enterprise_ai.workflow_store import FixtureActor


class DemoTests(unittest.TestCase):
    def test_real_m09_and_persistent_approvals_across_three_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'demo'
            report = run_demo(output)
            self.assertEqual(report['kind'], 'synthetic-scripted-not-real-model')
            self.assertEqual(report['effect_count'], 3)
            self.assertEqual([x['status'] for x in report['cases']], ['executed']*3)
            for case in report['cases']:
                context = json.loads((output/(case['thread_id']+'.json')).read_text(encoding='utf-8'))
                self.assertTrue(context['citations'])
                self.assertTrue(context['workflow']['waiting_for_approval'])
            with self.assertRaises(FileExistsError): run_demo(output)

    def test_wrong_model_parameters_are_rejected_before_any_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'demo'
            def wrong(text):
                return {'case':'quote', 'arguments':{'customer_id':1,'product_id':1,
                                                    'quantity':99,'discount_percent':'5.00'}}
            with self.assertRaises(ValueError):
                run_demo(output, planner=wrong)
            manager = FixtureActor(1,4,4,'manager')
            self.assertEqual(WorkflowLab(output/'lab').store.effect_count(manager), 0)
