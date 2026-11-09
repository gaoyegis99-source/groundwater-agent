CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE meta (singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), schema_version integer NOT NULL, project text NOT NULL, revision bigint NOT NULL DEFAULT 0, profile text NOT NULL);
CREATE TABLE imports (
 id text PRIMARY KEY, idempotency_key text UNIQUE NOT NULL, digest text NOT NULL,
 payload jsonb NOT NULL, actor jsonb NOT NULL,
 state text NOT NULL CHECK(state IN ('QUEUED','VALIDATING','INDEXING','SUCCEEDED','FAILED')),
 attempt integer NOT NULL DEFAULT 0, lease_until timestamptz, error jsonb, result jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE documents (id text PRIMARY KEY, current_version text);
CREATE TABLE versions (
 id text PRIMARY KEY, document_id text NOT NULL REFERENCES documents(id), body jsonb NOT NULL,
 checksum text NOT NULL, import_id text NOT NULL REFERENCES imports(id), verified_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE documents ADD FOREIGN KEY(current_version) REFERENCES versions(id);
CREATE TABLE lifecycle (
 version_id text NOT NULL REFERENCES versions(id), revision integer NOT NULL,
 status text NOT NULL CHECK(status IN ('ACTIVE','WITHDRAWN')), valid_through date,
 review_after date NOT NULL, reason text NOT NULL, actor jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(version_id,revision)
);
CREATE TABLE units (
 version_id text NOT NULL REFERENCES versions(id), unit_id text NOT NULL, ordinal integer NOT NULL,
 checksum text NOT NULL, body jsonb NOT NULL, PRIMARY KEY(version_id,unit_id)
);
CREATE TABLE embeddings (input_hash text PRIMARY KEY, embedding vector(1024) NOT NULL);
CREATE TABLE search_entries (
 version_id text NOT NULL, unit_id text NOT NULL, document_id text NOT NULL,
 ordinal integer NOT NULL, lexical tsvector NOT NULL, embedding vector(1024) NOT NULL,
 kind text NOT NULL, keys text[] NOT NULL, bindings jsonb NOT NULL,
 valid_through date, PRIMARY KEY(version_id,unit_id),
 FOREIGN KEY(version_id,unit_id) REFERENCES units(version_id,unit_id)
);
CREATE INDEX search_lexical ON search_entries USING gin(lexical);
CREATE INDEX search_keys ON search_entries USING gin(keys);
CREATE INDEX search_bindings ON search_entries USING gin(bindings);
CREATE INDEX search_vectors ON search_entries USING hnsw(embedding vector_cosine_ops);
CREATE TABLE receipts (
 id text PRIMARY KEY, request_hash text NOT NULL, kind text NOT NULL, body jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX versions_document ON versions(document_id,created_at);
ALTER TABLE imports DROP CONSTRAINT imports_state_check;
ALTER TABLE imports ADD CONSTRAINT imports_state_check
 CHECK(state IN ('QUEUED','VALIDATING','INDEXING','RETRY_WAIT','SUCCEEDED','FAILED'));
ALTER TABLE imports
 ADD COLUMN data_attempts integer NOT NULL DEFAULT 0 CHECK(data_attempts BETWEEN 0 AND 5),
 ADD COLUMN next_retry_at timestamptz,
 ADD COLUMN retry_deadline timestamptz,
 ADD COLUMN last_error jsonb;
