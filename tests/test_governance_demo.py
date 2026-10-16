import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from enterprise_ai.data import generate_dataset
from enterprise_ai.governance_demo import create_demo, verify_demo


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.seed = self.root / 'tiny.json'
        self.seed.write_text(json.dumps(generate_dataset()), encoding='utf-8')

    def test_create_counts_and_no_overwrite(self):
        out = self.root / 'inputs'
        create_demo(self.seed, out)
        before = (out / '01-initial.jsonl').read_bytes()
        self.assertEqual(len(before.splitlines()), 44)
        self.assertEqual(len((out / '02-changes.jsonl').read_bytes().splitlines()), 7)
        with self.assertRaises(FileExistsError):
            create_demo(self.seed, out)
        self.assertEqual((out / '01-initial.jsonl').read_bytes(), before)

    def test_standard_profile_rejected(self):
        self.seed.write_text(json.dumps(generate_dataset(profile='standard')), encoding='utf-8')
        with self.assertRaises(ValueError):
            create_demo(self.seed, self.root / 'inputs')
        self.assertFalse((self.root / 'inputs').exists())

    def test_verify_replays_and_leaves_no_workspace(self):
        before = set(self.root.iterdir())
        result = verify_demo(self.seed, temp_parent=self.root)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['active_customers'], 39)
        self.assertEqual(result['summary'], [{'tenant_id': 1, 'active_customers': 19},
                                              {'tenant_id': 2, 'active_customers': 20}])
        self.assertEqual(set(self.root.iterdir()), before)

    def test_documented_cli_round_trip_and_error_exit(self):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'),
                   PYTHONIOENCODING='utf-8')
        def run(module, *arguments):
            return subprocess.run([sys.executable, '-B', '-m', module, *map(str, arguments)],
                                  env=env, capture_output=True, encoding='utf-8', timeout=30)
        inputs = self.root / 'input with spaces'
        result = run('enterprise_ai.governance_demo', 'create', '--seed', self.seed, '--output', inputs)
        self.assertEqual(result.returncode, 0, result.stderr)
        repeated = run('enterprise_ai.governance_demo', 'create', '--seed', self.seed, '--output', inputs)
        self.assertEqual(repeated.returncode, 1)
        workspace = self.root / 'warehouse with spaces'
        for name in ['01-initial.jsonl', '02-changes.jsonl']:
            result = run('enterprise_ai.governance', 'run', '--seed', self.seed,
                         '--input', inputs / name, '--workspace', workspace)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertGreater(json.loads(result.stdout)['quarantined'], 0)
        result = run('enterprise_ai.governance', 'inspect', '--workspace', workspace)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('customers', json.loads(result.stdout))
        self.assertEqual(json.loads(result.stdout)['summary'][0]['active_customers'], 19)
        result = run('enterprise_ai.governance_demo', 'verify', '--seed', self.seed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['active_customers'], 39)


if __name__ == '__main__':
    unittest.main()
