ALTER TABLE model_registry_versions
  ADD COLUMN benchmark_status text NOT NULL DEFAULT 'NOT_ASSESSED'
    CHECK(benchmark_status IN ('NOT_ASSESSED','PASSED','FAILED')),
  ADD COLUMN tags jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE model_registry_versions RENAME COLUMN stage TO legacy_stage;
ALTER TABLE model_registry_versions ALTER COLUMN legacy_stage DROP NOT NULL;
ALTER TABLE model_registry_versions
  DROP CONSTRAINT model_registry_versions_stage_check;
DROP TABLE resource_access_revisions;
DROP TABLE resource_grants;
ALTER TABLE datasets DROP COLUMN access_revision;
UPDATE schema_version SET version=8;
