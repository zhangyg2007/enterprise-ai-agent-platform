-- Ubuntu administrator only, after M01. Existing schema makes reruns fail safely.
BEGIN;
CREATE SCHEMA rag_lab;
REVOKE ALL ON SCHEMA rag_lab FROM PUBLIC;
CREATE TABLE rag_lab.collections (
    collection_id text PRIMARY KEY CHECK (collection_id ~ '^[a-z][a-z0-9_-]{0,63}$'),
    tenant_id bigint NOT NULL CHECK (tenant_id > 0),
    generation bigint NOT NULL CHECK (generation > 0),
    digest text NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
    model jsonb NOT NULL,
    UNIQUE (collection_id, tenant_id)
);
CREATE TABLE rag_lab.chunks (
    collection_id text NOT NULL,
    tenant_id bigint NOT NULL,
    chunk_id text NOT NULL,
    roles text[] NOT NULL CHECK (cardinality(roles) > 0),
    effective_from date NOT NULL,
    effective_to date CHECK (effective_to > effective_from),
    payload jsonb NOT NULL,
    embedding vector(1024) NOT NULL,
    PRIMARY KEY (collection_id, chunk_id),
    FOREIGN KEY (collection_id, tenant_id) REFERENCES rag_lab.collections(collection_id, tenant_id)
);
CREATE INDEX m09_scope ON rag_lab.chunks (collection_id, tenant_id);
CREATE INDEX m09_embedding_hnsw ON rag_lab.chunks USING hnsw (embedding vector_cosine_ops);
CREATE TABLE rag_lab.reader_roles (
    db_role name PRIMARY KEY,
    roles text[] NOT NULL CHECK (cardinality(roles)>0 AND roles <@ ARRAY['employee','sales','hr','procurement']::text[])
);
INSERT INTO rag_lab.reader_roles VALUES ('lab_a_reader',ARRAY['sales']),('lab_b_reader',ARRAY['sales']);
CREATE FUNCTION rag_lab.current_roles() RETURNS text[]
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rag_lab
AS $$ SELECT roles FROM rag_lab.reader_roles WHERE db_role = session_user $$;
REVOKE ALL ON ALL TABLES IN SCHEMA rag_lab FROM PUBLIC;
REVOKE ALL ON FUNCTION rag_lab.current_roles() FROM PUBLIC;
GRANT USAGE ON SCHEMA rag_lab TO lab_a_reader,lab_b_reader;
GRANT EXECUTE ON FUNCTION rag_lab.current_roles() TO lab_a_reader,lab_b_reader;
GRANT SELECT ON rag_lab.collections,rag_lab.chunks TO lab_a_reader,lab_b_reader;
ALTER TABLE rag_lab.collections ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_lab.chunks ENABLE ROW LEVEL SECURITY;
CREATE POLICY m09_collections_read ON rag_lab.collections FOR SELECT TO lab_a_reader,lab_b_reader
USING (tenant_id=auth.current_tenant());
CREATE POLICY m09_chunks_read ON rag_lab.chunks FOR SELECT TO lab_a_reader,lab_b_reader
USING (tenant_id=auth.current_tenant() AND roles && rag_lab.current_roles());
COMMENT ON SCHEMA rag_lab IS 'M09 teaching index; reader identity comes from session_user mappings, never a model.';
COMMIT;
