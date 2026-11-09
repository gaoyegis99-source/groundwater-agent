ALTER TABLE documents
  ADD COLUMN owner_subject text,
  ADD COLUMN visibility text NOT NULL DEFAULT 'PROJECT'
    CHECK(visibility IN ('PRIVATE','PROJECT')),
  ADD COLUMN access_revision integer NOT NULL DEFAULT 1
    CHECK(access_revision > 0);

UPDATE documents AS document
SET owner_subject=source.owner_subject
FROM (
  SELECT DISTINCT ON (version.document_id)
         version.document_id,
         COALESCE(import.actor->>'subject','platform-knowledge-bootstrap') AS owner_subject
  FROM versions AS version
  JOIN imports AS import ON import.id=version.import_id
  ORDER BY version.document_id,version.created_at,version.id
) AS source
WHERE source.document_id=document.id;

ALTER TABLE documents ALTER COLUMN owner_subject SET NOT NULL;

CREATE TABLE resource_grants (
  resource_id text NOT NULL REFERENCES documents(id),
  principal_type text NOT NULL CHECK(principal_type IN ('SUBJECT','ROLE')),
  principal text NOT NULL,
  permission text NOT NULL CHECK(permission IN ('READ','WRITE','ADMIN')),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(resource_id,principal_type,principal)
);

CREATE TABLE resource_access_revisions (
  resource_id text NOT NULL REFERENCES documents(id),
  revision integer NOT NULL CHECK(revision > 0),
  body jsonb NOT NULL,
  actor jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(resource_id,revision)
);

INSERT INTO resource_access_revisions(resource_id,revision,body,actor)
SELECT id,1,
       jsonb_build_object(
         'ownerSubject',owner_subject,
         'visibility',visibility,
         'grants','[]'::jsonb
       ),
       '{"subject":"system:migration","roles":["admin"]}'::jsonb
FROM documents;

ALTER TABLE receipts ADD COLUMN owner_subject text;
UPDATE receipts SET owner_subject='platform-knowledge-internal';
ALTER TABLE receipts ALTER COLUMN owner_subject SET NOT NULL;
