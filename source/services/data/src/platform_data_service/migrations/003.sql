CREATE TABLE quality_suites (
  project text NOT NULL,
  suite_id text NOT NULL,
  title text NOT NULL,
  owner_subject text NOT NULL,
  latest_version_id text,
  latest_version_number integer NOT NULL DEFAULT 0 CHECK(latest_version_number >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(project,suite_id)
);

CREATE TABLE quality_suite_versions (
  id text PRIMARY KEY,
  project text NOT NULL,
  suite_id text NOT NULL,
  version_number integer NOT NULL CHECK(version_number > 0),
  title text NOT NULL,
  rules jsonb NOT NULL,
  body_digest text NOT NULL,
  actor jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(project,suite_id) REFERENCES quality_suites(project,suite_id),
  UNIQUE(project,suite_id,version_number),
  UNIQUE(project,suite_id,body_digest)
);

ALTER TABLE quality_suites
  ADD CONSTRAINT quality_suites_latest_version_fk
  FOREIGN KEY(latest_version_id) REFERENCES quality_suite_versions(id);

CREATE TABLE quality_bindings (
  project text NOT NULL,
  dataset_id text NOT NULL REFERENCES datasets(id),
  revision integer NOT NULL CHECK(revision > 0),
  suite_version_id text NOT NULL REFERENCES quality_suite_versions(id),
  enabled boolean NOT NULL,
  actor jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(project,dataset_id,revision)
);
CREATE INDEX quality_bindings_current
  ON quality_bindings(project,dataset_id,revision DESC);

CREATE TABLE quality_runs (
  id text PRIMARY KEY,
  project text NOT NULL,
  dataset_id text NOT NULL REFERENCES datasets(id),
  version_id text NOT NULL REFERENCES versions(id),
  semantic_id text NOT NULL REFERENCES semantic_revisions(id),
  binding_revision integer NOT NULL,
  suite_version_id text NOT NULL REFERENCES quality_suite_versions(id),
  idempotency_key text NOT NULL,
  request_digest text NOT NULL,
  operator_run_id text NOT NULL,
  operator_id text NOT NULL CHECK(operator_id='table.validate'),
  operator_version text NOT NULL CHECK(operator_version='1.0.0'),
  implementation_digest text NOT NULL,
  status text NOT NULL CHECK(status IN ('PASSED','PASSED_WITH_WARNINGS','FAILED','ERROR')),
  summary jsonb NOT NULL,
  actor jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(project,dataset_id,binding_revision)
    REFERENCES quality_bindings(project,dataset_id,revision),
  UNIQUE(project,idempotency_key)
);
CREATE INDEX quality_runs_dataset
  ON quality_runs(project,dataset_id,created_at DESC,id DESC);

CREATE TABLE quality_rule_results (
  run_id text NOT NULL REFERENCES quality_runs(id),
  rule_id text NOT NULL,
  severity text NOT NULL CHECK(severity IN ('ERROR','WARN','INFO')),
  status text NOT NULL CHECK(status IN ('PASS','FAIL','ERROR')),
  violation_count bigint NOT NULL CHECK(violation_count >= 0),
  code text,
  evidence jsonb NOT NULL,
  PRIMARY KEY(run_id,rule_id)
);

CREATE TABLE quality_alerts (
  id text PRIMARY KEY,
  project text NOT NULL,
  dataset_id text NOT NULL REFERENCES datasets(id),
  version_id text NOT NULL REFERENCES versions(id),
  run_id text NOT NULL REFERENCES quality_runs(id),
  rule_id text NOT NULL,
  severity text NOT NULL CHECK(severity IN ('ERROR','WARN','INFO')),
  status text NOT NULL CHECK(status IN ('FAIL','ERROR')),
  violation_count bigint NOT NULL CHECK(violation_count >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(run_id,rule_id)
);
CREATE INDEX quality_alerts_dataset
  ON quality_alerts(project,dataset_id,created_at DESC,id DESC);

CREATE TABLE outbox_events (
  id text PRIMARY KEY,
  project text NOT NULL,
  topic text NOT NULL,
  aggregate_type text NOT NULL,
  aggregate_id text NOT NULL,
  event_type text NOT NULL,
  payload jsonb NOT NULL,
  idempotency_key text NOT NULL UNIQUE,
  state text NOT NULL DEFAULT 'PENDING'
    CHECK(state IN ('PENDING','DELIVERING','DELIVERED','FAILED')),
  attempts integer NOT NULL DEFAULT 0 CHECK(attempts >= 0),
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  locked_until timestamptz,
  delivered_at timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX outbox_events_delivery
  ON outbox_events(state,next_attempt_at,created_at,id);

UPDATE schema_version SET version=6;
