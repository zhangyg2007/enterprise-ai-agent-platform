import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from enterprise_ai.agent import OllamaPlanner
from enterprise_ai.ollama import OllamaClient
from enterprise_ai.agent_cli import run_fixture
from tests.test_ollama import Boundary, Response, reply, MODEL


class CliTests(unittest.TestCase):
    def test_fixture_stream_retry_failures_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for scenario,expected in [('normal','ok'),('retry','ok'),('permanent','failed'),('invalid-plan','rejected'),('budget','budget')]:
                r=run_fixture(root/scenario,scenario=scenario)
                self.assertEqual(r['state']['status'],expected)
                self.assertEqual(r['kind'],'synthetic-fixture-not-live-agent')
                self.assertTrue((root/scenario/'events.json').exists())
            normal=json.loads((root/'normal'/'state.json').read_text(encoding='utf-8'))
            self.assertTrue(normal['result']['answer']['citations'])
            with self.assertRaises(FileExistsError): run_fixture(root/'normal')

    def test_actual_ollama_serialization_with_only_network_replaced(self):
        boundary=Boundary(Response(reply(message={'role':'assistant','content':'{"tool":"customer","arguments":{"customer_id":1}}'})))
        with patch('enterprise_ai.ollama.http.client.HTTPConnection',boundary.connect):
            result=OllamaPlanner(OllamaClient(),MODEL)('客户1')
        self.assertEqual(result,{'tool':'customer','arguments':{'customer_id':1}})
        request=json.loads(boundary.requests[0][2])
        self.assertEqual(request['stream'],False); self.assertEqual(request['think'],False)
        self.assertEqual(request['options']['num_predict'],192)


if __name__=='__main__': unittest.main()
