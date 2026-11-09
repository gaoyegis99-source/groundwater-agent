-- A manual retry starts a new bounded cycle without erasing lifetime attempts.
ALTER TABLE imports DROP CONSTRAINT imports_data_attempts_check;
ALTER TABLE imports ADD CONSTRAINT imports_data_attempts_check
 CHECK (data_attempts >= 0);
ALTER TABLE imports
 ADD COLUMN retry_generation integer NOT NULL DEFAULT 0 CHECK (retry_generation >= 0),
 ADD COLUMN retry_base_attempt integer NOT NULL DEFAULT 0,
 ADD COLUMN data_retry_base_attempt integer NOT NULL DEFAULT 0,
 ADD CONSTRAINT imports_retry_bases_check CHECK (
   retry_base_attempt >= 0 AND retry_base_attempt <= attempt
   AND data_retry_base_attempt >= 0 AND data_retry_base_attempt <= data_attempts
 );
CREATE TABLE import_manual_retries (
 request_id text PRIMARY KEY,
 import_id text NOT NULL REFERENCES imports(id),
 retry_generation integer NOT NULL CHECK (retry_generation > 0),
 from_attempt integer NOT NULL CHECK (from_attempt > 0),
 actor jsonb NOT NULL,
 failure jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (import_id, retry_generation)
);
CREATE INDEX import_manual_retries_import ON import_manual_retries(import_id, created_at);
