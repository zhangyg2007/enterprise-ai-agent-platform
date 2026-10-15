-- V001: initial schema. Applied once by the image entrypoint on an empty volume.
\set ON_ERROR_STOP on
BEGIN;
CREATE EXTENSION IF NOT EXISTS vector;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA core;
CREATE SCHEMA hcm;
CREATE SCHEMA crm;
CREATE SCHEMA scm;
CREATE SCHEMA auth;
CREATE SCHEMA lab;

CREATE TABLE core.tenants (
    id integer PRIMARY KEY CHECK (id > 0), name text NOT NULL
);
CREATE TABLE hcm.departments (
    tenant_id integer REFERENCES core.tenants(id), id integer CHECK (id > 0),
    name text NOT NULL, PRIMARY KEY (tenant_id, id)
);
CREATE TABLE hcm.employees (
    tenant_id integer, id integer CHECK (id > 0), department_id integer NOT NULL,
    name text NOT NULL, email text NOT NULL,
    PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, email),
    FOREIGN KEY (tenant_id, department_id) REFERENCES hcm.departments(tenant_id, id)
);
CREATE TABLE hcm.leave_balances (
    tenant_id integer, employee_id integer, year integer CHECK (year BETWEEN 2000 AND 2100),
    entitled_days numeric(6,2) NOT NULL CHECK (entitled_days >= 0 AND entitled_days < 'Infinity'::numeric),
    used_days numeric(6,2) NOT NULL CHECK (used_days >= 0 AND used_days <= entitled_days),
    PRIMARY KEY (tenant_id, employee_id, year),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES hcm.employees(tenant_id, id)
);
CREATE TABLE hcm.leave_requests (
    tenant_id integer, id integer CHECK (id > 0), employee_id integer NOT NULL,
    start_date date NOT NULL, end_date date NOT NULL CHECK (end_date >= start_date),
    days numeric(6,2) NOT NULL CHECK (days > 0 AND days < 'Infinity'::numeric),
    status text NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, employee_id) REFERENCES hcm.employees(tenant_id, id)
);
CREATE TABLE scm.suppliers (
    tenant_id integer REFERENCES core.tenants(id), id integer CHECK (id > 0),
    name text NOT NULL, lead_days integer NOT NULL CHECK (lead_days BETWEEN 1 AND 365),
    PRIMARY KEY (tenant_id, id)
);
CREATE TABLE scm.products (
    tenant_id integer, id integer CHECK (id > 0), supplier_id integer NOT NULL,
    sku text NOT NULL, name text NOT NULL, unit text NOT NULL,
    unit_price numeric(12,2) NOT NULL CHECK (unit_price > 0 AND unit_price < 'Infinity'::numeric),
    PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, sku),
    FOREIGN KEY (tenant_id, supplier_id) REFERENCES scm.suppliers(tenant_id, id)
);
CREATE TABLE scm.warehouses (
    tenant_id integer REFERENCES core.tenants(id), id integer CHECK (id > 0),
    name text NOT NULL, PRIMARY KEY (tenant_id, id)
);
CREATE TABLE scm.inventory (
    tenant_id integer, warehouse_id integer, product_id integer,
    on_hand integer NOT NULL CHECK (on_hand >= 0),
    reserved integer NOT NULL CHECK (reserved >= 0 AND reserved <= on_hand),
    reorder_point integer NOT NULL CHECK (reorder_point >= 0),
    PRIMARY KEY (tenant_id, warehouse_id, product_id),
    FOREIGN KEY (tenant_id, warehouse_id) REFERENCES scm.warehouses(tenant_id, id),
    FOREIGN KEY (tenant_id, product_id) REFERENCES scm.products(tenant_id, id)
);
CREATE TABLE crm.customers (
    tenant_id integer, id integer CHECK (id > 0), name text NOT NULL,
    owner_employee_id integer NOT NULL,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, owner_employee_id) REFERENCES hcm.employees(tenant_id, id)
);
CREATE TABLE crm.sales_orders (
    tenant_id integer, id integer CHECK (id > 0), customer_id integer NOT NULL,
    order_date date NOT NULL, status text NOT NULL CHECK (status IN ('open', 'completed')),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, customer_id) REFERENCES crm.customers(tenant_id, id)
);
CREATE TABLE crm.sales_order_items (
    tenant_id integer, order_id integer, line_no integer CHECK (line_no > 0),
    product_id integer NOT NULL, quantity integer NOT NULL CHECK (quantity > 0),
    unit_price numeric(12,2) NOT NULL CHECK (unit_price > 0 AND unit_price < 'Infinity'::numeric),
    PRIMARY KEY (tenant_id, order_id, line_no),
    FOREIGN KEY (tenant_id, order_id) REFERENCES crm.sales_orders(tenant_id, id),
    FOREIGN KEY (tenant_id, product_id) REFERENCES scm.products(tenant_id, id)
);
CREATE TABLE scm.purchase_orders (
    tenant_id integer, id integer CHECK (id > 0), supplier_id integer NOT NULL,
    expected_date date NOT NULL, status text NOT NULL CHECK (status IN ('ordered', 'received')),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, supplier_id) REFERENCES scm.suppliers(tenant_id, id)
);
CREATE TABLE scm.purchase_order_items (
    tenant_id integer, order_id integer, line_no integer CHECK (line_no > 0),
    product_id integer NOT NULL, quantity integer NOT NULL CHECK (quantity > 0),
    unit_cost numeric(12,2) NOT NULL CHECK (unit_cost > 0 AND unit_cost < 'Infinity'::numeric),
    PRIMARY KEY (tenant_id, order_id, line_no),
    FOREIGN KEY (tenant_id, order_id) REFERENCES scm.purchase_orders(tenant_id, id),
    FOREIGN KEY (tenant_id, product_id) REFERENCES scm.products(tenant_id, id)
);

CREATE INDEX ON crm.sales_orders (tenant_id, customer_id, order_date);
CREATE INDEX ON hcm.leave_requests (tenant_id, employee_id, status);
CREATE INDEX ON scm.inventory (tenant_id, product_id);
CREATE INDEX ON scm.purchase_orders (tenant_id, supplier_id, status);

-- Mapping is maintained only by the migration administrator, never by a model.
CREATE TABLE auth.reader_context (
    db_role name PRIMARY KEY, tenant_id integer NOT NULL, employee_id integer NOT NULL
);
INSERT INTO auth.reader_context VALUES ('lab_a_reader', 1, 1), ('lab_b_reader', 2, 1);
CREATE FUNCTION auth.current_tenant() RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, auth
AS $$ SELECT tenant_id FROM auth.reader_context WHERE db_role = session_user $$;
CREATE FUNCTION auth.current_employee() RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, auth
AS $$ SELECT employee_id FROM auth.reader_context WHERE db_role = session_user $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA auth FROM PUBLIC;
GRANT USAGE ON SCHEMA auth, core, hcm, crm, scm TO lab_a_reader, lab_b_reader;
GRANT EXECUTE ON FUNCTION auth.current_tenant(), auth.current_employee()
    TO lab_a_reader, lab_b_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA core, hcm, crm, scm TO lab_a_reader, lab_b_reader;

ALTER TABLE core.tenants ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_read ON core.tenants FOR SELECT TO lab_a_reader, lab_b_reader
    USING (id = auth.current_tenant());
DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'hcm.departments','hcm.employees','hcm.leave_balances','hcm.leave_requests',
        'scm.suppliers','scm.products','scm.warehouses','scm.inventory',
        'crm.customers','crm.sales_orders','crm.sales_order_items',
        'scm.purchase_orders','scm.purchase_order_items'
    ] LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('CREATE POLICY tenant_read ON %s FOR SELECT TO lab_a_reader, lab_b_reader USING (tenant_id = auth.current_tenant())', table_name);
    END LOOP;
END $$;
CREATE POLICY employee_self ON hcm.employees AS RESTRICTIVE FOR SELECT
    TO lab_a_reader, lab_b_reader USING (id = auth.current_employee());
CREATE POLICY balance_self ON hcm.leave_balances AS RESTRICTIVE FOR SELECT
    TO lab_a_reader, lab_b_reader USING (employee_id = auth.current_employee());
CREATE POLICY request_self ON hcm.leave_requests AS RESTRICTIVE FOR SELECT
    TO lab_a_reader, lab_b_reader USING (employee_id = auth.current_employee());

CREATE TABLE lab.schema_versions (version integer PRIMARY KEY, applied_at timestamptz DEFAULT now());
INSERT INTO lab.schema_versions(version) VALUES (1);
CREATE TABLE lab.seed_runs (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    digest text NOT NULL, profile text NOT NULL, counts jsonb NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON SCHEMA auth IS 'Teaching identity mapping. Never grant readers access to reader_context.';
COMMENT ON TABLE scm.inventory IS 'Snapshot only in M02; movement ledger and write workflows arrive in M11.';
COMMIT;
