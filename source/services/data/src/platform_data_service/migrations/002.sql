ALTER TABLE datasets
  ADD COLUMN owner_subject text,
  ADD COLUMN asset_kind text NOT NULL DEFAULT 'DATASET'
    CHECK(asset_kind IN ('DATASET','MODEL')),
  ADD COLUMN visibility text NOT NULL DEFAULT 'PROJECT'
    CHECK(visibility IN ('PRIVATE','PROJECT')),
  ADD COLUMN access_revision integer NOT NULL DEFAULT 1
    CHECK(access_revision > 0);

UPDATE datasets AS dataset
SET owner_subject=source.owner_subject
FROM (
  SELECT DISTINCT ON (version.dataset_id)
         version.dataset_id,publication.owner_subject
  FROM versions AS version
  JOIN publications AS publication ON publication.id=version.publication_id
  ORDER BY version.dataset_id,version.created_at,version.id
) AS source
WHERE source.dataset_id=dataset.id;

UPDATE datasets AS dataset
SET asset_kind='MODEL'
WHERE EXISTS (
  SELECT 1 FROM objects AS object
  WHERE object.version_id=dataset.current_version
    AND object.descriptor->>'format'='pytorch-state-dict'
);

ALTER TABLE datasets ALTER COLUMN owner_subject SET NOT NULL;

CREATE TABLE resource_grants (
  project text NOT NULL,
  resource_kind text NOT NULL CHECK(resource_kind IN ('DATASET','MODEL')),
  resource_id text NOT NULL,
  principal_type text NOT NULL CHECK(principal_type IN ('SUBJECT','ROLE')),
  principal text NOT NULL,
  permission text NOT NULL CHECK(permission IN ('READ','WRITE','ADMIN')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(project,resource_kind,resource_id,principal_type,principal)
);
CREATE INDEX resource_grants_resource
  ON resource_grants(project,resource_kind,resource_id);

CREATE TABLE resource_access_revisions (
  resource_kind text NOT NULL CHECK(resource_kind IN ('DATASET','MODEL')),
  resource_id text NOT NULL,
  revision integer NOT NULL CHECK(revision > 0),
  body jsonb NOT NULL,
  actor jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(resource_kind,resource_id,revision)
);

INSERT INTO resource_access_revisions(resource_kind,resource_id,revision,body,actor)
SELECT asset_kind,id,1,
       jsonb_build_object(
         'ownerSubject',owner_subject,
         'visibility',visibility,
         'grants','[]'::jsonb
       ),
       '{"subject":"system:migration","roles":["admin"]}'::jsonb
FROM datasets;

UPDATE schema_version SET version=5;
