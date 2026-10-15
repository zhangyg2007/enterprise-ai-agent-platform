import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from enterprise_ai.data import dataset_digest, generate_dataset, validate_dataset


class DataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = generate_dataset()

    def test_deterministic_and_seed_sensitive(self):
        self.assertEqual(self.dataset, generate_dataset(seed=42))
        self.assertNotEqual(dataset_digest(self.dataset), dataset_digest(generate_dataset(seed=43)))
        reversed_keys = dict(reversed(list(self.dataset.items())))
        self.assertEqual(dataset_digest(self.dataset), dataset_digest(reversed_keys))
        self.assertEqual(len(dataset_digest(self.dataset)), 64)

    def test_tiny_counts_and_business_scenarios(self):
        validate_dataset(self.dataset)
        tables = self.dataset['tables']
        self.assertEqual(len(tables), 14)
        for table, count in {'crm.customers': 40, 'hcm.employees': 60,
                             'scm.products': 80, 'crm.sales_orders': 200,
                             'scm.purchase_orders': 60, 'hcm.departments': 8,
                             'scm.suppliers': 10, 'scm.warehouses': 4,
                             'hcm.leave_balances': 60}.items():
            self.assertEqual(len(tables[table]), count, table)
        self.assertTrue(all(e['email'].endswith('@example.com') for e in tables['hcm.employees']))
        for tenant in (1, 2):
            self.assertTrue(any(i['tenant_id'] == tenant and i['product_id'] == 1
                                and i['on_hand'] - i['reserved'] < i['reorder_point']
                                for i in tables['scm.inventory']))
        for orders, items in [('crm.sales_orders', 'crm.sales_order_items'),
                              ('scm.purchase_orders', 'scm.purchase_order_items')]:
            counts = {}
            for row in tables[items]:
                key = row['tenant_id'], row['order_id']
                counts[key] = counts.get(key, 0) + 1
            self.assertEqual(len(counts), len(tables[orders]))
            self.assertTrue(all(1 <= n <= 3 for n in counts.values()))

    def test_standard_counts(self):
        dataset = generate_dataset('standard', 7)
        validate_dataset(dataset)
        for table, count in {'crm.customers': 2000, 'hcm.employees': 1000,
                             'scm.products': 4000, 'crm.sales_orders': 40000,
                             'scm.purchase_orders': 10000}.items():
            self.assertEqual(len(dataset['tables'][table]), count)

    def assert_invalid(self, mutate):
        dataset = copy.deepcopy(self.dataset)
        mutate(dataset)
        with self.assertRaises(ValueError):
            validate_dataset(dataset)

    def test_bad_values_and_types(self):
        cases = [('scm.inventory', 'on_hand', -1), ('scm.inventory', 'reserved', 99999),
                 ('crm.sales_order_items', 'quantity', True),
                 ('scm.products', 'unit_price', 'NaN'), ('scm.products', 'unit_price', '-1.00'),
                 ('scm.products', 'unit_price', '1.001'), ('scm.products', 'unit_price', 1.25),
                 ('crm.sales_orders', 'order_date', '2026-02-30'),
                 ('crm.sales_orders', 'order_date', '20260101'),
                 ('crm.sales_orders', 'status', 'invalid'), ('hcm.employees', 'name', None),
                 ('hcm.leave_balances', 'used_days', '999.00')]
        for table, field, value in cases:
            with self.subTest(table=table, field=field, value=value):
                self.assert_invalid(lambda d: d['tables'][table][0].__setitem__(field, value))

    def test_schema_duplicate_and_foreign_keys(self):
        mutations = [lambda d: d['tables'].pop('crm.customers'),
                     lambda d: d.__setitem__('unexpected', 1),
                     lambda d: d.__setitem__('schema_version', True),
                     lambda d: d['tables']['scm.products'][0].__setitem__('extra', 'x'),
                     lambda d: d['tables']['hcm.employees'].append(d['tables']['hcm.employees'][0].copy()),
                     lambda d: d['tables']['crm.customers'][0].__setitem__('owner_employee_id', 999),
                     lambda d: d['tables']['scm.products'][0].__setitem__('tenant_id', 3),
                     lambda d: d['tables']['scm.purchase_orders'][0].__setitem__('supplier_id', 999)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_invalid(mutation)

    def test_cross_tenant_foreign_key(self):
        dataset = copy.deepcopy(self.dataset)
        # ID 999 exists, but only under the other tenant.
        dataset['tables']['hcm.employees'].append(
            dict(dataset['tables']['hcm.employees'][-1], id=999, email='employee999.tenant2@example.com'))
        dataset['tables']['hcm.leave_balances'].append(
            dict(tenant_id=2, employee_id=999, year=2026, entitled_days='20.00', used_days='0.00'))
        dataset['tables']['crm.customers'][0]['owner_employee_id'] = 999
        with self.assertRaisesRegex(ValueError, 'missing same-tenant reference'):
            validate_dataset(dataset)

    def test_database_supplier_lead_day_bounds(self):
        for days in (0, 366):
            with self.subTest(days=days):
                dataset = copy.deepcopy(self.dataset)
                dataset['tables']['scm.suppliers'][0]['lead_days'] = days
                with self.assertRaisesRegex(ValueError, 'lead_days.*out of range'):
                    validate_dataset(dataset)
        for days in (1, 365):
            dataset = copy.deepcopy(self.dataset)
            dataset['tables']['scm.suppliers'][0]['lead_days'] = days
            validate_dataset(dataset)

    def test_database_prices_must_be_positive(self):
        for table, field in [('scm.products', 'unit_price'),
                             ('crm.sales_order_items', 'unit_price'),
                             ('scm.purchase_order_items', 'unit_cost')]:
            with self.subTest(table=table):
                dataset = copy.deepcopy(self.dataset)
                dataset['tables'][table][0][field] = '0.00'
                with self.assertRaisesRegex(ValueError, field + '.*must be positive'):
                    validate_dataset(dataset)
                dataset['tables'][table][0][field] = '0.01'
                validate_dataset(dataset)

    def test_database_leave_decimal_precision(self):
        for table, field in [('hcm.leave_balances', 'entitled_days'),
                             ('hcm.leave_balances', 'used_days'),
                             ('hcm.leave_requests', 'days')]:
            with self.subTest(field=field):
                dataset = copy.deepcopy(self.dataset)
                dataset['tables'][table][0][field] = '10000.00'
                with self.assertRaisesRegex(ValueError, field + '.*amount out of range'):
                    validate_dataset(dataset)
        dataset = copy.deepcopy(self.dataset)
        dataset['tables']['hcm.leave_balances'][0]['entitled_days'] = '9999.99'
        validate_dataset(dataset)

    def test_database_text_rejects_nul(self):
        for table, field in [('core.tenants', 'name'), ('hcm.employees', 'email'),
                             ('scm.products', 'sku')]:
            with self.subTest(table=table, field=field):
                dataset = copy.deepcopy(self.dataset)
                dataset['tables'][table][0][field] = 'prefix\x00' + dataset['tables'][table][0][field]
                with self.assertRaisesRegex(ValueError, 'NUL'):
                    validate_dataset(dataset)

    def test_purchase_supplier_mismatch(self):
        def mutate(d):
            row = d['tables']['scm.purchase_order_items'][0]
            supplier = d['tables']['scm.purchase_orders'][0]['supplier_id']
            row['product_id'] = next(p['id'] for p in d['tables']['scm.products']
                                     if p['tenant_id'] == row['tenant_id'] and p['supplier_id'] != supplier)
        self.assert_invalid(mutate)

    def test_pending_leave_not_debited(self):
        def mutate(d):
            request = next(r for r in d['tables']['hcm.leave_requests'] if r['status'] == 'approved')
            request['status'] = 'pending'
        self.assert_invalid(mutate)

    def test_invalid_arguments(self):
        for profile, seed in [('huge', 42), ('tiny', True), ('tiny', '42')]:
            with self.assertRaises(ValueError):
                generate_dataset(profile, seed)

    def test_cli_round_trip_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            target = Path(directory) / 'tiny.json'
            command = [sys.executable, '-m', 'enterprise_ai.data']
            import os
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
            result = subprocess.run(command + ['generate', '--output', str(target)],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(dataset_digest(self.dataset), result.stdout)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')), self.dataset)
            result = subprocess.run(command + ['validate', str(target)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            before = target.read_bytes()
            result = subprocess.run(command + ['generate', '--seed', '43', '--output', str(target)],
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(target.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
