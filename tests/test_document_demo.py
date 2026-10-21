import json
import tempfile
import unittest
from pathlib import Path

from enterprise_ai.document_demo import run_demo


class DemoTests(unittest.TestCase):
    def test_full_incremental_experiment(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'demo'
            summary = run_demo(out)
            self.assertEqual(summary['kind'],'synthetic-fixture-not-production')
            self.assertEqual(summary['baseline_documents'],30)
            self.assertEqual(summary['history_versions'],32)
            self.assertEqual(summary['checks_passed'],7)
            self.assertEqual(summary,json.loads((out/'summary.json').read_text(encoding='utf-8')))
            self.assertEqual(json.loads((out/'05-bad-hash-report.json').read_text())['status'],'failed')
            with self.assertRaises(FileExistsError):
                run_demo(out)


if __name__ == '__main__':
    unittest.main()
