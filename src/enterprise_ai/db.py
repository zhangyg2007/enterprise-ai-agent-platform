"""Run on Ubuntu: health, atomic synthetic-data load, demos, real DB checks."""
import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from .data import dataset_digest, validate_dataset
from .queries import CUSTOMER, INVENTORY, LEAVE
from .settings import load_settings

# Dependency order is deliberate. No arbitrary table names or SQL from CLI/LLM.
TABLES = (
    'core.tenants', 'hcm.departments', 'hcm.employees', 'hcm.leave_balances',
    'hcm.leave_requests', 'scm.suppliers', 'scm.products', 'scm.warehouses',
    'scm.inventory', 'crm.customers', 'crm.sales_orders', 'crm.sales_order_items',
    'scm.purchase_orders', 'scm.purchase_order_items',
)


def connect(settings, reader=None):
    import psycopg
    if reader is None:
        user, password = settings['POSTGRES_USER'], settings['POSTGRES_PASSWORD']
    elif reader in ('a', 'b'):
        user = f'lab_{reader}_reader'
        password = settings[f'LAB_{reader.upper()}_PASSWORD']
    else:
        raise ValueError('Unknown reader')
    return psycopg.connect(host='127.0.0.1', port=int(settings['POSTGRES_PORT']),
                           dbname=settings['POSTGRES_DB'], user=user, password=password,
                           connect_timeout=5, autocommit=True)


def table_identifier(name):
    from psycopg import sql
    if name not in TABLES:
        raise ValueError('Unknown dataset table')
    return sql.Identifier(*name.split('.'))


def normalize(value):
    if isinstance(value, Decimal):
        return format(value, '.2f')
    if isinstance(value, date):
        return value.isoformat()
    return value


def assert_matches(conn, dataset):
    """Compare actual persisted content, not just row counts or a marker."""
    from psycopg import sql
    for name in TABLES:
        rows = dataset['tables'][name]
        if not rows:
            actual = conn.execute(sql.SQL('SELECT count(*) FROM {}').format(table_identifier(name))).fetchone()[0]
            if actual:
                raise ValueError(f'Database differs from dataset: {name}')
            continue
        columns = sorted(rows[0])
        result = conn.execute(sql.SQL('SELECT {} FROM {}').format(
            sql.SQL(',').join(map(sql.Identifier, columns)), table_identifier(name)))
        actual = [tuple(normalize(v) for v in row) for row in result]
        expected = [tuple(row[c] for c in columns) for row in rows]
        if sorted(actual) != sorted(expected):
            raise ValueError(f'Database differs from dataset: {name}')


def health(settings):
    with connect(settings) as conn:
        version = conn.execute('SHOW server_version').fetchone()[0]
        extension = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
        schema = conn.execute('SELECT max(version) FROM lab.schema_versions').fetchone()[0]
        if extension is None or schema != 1:
            raise ValueError('Expected pgvector and schema version 1')
        return {'postgres': version, 'pgvector': extension[0], 'schema_version': schema}


def load_dataset(settings, dataset):
    from psycopg import sql
    from psycopg.types.json import Jsonb
    validate_dataset(dataset)
    if set(dataset['tables']) != set(TABLES):
        raise ValueError('Unexpected dataset tables')
    digest = dataset_digest(dataset)
    counts = {name: len(dataset['tables'][name]) for name in TABLES}
    with connect(settings) as conn, conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (2026101501,))
        existing = conn.execute('SELECT digest FROM lab.seed_runs WHERE singleton').fetchone()
        if existing:
            if existing[0] != digest:
                raise ValueError('Different dataset already loaded; use a separate lab database/volume')
            assert_matches(conn, dataset)
            return {'status': 'unchanged', 'digest': digest, 'counts': counts}
        for name in TABLES:
            if conn.execute(sql.SQL('SELECT EXISTS (SELECT 1 FROM {})').format(table_identifier(name))).fetchone()[0]:
                raise ValueError(f'Table is not empty: {name}; refusing to mix datasets')
        for name in TABLES:
            rows = dataset['tables'][name]
            if not rows:
                continue
            columns = sorted(rows[0])
            command = sql.SQL('COPY {} ({}) FROM STDIN').format(
                table_identifier(name), sql.SQL(',').join(map(sql.Identifier, columns)))
            with conn.cursor() as cursor, cursor.copy(command) as copy:
                for row in rows:
                    copy.write_row(tuple(row[column] for column in columns))
        assert_matches(conn, dataset)
        conn.execute('INSERT INTO lab.seed_runs (digest, profile, counts) VALUES (%s, %s, %s)',
                     (digest, dataset['profile'], Jsonb(counts)))
    return {'status': 'loaded', 'digest': digest, 'counts': counts}


def demo(settings, reader):
    with connect(settings, reader) as conn:
        return {'reader': reader, 'customer_1': conn.execute(CUSTOMER, (1,)).fetchall(),
                'my_leave_2026': conn.execute(LEAVE, (2026,)).fetchall(),
                'product_1': conn.execute(INVENTORY, (1,)).fetchall()}


def require(condition, message):
    if not condition:
        raise ValueError(f'Verification failed: {message}')


def verify(settings, dataset):
    """Real PostgreSQL tests. Negative writes use transactions and rollback."""
    from psycopg import errors, sql
    validate_dataset(dataset)
    checks = []
    health(settings)
    checks.append('health_and_schema')
    # Only a previously imported dataset is eligible; verification never seeds it.
    with connect(settings) as admin:
        marker = admin.execute('SELECT digest FROM lab.seed_runs WHERE singleton').fetchone()
        require(marker is not None and marker[0] == dataset_digest(dataset), 'import the supplied dataset first')
        assert_matches(admin, dataset)
    require(load_dataset(settings, dataset)['status'] == 'unchanged', 'idempotent import')
    checks.append('content_and_idempotent_import')
    for reader, tenant in (('a', 1), ('b', 2)):
        with connect(settings, reader) as conn:
            flags = conn.execute('SELECT rolsuper, rolbypassrls, rolcreaterole FROM pg_roles WHERE rolname = current_user').fetchone()
            require(flags == (False, False, False), 'reader privilege flags')
            conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(3 - tenant),))
            require(conn.execute('SELECT auth.current_tenant()').fetchone()[0] == tenant, 'session setting cannot spoof tenant')
            require(conn.execute('SELECT id FROM core.tenants').fetchall() == [(tenant,)], 'tenant visibility')
            for name in TABLES[1:]:
                visible = conn.execute(sql.SQL('SELECT DISTINCT tenant_id FROM {}').format(table_identifier(name))).fetchall()
                require(all(row[0] == tenant for row in visible), f'{name}: cross-tenant visibility')
                rls = conn.execute('SELECT relrowsecurity, pg_get_userbyid(relowner) = current_user FROM pg_class WHERE oid = %s::regclass', (name,)).fetchone()
                require(rls == (True, False), f'{name}: RLS and non-owner role')
            require(conn.execute('SELECT id FROM hcm.employees').fetchall() == [(1,)], 'employee self-only')
            require(conn.execute('SELECT employee_id FROM hcm.leave_balances').fetchall() == [(1,)], 'balance self-only')
            require(all(row[0] == 1 for row in conn.execute('SELECT employee_id FROM hcm.leave_requests')), 'request self-only')
            expected_orders = {r['id'] for r in dataset['tables']['crm.sales_orders'] if r['tenant_id'] == tenant and r['customer_id'] == 1}
            amount = sum((Decimal(r['unit_price']) * r['quantity'] for r in dataset['tables']['crm.sales_order_items']
                          if r['tenant_id'] == tenant and r['order_id'] in expected_orders), Decimal('0.00'))
            customer = conn.execute(CUSTOMER, (1,)).fetchone()
            require(customer is not None and customer[2:] == (len(expected_orders), amount), 'customer amount and order count')
            balance = next(r for r in dataset['tables']['hcm.leave_balances'] if r['tenant_id'] == tenant and r['employee_id'] == 1)
            leave = conn.execute(LEAVE, (2026,)).fetchone()
            require(leave is not None and leave[4] == Decimal(balance['entitled_days']) - Decimal(balance['used_days']), 'leave arithmetic')
            expected_inventory = sorted((r['warehouse_id'], r['on_hand'] - r['reserved']) for r in dataset['tables']['scm.inventory']
                                        if r['tenant_id'] == tenant and r['product_id'] == 1)
            actual_inventory = [(r[2], r[6]) for r in conn.execute(INVENTORY, (1,))]
            require(actual_inventory == expected_inventory, 'inventory arithmetic')
            # Turn off convenience read-only setting: ACL must still deny writes.
            conn.execute('SET default_transaction_read_only = off')
            try:
                with conn.transaction():
                    conn.execute("UPDATE crm.customers SET name = name WHERE id = %s", (1,))
                    raise ValueError('Reader UPDATE was unexpectedly permitted')
            except errors.InsufficientPrivilege:
                pass
            try:
                with conn.transaction():
                    conn.execute('SELECT * FROM auth.reader_context')
                    raise ValueError('Reader accessed identity mapping')
            except errors.InsufficientPrivilege:
                pass
        checks.append(f'{reader}_isolation_privacy_queries_and_write_denial')
    # Prove composite foreign keys with a customer ID that exists only in tenant B.
    class RollbackProbe(Exception):
        pass
    with connect(settings) as admin:
        try:
            with admin.transaction():
                sentinel = admin.execute('SELECT max(id) + 1 FROM crm.customers').fetchone()[0]
                admin.execute('INSERT INTO crm.customers VALUES (%s, %s, %s, %s)', (2, sentinel, 'FK probe', 1))
                try:
                    with admin.transaction():
                        admin.execute('INSERT INTO crm.sales_orders VALUES (%s,%s,%s,%s,%s)',
                                      (1, 2147483647, sentinel, '2026-10-15', 'open'))
                        raise ValueError('Cross-tenant foreign key was accepted')
                except errors.ForeignKeyViolation:
                    pass
                raise RollbackProbe()
        except RollbackProbe:
            pass
        assert_matches(admin, dataset)
    checks.append('cross_tenant_foreign_key_and_probe_rollback')
    return {'status': 'passed', 'checks': checks, 'dataset_digest': dataset_digest(dataset)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('health')
    for name in ('load', 'verify'):
        sub = commands.add_parser(name)
        sub.add_argument('dataset', type=Path)
    sub = commands.add_parser('demo')
    sub.add_argument('--reader', choices=('a', 'b'), default='a')
    args = parser.parse_args()
    try:
        settings = load_settings()
        if args.command == 'health':
            result = health(settings)
        elif args.command == 'demo':
            result = demo(settings, args.reader)
        else:
            dataset = json.loads(args.dataset.read_text(encoding='utf-8'))
            result = (load_dataset if args.command == 'load' else verify)(settings, dataset)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=normalize))
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(1, f'{exc}\n')
    except ImportError:
        parser.exit(1, 'Install the db extra in the Ubuntu application environment: pip install -e ".[db]"\n')
    except Exception as exc:
        # DB error details can contain row values; keep public console output minimal.
        parser.exit(1, f'Database operation failed ({type(exc).__name__}); inspect private server logs.\n')


if __name__ == '__main__':
    main()
