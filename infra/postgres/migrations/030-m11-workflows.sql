-- m11_migrator, only in a NEW independent enterprise_workflows[_suffix] database.
-- No IF NOT EXISTS: a rerun/conflicting schema fails rather than patching old data.
BEGIN;
DO $$ BEGIN
 IF current_database() !~ '^enterprise_workflows(_[a-z0-9]+)?$'
    OR current_user <> 'm11_migrator'
    OR EXISTS(SELECT 1 FROM pg_namespace WHERE nspname IN ('crm','hcm','scm')) THEN
   RAISE EXCEPTION 'Use the dedicated new workflow database and migration identity';
 END IF;
END $$;
DO $$ BEGIN
 EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC',current_database());
 EXECUTE format('GRANT CONNECT ON DATABASE %I TO m11_runtime,m11_cp',current_database());
END $$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA workflow_auth;
CREATE SCHEMA workflow_lab;
CREATE SCHEMA workflow_cp;
REVOKE ALL ON SCHEMA workflow_auth,workflow_lab,workflow_cp FROM PUBLIC;
CREATE TABLE workflow_auth.identities(
 db_role name PRIMARY KEY,tenant_id integer NOT NULL CHECK(tenant_id>0),
 user_id integer NOT NULL CHECK(user_id>0),employee_id integer NOT NULL CHECK(employee_id>0),
 role text NOT NULL CHECK(role IN ('sales','employee','procurement','manager')));
INSERT INTO workflow_auth.identities VALUES
 ('m11_a_sales',1,1,1,'sales'),('m11_a_employee',1,2,1,'employee'),
 ('m11_a_procurement',1,3,3,'procurement'),('m11_a_manager',1,4,4,'manager'),
 ('m11_b_sales',2,1,1,'sales'),('m11_b_employee',2,2,1,'employee'),
 ('m11_b_procurement',2,3,3,'procurement'),('m11_b_manager',2,4,4,'manager');
CREATE FUNCTION workflow_auth.context()
RETURNS TABLE(tenant_id integer,user_id integer,employee_id integer,role text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $$ SELECT tenant_id,user_id,employee_id,role FROM workflow_auth.identities
      WHERE db_role=session_user $$;
REVOKE ALL ON FUNCTION workflow_auth.context() FROM PUBLIC;
GRANT USAGE ON SCHEMA workflow_auth TO m11_runtime;
GRANT EXECUTE ON FUNCTION workflow_auth.context() TO m11_runtime;

CREATE TABLE workflow_lab.metadata(slot integer PRIMARY KEY CHECK(slot=1),lab_id text NOT NULL,
 format_version integer NOT NULL CHECK(format_version=1));
INSERT INTO workflow_lab.metadata VALUES(1,md5(random()::text||clock_timestamp()::text),1);
CREATE TABLE workflow_lab.seeds(tenant integer PRIMARY KEY,digest text NOT NULL,source text NOT NULL);
CREATE TABLE workflow_lab.resources(tenant integer,kind text,id integer,revision integer NOT NULL,
 value text NOT NULL,PRIMARY KEY(tenant,kind,id));
CREATE TABLE workflow_lab.customers(tenant integer,id integer,PRIMARY KEY(tenant,id));
CREATE TABLE workflow_lab.requests(tenant integer,request_id text,owner integer NOT NULL,
 case_name text NOT NULL,idem_key text NOT NULL,input_hash text NOT NULL,kind text NOT NULL,
 resource_id integer NOT NULL,snapshot text NOT NULL,plan text NOT NULL,
 status text NOT NULL CHECK(status IN ('pending','approved','rejected','executed','conflict')),
 approver integer,PRIMARY KEY(tenant,request_id),UNIQUE(tenant,idem_key),
 FOREIGN KEY(tenant,kind,resource_id) REFERENCES workflow_lab.resources(tenant,kind,id));
CREATE TABLE workflow_lab.effects(tenant integer,request_id text,receipt text NOT NULL,
 PRIMARY KEY(tenant,request_id),FOREIGN KEY(tenant,request_id) REFERENCES workflow_lab.requests(tenant,request_id));
CREATE TABLE workflow_lab.audit(sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 tenant integer,request_id text,user_id integer,event text,
 FOREIGN KEY(tenant,request_id) REFERENCES workflow_lab.requests(tenant,request_id));
CREATE TABLE workflow_lab.threads(tenant integer,thread_id text,owner integer NOT NULL,
 request_id text NOT NULL,binding text NOT NULL,PRIMARY KEY(tenant,thread_id),UNIQUE(tenant,request_id),
 FOREIGN KEY(tenant,request_id) REFERENCES workflow_lab.requests(tenant,request_id));

GRANT USAGE ON SCHEMA workflow_lab TO m11_runtime,m11_cp;
GRANT SELECT ON workflow_lab.metadata TO m11_runtime,m11_cp;
GRANT SELECT ON workflow_lab.resources,workflow_lab.customers,workflow_lab.requests,
 workflow_lab.effects,workflow_lab.threads TO m11_runtime;
GRANT INSERT ON workflow_lab.requests,workflow_lab.effects,workflow_lab.audit,workflow_lab.threads TO m11_runtime;
GRANT UPDATE(status,approver) ON workflow_lab.requests TO m11_runtime;
GRANT UPDATE(revision,value) ON workflow_lab.resources TO m11_runtime;
GRANT USAGE ON SEQUENCE workflow_lab.audit_sequence_seq TO m11_runtime;
GRANT USAGE ON SCHEMA workflow_cp TO m11_cp;
ALTER DEFAULT PRIVILEGES IN SCHEMA workflow_cp
 GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO m11_cp;

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['resources','customers','requests','effects','audit','threads'] LOOP
  EXECUTE format('ALTER TABLE workflow_lab.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant_scope ON workflow_lab.%I AS RESTRICTIVE TO m11_runtime '
    'USING (tenant=(SELECT tenant_id FROM workflow_auth.context())) '
    'WITH CHECK (tenant=(SELECT tenant_id FROM workflow_auth.context()))',t);
 END LOOP;
END $$;
CREATE POLICY resource_read ON workflow_lab.resources FOR SELECT TO m11_runtime USING(
 (SELECT role FROM workflow_auth.context())='manager' OR
 kind='catalog' AND (SELECT role FROM workflow_auth.context())='sales' OR
 kind='stock' AND (SELECT role FROM workflow_auth.context())='procurement' OR
 kind='leave' AND (SELECT role FROM workflow_auth.context())='employee'
   AND id=(SELECT employee_id FROM workflow_auth.context()));
CREATE POLICY resource_write ON workflow_lab.resources FOR UPDATE TO m11_runtime
 USING((SELECT role FROM workflow_auth.context())='manager')
 WITH CHECK((SELECT role FROM workflow_auth.context())='manager');
CREATE POLICY customer_read ON workflow_lab.customers FOR SELECT TO m11_runtime
 USING((SELECT role FROM workflow_auth.context()) IN ('sales','manager'));
CREATE POLICY request_read ON workflow_lab.requests FOR SELECT TO m11_runtime
 USING(owner=(SELECT user_id FROM workflow_auth.context()) OR (SELECT role FROM workflow_auth.context())='manager');
CREATE POLICY request_draft ON workflow_lab.requests FOR INSERT TO m11_runtime
 WITH CHECK(owner=(SELECT user_id FROM workflow_auth.context()) AND status='pending' AND approver IS NULL
  AND (case_name='quote' AND (SELECT role FROM workflow_auth.context())='sales'
    OR case_name='leave' AND (SELECT role FROM workflow_auth.context())='employee'
    OR case_name='replenish' AND (SELECT role FROM workflow_auth.context())='procurement'));
CREATE POLICY request_decide ON workflow_lab.requests FOR UPDATE TO m11_runtime
 USING((SELECT role FROM workflow_auth.context())='manager' AND owner<>(SELECT user_id FROM workflow_auth.context()))
 WITH CHECK((SELECT role FROM workflow_auth.context())='manager' AND owner<>(SELECT user_id FROM workflow_auth.context()));
CREATE POLICY effect_read ON workflow_lab.effects FOR SELECT TO m11_runtime
 USING((SELECT role FROM workflow_auth.context())='manager');
CREATE POLICY effect_write ON workflow_lab.effects FOR INSERT TO m11_runtime
 WITH CHECK((SELECT role FROM workflow_auth.context())='manager');
CREATE POLICY audit_append ON workflow_lab.audit FOR INSERT TO m11_runtime
 WITH CHECK(user_id=(SELECT user_id FROM workflow_auth.context()));
CREATE POLICY thread_read ON workflow_lab.threads FOR SELECT TO m11_runtime
 USING(owner=(SELECT user_id FROM workflow_auth.context()) OR (SELECT role FROM workflow_auth.context())='manager');
CREATE POLICY thread_start ON workflow_lab.threads FOR INSERT TO m11_runtime
 WITH CHECK(owner=(SELECT user_id FROM workflow_auth.context()) AND
  EXISTS(SELECT 1 FROM workflow_lab.requests r WHERE r.tenant=threads.tenant
   AND r.request_id=threads.request_id AND r.owner=threads.owner));
COMMIT;
