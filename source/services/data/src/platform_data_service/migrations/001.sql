CREATE TABLE schema_version(version integer PRIMARY KEY);
INSERT INTO schema_version VALUES (4);
CREATE TABLE publications (
 id text PRIMARY KEY, project text NOT NULL, owner_subject text NOT NULL,
 publication_kind text NOT NULL CHECK(publication_kind IN ('USER_UPLOAD','MANAGED')),
 idem text NOT NULL, digest text NOT NULL,
 manifest jsonb NOT NULL, state text NOT NULL CHECK(state IN ('OPEN','PREPARING','READY','PUBLISHED','FAILED','ABORTED')),
 prepared jsonb, receipt jsonb, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(project,owner_subject,idem)
);
CREATE TABLE uploads (
 publication_id text REFERENCES publications(id), name text NOT NULL, object jsonb NOT NULL,
 PRIMARY KEY(publication_id,name)
);
CREATE TABLE datasets (
 id text PRIMARY KEY, project text NOT NULL, title text NOT NULL, tags jsonb NOT NULL,
 current_version text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE versions (
 id text PRIMARY KEY, dataset_id text NOT NULL REFERENCES datasets(id), publication_id text NOT NULL REFERENCES publications(id),
 descriptor jsonb NOT NULL, semantic_id text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE objects (
 id text PRIMARY KEY, version_id text NOT NULL REFERENCES versions(id), descriptor jsonb NOT NULL
);
CREATE TABLE semantic_revisions (
 id text PRIMARY KEY, version_id text NOT NULL REFERENCES versions(id), body jsonb NOT NULL,
 actor jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE lineage (
 version_id text REFERENCES versions(id), input_version_id text REFERENCES versions(id),
 PRIMARY KEY(version_id,input_version_id)
);
CREATE TABLE metrics (
 version_id text REFERENCES versions(id), name text NOT NULL, definition text NOT NULL,
 unit text NOT NULL, evaluation_scope text NOT NULL, value double precision NOT NULL,
 PRIMARY KEY(version_id,name)
);
CREATE TABLE operations (
 id text PRIMARY KEY, project text NOT NULL, kind text NOT NULL CHECK(kind IN ('prepare','query')),
 target text NOT NULL, payload jsonb NOT NULL, state text NOT NULL CHECK(state IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')),
 generation integer NOT NULL DEFAULT 0, lease_until timestamptz, result jsonb, error jsonb,
 actor jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX operations_queue ON operations(state,created_at);
CREATE TABLE operation_events (
 seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, operation_id text REFERENCES operations(id),
 state text NOT NULL, generation integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE queries (
 id text PRIMARY KEY REFERENCES operations(id), project text NOT NULL, owner_subject text NOT NULL,
 idem text NOT NULL, digest text NOT NULL, plan jsonb NOT NULL, receipt jsonb,
 result_object jsonb, expires_at timestamptz, UNIQUE(project,owner_subject,idem)
);
CREATE INDEX datasets_project ON datasets(project,id);
CREATE INDEX versions_dataset ON versions(dataset_id,created_at);
CREATE TABLE publication_events (
 seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 publication_id text NOT NULL REFERENCES publications(id), state text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION record_publication_state() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'INSERT' OR NEW.state IS DISTINCT FROM OLD.state THEN
  INSERT INTO publication_events(publication_id,state) VALUES (NEW.id,NEW.state);
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER publication_state_event AFTER INSERT OR UPDATE OF state ON publications
 FOR EACH ROW EXECUTE FUNCTION record_publication_state();
ALTER TABLE operations DROP CONSTRAINT operations_kind_check;
ALTER TABLE operations ADD CONSTRAINT operations_kind_check
 CHECK(kind IN ('prepare','query','preview'));

CREATE TABLE previews (
 id text PRIMARY KEY REFERENCES operations(id),
 project text NOT NULL,
 owner_subject text NOT NULL,
 idem text NOT NULL,
 digest text NOT NULL,
 plan jsonb NOT NULL,
 receipt jsonb,
 result_object jsonb,
 expires_at timestamptz,
 UNIQUE(project,owner_subject,idem)
);
