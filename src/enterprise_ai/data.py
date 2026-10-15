"""Deterministic, synthetic CRM/HCM/SCM fixtures; Python standard library only."""

import argparse
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
import re
import sys


AS_OF = '2026-10-15'
PROFILES = {'tiny': (20, 30, 40, 100, 30, 5),
            'standard': (1000, 500, 2000, 20000, 5000, 50)}
_COLUMNS = {
    'core.tenants': 'id name',
    'hcm.departments': 'tenant_id id name',
    'hcm.employees': 'tenant_id id department_id name email',
    'hcm.leave_balances': 'tenant_id employee_id year entitled_days used_days',
    'hcm.leave_requests': 'tenant_id id employee_id start_date end_date days status',
    'scm.suppliers': 'tenant_id id name lead_days',
    'scm.products': 'tenant_id id supplier_id sku name unit unit_price',
    'scm.warehouses': 'tenant_id id name',
    'scm.inventory': 'tenant_id warehouse_id product_id on_hand reserved reorder_point',
    'crm.customers': 'tenant_id id name owner_employee_id',
    'crm.sales_orders': 'tenant_id id customer_id order_date status',
    'crm.sales_order_items': 'tenant_id order_id line_no product_id quantity unit_price',
    'scm.purchase_orders': 'tenant_id id supplier_id expected_date status',
    'scm.purchase_order_items': 'tenant_id order_id line_no product_id quantity unit_cost',
}
_PK = {'core.tenants': ('id',),
       'hcm.leave_balances': ('tenant_id', 'employee_id', 'year'),
       'scm.inventory': ('tenant_id', 'warehouse_id', 'product_id'),
       'crm.sales_order_items': ('tenant_id', 'order_id', 'line_no'),
       'scm.purchase_order_items': ('tenant_id', 'order_id', 'line_no')}
_INTS = {'id', 'tenant_id', 'department_id', 'employee_id', 'year', 'lead_days',
         'supplier_id', 'warehouse_id', 'product_id', 'on_hand', 'reserved',
         'reorder_point', 'owner_employee_id', 'customer_id', 'order_id', 'line_no', 'quantity'}
_NONNEGATIVE = {'on_hand', 'reserved', 'reorder_point'}
_DECIMALS = {'entitled_days', 'used_days', 'days', 'unit_price', 'unit_cost'}
_DATES = {'start_date', 'end_date', 'order_date', 'expected_date'}
_STATUSES = {'hcm.leave_requests': {'pending', 'approved', 'rejected'},
             'crm.sales_orders': {'open', 'completed'},
             'scm.purchase_orders': {'ordered', 'received'}}
_FKS = {
    'hcm.employees': [('department_id', 'hcm.departments')],
    'hcm.leave_balances': [('employee_id', 'hcm.employees')],
    'hcm.leave_requests': [('employee_id', 'hcm.employees')],
    'scm.products': [('supplier_id', 'scm.suppliers')],
    'scm.inventory': [('warehouse_id', 'scm.warehouses'), ('product_id', 'scm.products')],
    'crm.customers': [('owner_employee_id', 'hcm.employees')],
    'crm.sales_orders': [('customer_id', 'crm.customers')],
    'crm.sales_order_items': [('order_id', 'crm.sales_orders'), ('product_id', 'scm.products')],
    'scm.purchase_orders': [('supplier_id', 'scm.suppliers')],
    'scm.purchase_order_items': [('order_id', 'scm.purchase_orders'), ('product_id', 'scm.products')],
}


def _money(cents):
    return f'{cents // 100}.{cents % 100:02d}'


def generate_dataset(profile='tiny', seed=42):
    """Return a reproducible dataset with two isolated tenants and no real PII."""
    if not isinstance(profile, str) or profile not in PROFILES or type(seed) is not int:
        raise ValueError('profile must be tiny/standard and seed must be an integer')
    customers, employees, products, sales, purchases, suppliers = PROFILES[profile]
    rng = random.Random(seed)
    tables = {name: [] for name in _COLUMNS}

    def add(table, *values):
        tables[table].append(dict(zip(_COLUMNS[table].split(), values)))

    for tenant in (1, 2):
        add('core.tenants', tenant, f'Synthetic Tenant {tenant}')
        for entity in range(1, 5):
            add('hcm.departments', tenant, entity, f'Department {entity}')
        for entity in range(1, employees + 1):
            add('hcm.employees', tenant, entity, (entity - 1) % 4 + 1,
                f'Employee {tenant}-{entity}', f'employee{entity}.tenant{tenant}@example.com')
            approved_days = rng.randint(1, 5)
            add('hcm.leave_balances', tenant, entity, 2026, '20.00', f'{approved_days}.00')
            for offset, status in enumerate(('approved', 'pending', 'rejected')):
                days = approved_days if status == 'approved' else rng.randint(1, 3)
                start = date(2026, (2, 11, 12)[offset], 1)
                add('hcm.leave_requests', tenant, (entity - 1) * 3 + offset + 1,
                    entity, start.isoformat(), (start + timedelta(days=days - 1)).isoformat(),
                    f'{days}.00', status)
        for entity in range(1, suppliers + 1):
            add('scm.suppliers', tenant, entity, f'Supplier {tenant}-{entity}', rng.randint(2, 21))
        product_prices = {}
        supplier_products = defaultdict(list)
        for entity in range(1, products + 1):
            supplier = (entity - 1) % suppliers + 1
            price = rng.randint(100, 100000)
            product_prices[entity] = price
            supplier_products[supplier].append(entity)
            add('scm.products', tenant, entity, supplier, f'SYN-{tenant}-{entity:05}',
                f'Product {tenant}-{entity}', 'piece', _money(price))
        for warehouse in (1, 2):
            add('scm.warehouses', tenant, warehouse, f'Warehouse {warehouse}')
            for product in range(1, products + 1):
                on_hand = 0 if product == 1 else rng.randint(10, 500)
                add('scm.inventory', tenant, warehouse, product, on_hand,
                    rng.randint(0, on_hand), rng.randint(5, 30))
        for entity in range(1, customers + 1):
            add('crm.customers', tenant, entity, f'Customer {tenant}-{entity}',
                (entity - 1) % employees + 1)
        for entity in range(1, sales + 1):
            order_date = date(2026, 1, 1) + timedelta(days=rng.randint(0, 287))
            add('crm.sales_orders', tenant, entity, (entity - 1) % customers + 1,
                order_date.isoformat(), rng.choice(('open', 'completed')))
            for line in range(1, rng.randint(1, 3) + 1):
                product = rng.randint(1, products)
                add('crm.sales_order_items', tenant, entity, line, product,
                    rng.randint(1, 15), _money(product_prices[product]))
        for entity in range(1, purchases + 1):
            supplier = (entity - 1) % suppliers + 1
            expected = date(2026, 10, 15) + timedelta(days=rng.randint(0, 45))
            add('scm.purchase_orders', tenant, entity, supplier, expected.isoformat(),
                rng.choice(('ordered', 'received')))
            for line in range(1, rng.randint(1, 3) + 1):
                product = rng.choice(supplier_products[supplier])
                add('scm.purchase_order_items', tenant, entity, line, product,
                    rng.randint(10, 100), _money(product_prices[product] * 70 // 100))
    return {'schema_version': 1, 'profile': profile, 'seed': seed,
            'as_of': AS_OF, 'tables': tables}


def validate_dataset(dataset):
    """Reject malformed schemas, references and inconsistent synthetic business data."""
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(type(dataset) is dict and set(dataset) ==
            {'schema_version', 'profile', 'seed', 'as_of', 'tables'}, 'invalid top-level fields')
    require(type(dataset['schema_version']) is int and dataset['schema_version'] == 1,
            'unsupported schema_version')
    require(type(dataset['profile']) is str and dataset['profile'] in PROFILES, 'invalid profile')
    require(type(dataset['seed']) is int, 'seed must be an integer')
    require(dataset['as_of'] == AS_OF, 'invalid as_of')
    tables = dataset['tables']
    require(type(tables) is dict and set(tables) == set(_COLUMNS), 'invalid table set')
    indexes = {}
    for table, column_text in _COLUMNS.items():
        require(type(tables[table]) is list, f'{table}: rows must be a list')
        pk = _PK.get(table, ('tenant_id', 'id'))
        index = indexes[table] = {}
        columns = set(column_text.split())
        for row in tables[table]:
            require(type(row) is dict and set(row) == columns, f'{table}: invalid columns')
            for column, value in row.items():
                label = f'{table}.{column}'
                if column in _INTS:
                    require(type(value) is int, f'{label}: expected integer')
                    require(value >= (0 if column in _NONNEGATIVE else 1), f'{label}: out of range')
                    require(value <= 2147483647, f'{label}: exceeds database integer range')
                    if column == 'lead_days':
                        require(value <= 365, f'{label}: out of range')
                else:
                    require(type(value) is str and bool(value.strip()), f'{label}: expected nonempty string')
                    require('\x00' not in value, f'{label}: NUL characters are not supported')
                    if column in _DECIMALS:
                        require(re.fullmatch(r'(0|[1-9][0-9]*)\.[0-9]{2}', value) is not None,
                                f'{label}: expected nonnegative fixed decimal with two places')
                        maximum = '10000' if column in {'entitled_days', 'used_days', 'days'} else '10000000000'
                        require(Decimal(value) < Decimal(maximum), f'{label}: amount out of range')
                        if column in {'days', 'unit_price', 'unit_cost'}:
                            require(Decimal(value) > 0, f'{label}: must be positive')
                    elif column in _DATES:
                        try:
                            parsed = date.fromisoformat(value)
                        except ValueError as exc:
                            raise ValueError(f'{label}: invalid date') from exc
                        require(parsed.isoformat() == value and parsed.year == 2026, f'{label}: invalid ISO date/year')
            key = tuple(row[column] for column in pk)
            require(key not in index, f'{table}: duplicate primary key {key}')
            index[key] = row
            if table in _STATUSES:
                require(row['status'] in _STATUSES[table], f'{table}: invalid status')
            if table == 'hcm.employees':
                require(re.fullmatch(r'[^\s@]+@example\.com', row['email']) is not None,
                        'employees must have synthetic example.com emails')
            if table == 'hcm.leave_balances':
                require(row['year'] == 2026, 'leave balance year must be 2026')
                require(Decimal(row['used_days']) <= Decimal(row['entitled_days']), 'leave balance overdrawn')
            if table == 'hcm.leave_requests':
                require(row['start_date'] <= row['end_date'], 'leave date range reversed')
                span = (date.fromisoformat(row['end_date']) - date.fromisoformat(row['start_date'])).days + 1
                require(Decimal(row['days']) <= span, 'leave days exceed calendar span')
            if table == 'scm.inventory':
                require(row['reserved'] <= row['on_hand'], 'reserved stock exceeds on_hand')
    require(set(indexes['core.tenants']) == {(1,), (2,)}, 'expected tenants 1 and 2')
    for table, rows in tables.items():
        for row in rows:
            if table != 'core.tenants':
                require((row['tenant_id'],) in indexes['core.tenants'], f'{table}: missing tenant')
            for column, target in _FKS.get(table, ()):
                require((row['tenant_id'], row[column]) in indexes[target],
                        f'{table}.{column}: missing same-tenant reference')
    for orders, items in [('crm.sales_orders', 'crm.sales_order_items'),
                          ('scm.purchase_orders', 'scm.purchase_order_items')]:
        lines = defaultdict(list)
        for row in tables[items]:
            key = row['tenant_id'], row['order_id']
            lines[key].append(row['line_no'])
            if items == 'scm.purchase_order_items':
                product = indexes['scm.products'][row['tenant_id'], row['product_id']]
                require(product['supplier_id'] == indexes[orders][key]['supplier_id'],
                        'purchase item product belongs to another supplier')
        for key in indexes[orders]:
            numbers = sorted(lines[key])
            require(1 <= len(numbers) <= 3 and numbers == list(range(1, len(numbers) + 1)),
                    f'{orders}: expected 1-3 consecutive item lines')
    approved = defaultdict(Decimal)
    for row in tables['hcm.leave_requests']:
        key = row['tenant_id'], row['employee_id'], 2026
        require(key in indexes['hcm.leave_balances'], 'leave request missing balance')
        if row['status'] == 'approved':
            approved[key] += Decimal(row['days'])
    for key, row in indexes['hcm.leave_balances'].items():
        require(Decimal(row['used_days']) == approved[key],
                'used_days must equal approved leave only; pending/rejected leave is not deducted')
    for key in indexes['hcm.employees']:
        require((*key, 2026) in indexes['hcm.leave_balances'], 'employee missing leave balance')
    for table, column in [('hcm.employees', 'email'), ('scm.products', 'sku')]:
        values = [(row['tenant_id'], row[column]) for row in tables[table]]
        require(len(values) == len(set(values)), f'{table}: duplicate {column}')


def dataset_digest(dataset):
    """SHA256 of UTF-8 JSON, sorted keys, compact separators, no NaN values."""
    canonical = json.dumps(dataset, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    generate = commands.add_parser('generate', help='write a synthetic JSON dataset')
    generate.add_argument('--profile', choices=tuple(PROFILES), default='tiny')
    generate.add_argument('--seed', type=int, default=42)
    generate.add_argument('--output', type=Path, required=True)
    validate = commands.add_parser('validate', help='validate an existing JSON dataset')
    validate.add_argument('path', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'generate':
            dataset = generate_dataset(args.profile, args.seed)
            validate_dataset(dataset)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('x', encoding='utf-8', newline='\n') as stream:
                json.dump(dataset, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
        else:
            with args.path.open(encoding='utf-8') as stream:
                dataset = json.load(stream)
            validate_dataset(dataset)
    except (ValueError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({'sha256': dataset_digest(dataset), 'profile': dataset['profile'],
                      'seed': dataset['seed'],
                      'rows': {name: len(rows) for name, rows in dataset['tables'].items()}},
                     sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
