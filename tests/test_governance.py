"""Behavioral tests for incremental ETL, including interrupted publication."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from enterprise_ai.data import generate_dataset, validate_dataset
from enterprise_ai.governance import run_batch, inspect_workspace


def event(eid='e1', tenant=1, customer=1, version=1, op='upsert', name=' 客户一 '):
    return dict(event_id=eid, tenant_id=tenant, customer_id=customer,
                version=version, op=op,
                payload={'name': name, 'owner_employee_id': 1} if op == 'upsert' else None)


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.seed = self.root / 'seed.json'
        self.seed.write_text(json.dumps(generate_dataset()), encoding='utf-8')
        self.ws = self.root / 'warehouse'
        self.seq = 0

    def batch(self, rows):
        self.seq += 1
        path = self.root / f'batch-{self.seq}.jsonl'
        path.write_text('\n'.join(r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
                                  for r in rows) + '\n', encoding='utf-8')
        return path

    def run_rows(self, rows):
        return run_batch(self.seed, self.batch(rows), self.ws)

    def test_clean_duplicate_and_batch_idempotency(self):
        path = self.batch([event(), event()])
        first = run_batch(self.seed, path, self.ws)
        self.assertEqual((first['applied'], first['duplicate'], first['quarantined']), (1, 1, 0))
        self.assertEqual(first['cleaned'], 1)
        snapshot = inspect_workspace(self.ws)
        self.assertEqual(snapshot['customers'][0]['name'], '客户一')
        again = run_batch(self.seed, path, self.ws)
        self.assertTrue(again['unchanged'])
        self.assertEqual(inspect_workspace(self.ws)['customers'], snapshot['customers'])
        with closing(sqlite3.connect(self.ws / 'state.sqlite3')) as conn, conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM batches').fetchone()[0], 1)

    def test_delete_late_event_and_tenant_boundary(self):
        self.run_rows([event(), event('b', tenant=2)])
        result = self.run_rows([event('del', version=2, op='delete'), event('late', version=1)])
        self.assertEqual((result['applied'], result['stale']), (1, 1))
        snapshot = inspect_workspace(self.ws)
        self.assertEqual([(r['tenant_id'], r['customer_id']) for r in snapshot['customers']], [(2, 1)])
        self.assertEqual(snapshot['summary'], [{'tenant_id': 1, 'active_customers': 0},
                                                {'tenant_id': 2, 'active_customers': 1}])
        self.run_rows([event('restore', version=3)])
        self.assertEqual(len(inspect_workspace(self.ws)['customers']), 2)

    def test_event_and_current_version_conflicts_are_quarantined(self):
        self.run_rows([event()])
        result = self.run_rows([event(name='changed'), event('new-id', name='changed')])
        self.assertEqual(result['quarantined'], 2)
        self.assertEqual(result['applied'], 0)
        self.assertEqual(inspect_workspace(self.ws)['customers'][0]['name'], '客户一')

    def test_version_conflict_reserves_valid_event_identity(self):
        self.run_rows([event()])
        conflict = event('reserved', name='Version conflict')
        self.assertEqual(self.run_rows([conflict])['quarantined'], 1)
        changed = event('reserved', version=2, name='Must not apply')
        result = self.run_rows([changed, conflict])
        self.assertEqual((result['applied'], result['quarantined'], result['duplicate']), (0, 1, 1))
        self.assertEqual(inspect_workspace(self.ws)['customers'][0]['version'], 1)

    def test_invalid_rows_do_not_block_valid_rows(self):
        bad_owner = event('bad-owner')
        bad_owner['payload']['owner_employee_id'] = 9999
        unknown = event('unknown')
        unknown['unexpected'] = 1
        result = self.run_rows(['{', event('bad-tenant', tenant=3), event('bool', version=True),
                                bad_owner, unknown, event()])
        self.assertEqual((result['rows'], result['applied'], result['quarantined']), (6, 1, 5))
        with closing(sqlite3.connect(self.ws / 'state.sqlite3')) as conn, conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM quarantine').fetchone()[0], 5)

    def test_duplicate_json_keys_nonfinite_and_control_text(self):
        duplicate = json.dumps(event()).replace('"version": 1', '"version": 1, "version": 2')
        nonfinite = json.dumps(event()).replace('"version": 1', '"version": NaN')
        result = self.run_rows([duplicate, nonfinite, event('nul', name='bad\x00name')])
        self.assertEqual(result['quarantined'], 3)

    def test_invalid_utf8_is_a_file_error_not_silent_replacement(self):
        path = self.root / 'invalid.jsonl'
        path.write_bytes(b'\xff\xfe\x00\x00')
        with self.assertRaises(ValueError):
            run_batch(self.seed, path, self.ws)
        self.assertFalse((self.ws / 'CURRENT.json').exists())

    def test_blank_line_is_quarantined_and_report_accounts_for_all_lines(self):
        result = self.run_rows([event(), '', 'null'])
        self.assertEqual((result['rows'], result['applied'], result['quarantined']), (3, 1, 2))
        self.assertEqual(result['rows'], sum(result[key] for key in
                                            ['applied', 'duplicate', 'stale', 'quarantined']))

    def test_cross_tenant_owner_reference_rejected(self):
        dataset = generate_dataset()
        # Employee 31 exists in tenant 2 only, including all required dependent rows.
        for table, rows in dataset['tables'].items():
            for item in rows:
                if item.get('tenant_id') == 2:
                    for field in ['employee_id', 'owner_employee_id'] + (['id'] if table == 'hcm.employees' else []):
                        if item.get(field) == 30:
                            item[field] = 31
        validate_dataset(dataset)
        self.seed.write_text(json.dumps(dataset), encoding='utf-8')
        row = event('bad', tenant=1)
        row['payload']['owner_employee_id'] = 31
        self.assertEqual(self.run_rows([row])['quarantined'], 1)
        row.update(event_id='valid', tenant_id=2)
        self.assertEqual(self.run_rows([row])['applied'], 1)

    def test_strict_contract_rejects_invalid_operations_and_scalar_types(self):
        rows = []
        for key, value in [('version', 0), ('version', 1.5), ('version', '2'),
                           ('customer_id', 0), ('tenant_id', True), ('op', 'truncate'),
                           ('event_id', '')]:
            row = event(f'bad-{len(rows)}')
            row[key] = value
            rows.append(row)
        row = event('bad-delete', op='delete')
        row['payload'] = {}
        rows.append(row)
        for payload in [{'name': ' ', 'owner_employee_id': 1},
                        {'name': 'Test', 'owner_employee_id': True},
                        {'name': 'Test', 'owner_employee_id': 1, 'extra': 'field'}]:
            row = event(f'bad-{len(rows)}')
            row['payload'] = payload
            rows.append(row)
        self.assertEqual(self.run_rows(rows)['quarantined'], len(rows))

    def test_delete_unknown_entity_still_prevents_late_resurrection(self):
        result = self.run_rows([event('delete', customer=99, version=5, op='delete'),
                               event('late', customer=99, version=3)])
        self.assertEqual((result['applied'], result['stale']), (1, 1))
        self.assertEqual(inspect_workspace(self.ws)['customers'], [])

    def test_seed_digest_mismatch_does_not_change_state(self):
        self.run_rows([event()])
        before = inspect_workspace(self.ws)['customers']
        self.seed.write_text(json.dumps(generate_dataset(seed=43)), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.run_rows([event('v2', version=2)])
        self.assertEqual(inspect_workspace(self.ws)['customers'], before)

    def test_processing_failure_rolls_back_whole_batch(self):
        self.run_rows([event()])
        # A trigger simulates a disk/database failure on the second write.
        with closing(sqlite3.connect(self.ws / 'state.sqlite3')) as conn, conn:
            conn.execute("CREATE TRIGGER fail_second BEFORE INSERT ON events "
                         "WHEN NEW.event_id='fail' BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.run_rows([event('good', version=2), event('fail', tenant=2)])
        with closing(sqlite3.connect(self.ws / 'state.sqlite3')) as conn, conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM batches').fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM events WHERE event_id='good'").fetchone()[0], 0)
        self.assertEqual(inspect_workspace(self.ws)['customers'][0]['version'], 1)

    def test_publish_failure_can_be_recovered_by_same_batch(self):
        self.run_rows([event()])
        path = self.batch([event('v2', version=2, name='Updated')])
        import enterprise_ai.governance as module
        real_replace = module.os.replace
        def fail_pointer(src, dst):
            if Path(dst).name == 'CURRENT.json':
                raise OSError('injected publication failure')
            return real_replace(src, dst)
        with patch.object(module.os, 'replace', side_effect=fail_pointer):
            with self.assertRaises(OSError):
                run_batch(self.seed, path, self.ws)
        self.assertEqual(inspect_workspace(self.ws)['customers'][0]['version'], 1)
        result = run_batch(self.seed, path, self.ws)
        self.assertTrue(result['unchanged'])
        self.assertEqual(inspect_workspace(self.ws)['customers'][0]['name'], 'Updated')

    def test_snapshot_corruption_detected(self):
        self.run_rows([event()])
        inspect_workspace(self.ws)
        pointer = json.loads((self.ws / 'CURRENT.json').read_text())
        manifest_path = self.ws / pointer['manifest']
        (manifest_path.parent / 'customers.json').write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):
            inspect_workspace(self.ws)

    def test_input_limit_and_lock_refusal(self):
        self.ws.mkdir()
        (self.ws / '.lock').write_text('existing-process', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            self.run_rows([event()])
        self.assertEqual((self.ws / '.lock').read_text(), 'existing-process')
        (self.ws / '.lock').unlink()
        with self.assertRaises(ValueError):
            self.run_rows(['x' * 65537])
        self.assertFalse((self.ws / 'CURRENT.json').exists())

    def test_raw_archive_is_exact_and_checked_before_replay(self):
        path = self.batch([event()])
        run_batch(self.seed, path, self.ws)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        raw = self.ws / 'raw' / (digest + '.jsonl')
        self.assertEqual(raw.read_bytes(), path.read_bytes())
        raw.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            run_batch(self.seed, path, self.ws)


if __name__ == '__main__':
    unittest.main()
