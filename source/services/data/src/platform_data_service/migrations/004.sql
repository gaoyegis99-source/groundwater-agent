CREATE TABLE ml_experiments (
  id text PRIMARY KEY,
  project text NOT NULL,
  name text NOT NULL,
  description text NOT NULL,
  owner_subject text NOT NULL,
  actor jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(project,name)
);

CREATE TABLE ml_runs (
  id text PRIMARY KEY,
  project text NOT NULL,
  experiment_id text NOT NULL REFERENCES ml_experiments(id),
  idempotency_key text NOT NULL,
  request_digest text NOT NULL,
  input_versions jsonb NOT NULL,
  resolved_model_inputs jsonb NOT NULL,
  parameters jsonb NOT NULL,
  metrics jsonb NOT NULL,
  environment jsonb NOT NULL,
  operator_implementations jsonb NOT NULL,
  actor jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(project,idempotency_key)
);
CREATE INDEX ml_runs_experiment
  ON ml_runs(project,experiment_id,created_at DESC,id DESC);

CREATE TABLE model_registry_versions (
  project text NOT NULL,
  model_id text NOT NULL REFERENCES datasets(id),
  registry_version integer NOT NULL CHECK(registry_version > 0),
  data_version_id text NOT NULL REFERENCES versions(id),
  artifact_object_id text NOT NULL REFERENCES objects(id),
  artifact_checksum text NOT NULL,
  experiment_run_id text REFERENCES ml_runs(id),
  stage text NOT NULL CHECK(stage IN ('CANDIDATE','VALIDATED','APPROVED','PRODUCTION')),
  actor jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(project,model_id,registry_version),
  UNIQUE(project,model_id,data_version_id)
);

CREATE TABLE model_stage_events (
  model_id text NOT NULL,
  registry_version integer NOT NULL,
  sequence integer NOT NULL CHECK(sequence > 0),
  from_stage text,
  to_stage text NOT NULL CHECK(to_stage IN ('CANDIDATE','VALIDATED','APPROVED','PRODUCTION')),
  actor jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(model_id,registry_version,sequence)
);

CREATE TABLE model_alias_revisions (
  project text NOT NULL,
  model_id text NOT NULL,
  alias text NOT NULL,
  revision integer NOT NULL CHECK(revision > 0),
  registry_version integer NOT NULL,
  actor jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(project,model_id,alias,revision),
  FOREIGN KEY(project,model_id,registry_version)
    REFERENCES model_registry_versions(project,model_id,registry_version)
);
CREATE INDEX model_alias_current
  ON model_alias_revisions(project,model_id,alias,revision DESC);

UPDATE schema_version SET version=7;
