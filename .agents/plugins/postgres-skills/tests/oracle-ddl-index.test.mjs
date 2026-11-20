import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { indexOracleDdlFolder } from "../skills/migrate-oracle-app/scripts/index-oracle-ddl.mjs";

test("indexes one Oracle object per DDL file without persisting source bodies", async () => {
  const root = await ddlFixture({
    "PROCEDURE/p_update_status.sql": `
      CREATE OR REPLACE EDITIONABLE PROCEDURE "CWF"."P_UPDATE_STATUS" (
        p_task_id IN NUMBER
      ) AS
      BEGIN
        UPDATE t_task SET status = 'DONE' WHERE task_id = p_task_id;
      END;
    `,
    "TABLE/t_task.sql": `
      CREATE TABLE CWF.T_TASK (
        TASK_ID NUMBER PRIMARY KEY,
        STATUS VARCHAR2(20)
      );
    `,
  });
  try {
    const result = await indexOracleDdlFolder(root, { schema: "CWF" });

    assert.equal(result.status, "VALID");
    assert.equal(result.schema, "CWF");
    assert.equal(result.layout, "schema-object-type-folders");
    assert.equal(result.relativeSchemaPath, "exports/CWF");
    assert.equal(result.indexedObjects, 2);
    assert.deepEqual(
      result.files.map((file) => file.displayIdentity),
      ["CWF.P_UPDATE_STATUS", "CWF.T_TASK"],
    );
    assert.doesNotMatch(JSON.stringify(result), /UPDATE t_task SET status/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("allows package specification and body for the same package in one file", async () => {
  const root = await ddlFixture({
    "PACKAGE/task_api.sql": `
      CREATE OR REPLACE PACKAGE CWF.TASK_API AS
        PROCEDURE UPDATE_STATUS(p_task_id IN NUMBER);
      END TASK_API;
      /
      CREATE OR REPLACE PACKAGE BODY CWF.TASK_API AS
        PROCEDURE UPDATE_STATUS(p_task_id IN NUMBER) AS
        BEGIN
          NULL;
        END UPDATE_STATUS;
      END TASK_API;
      /
    `,
  });
  try {
    const result = await indexOracleDdlFolder(root, { schema: "CWF" });

    assert.equal(result.status, "VALID");
    assert.equal(result.files[0].objectKind, "package");
    assert.deepEqual(result.files[0].statementKinds, [
      "package",
      "package body",
    ]);
    assert.ok(
      result.files[0].packageMembers.some(
        (member) => member.kind === "procedure" && member.name === "UPDATE_STATUS",
      ),
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("allows package specification and body as companion object files", async () => {
  const root = await ddlFixture({
    "PACKAGE/task_api.pks": `
      CREATE OR REPLACE PACKAGE CWF.TASK_API AS
        PROCEDURE UPDATE_STATUS(p_task_id IN NUMBER);
      END TASK_API;
    `,
    "PACKAGE/task_api.pkb": `
      CREATE OR REPLACE PACKAGE BODY CWF.TASK_API AS
        PROCEDURE UPDATE_STATUS(p_task_id IN NUMBER) AS
        BEGIN
          NULL;
        END UPDATE_STATUS;
      END TASK_API;
    `,
  });
  try {
    const result = await indexOracleDdlFolder(root, { schema: "CWF" });

    assert.equal(result.status, "VALID");
    assert.equal(result.indexedObjects, 1);
    assert.equal(result.files.length, 2);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("rejects files containing more than one Oracle object", async () => {
  const root = await ddlFixture({
    "TABLE/invalid.sql": `
      CREATE TABLE CWF.T_TASK (TASK_ID NUMBER);
      CREATE TABLE CWF.T_OTHER (ID NUMBER);
    `,
  });
  try {
    const result = await indexOracleDdlFolder(root, { schema: "CWF" });

    assert.equal(result.status, "INVALID");
    assert.match(result.errors.join("\n"), /multiple primary TABLE objects/);
    assert.match(result.errors.join("\n"), /one file per object is required/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("rejects duplicate object identities across files", async () => {
  const root = await ddlFixture({
    "SEQUENCE/a.sql": "CREATE SEQUENCE CWF.SEQ_TASK;",
    "SEQUENCE/b.sql": "CREATE SEQUENCE CWF.SEQ_TASK;",
  });
  try {
    const result = await indexOracleDdlFolder(root, { schema: "CWF" });

    assert.equal(result.status, "INVALID");
    assert.match(result.errors.join("\n"), /duplicate Oracle object DDL files/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("ignores CREATE text in comments and Oracle string literals", async () => {
  const root = await ddlFixture({
    "PROCEDURE/p_execute.sql": `
      -- CREATE TABLE CWF.COMMENTED_OUT (ID NUMBER);
      CREATE OR REPLACE PROCEDURE CWF.P_EXECUTE AS
        v_sql VARCHAR2(1000);
      BEGIN
        v_sql := 'CREATE TABLE CWF.DYNAMIC_TABLE (ID NUMBER)';
        v_sql := q'[CREATE VIEW CWF.DYNAMIC_VIEW AS SELECT 1 FROM DUAL]';
      END;
    `,
  });
  try {
    const result = await indexOracleDdlFolder(root, { schema: "CWF" });

    assert.equal(result.status, "VALID");
    assert.equal(result.indexedObjects, 1);
    assert.equal(result.files[0].displayIdentity, "CWF.P_EXECUTE");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("discovers a canonical layout with an arbitrary user-provided migration name", async () => {
  const root = await ddlFixture(
    {
      "FUNCTION/f_value.sql":
        "CREATE OR REPLACE FUNCTION CWF.F_VALUE RETURN NUMBER AS BEGIN RETURN 1; END;",
      "SCHEMA/CWF.sql": 'CREATE SCHEMA IF NOT EXISTS "CWF";',
    },
    {
      schemaRelativePath:
        ".github/postgres-migrations/Customer Migration Wave 01/artifacts/oracle/CWF",
    },
  );
  try {
    const result = await indexOracleDdlFolder(root, { schema: "cwf" });

    assert.equal(result.status, "VALID");
    assert.equal(result.layout, "canonical-postgres-migrations");
    assert.equal(result.migrationName, "Customer Migration Wave 01");
    assert.equal(
      result.relativeSchemaPath,
      ".github/postgres-migrations/Customer Migration Wave 01/artifacts/oracle/CWF",
    );
    assert.match(
      result.objectTypeDirectories.find(
        (objectType) => objectType.name === "FUNCTION",
      ).relativePath,
      /\.github\/postgres-migrations\/Customer Migration Wave 01\/artifacts\/oracle\/CWF\/FUNCTION$/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("accepts a schema folder directly when all object-type folders exist", async () => {
  const projectRoot = await ddlFixture({
    "SEQUENCE/seq_task.sql": "CREATE SEQUENCE CWF.SEQ_TASK;",
  });
  const schemaRoot = path.join(projectRoot, "exports", "CWF");
  try {
    const result = await indexOracleDdlFolder(schemaRoot, { schema: "CWF" });

    assert.equal(result.status, "VALID");
    assert.equal(result.relativeSchemaPath, ".");
    assert.equal(result.schema, "CWF");
  } finally {
    await rm(projectRoot, { recursive: true, force: true });
  }
});

test("accepts fewer object-type directories when that is what the migration contains", async () => {
  const root = await ddlFixture(
    {
      "TABLE/t_task.sql": "CREATE TABLE CWF.T_TASK (TASK_ID NUMBER);",
    },
    {
      omittedObjectTypes: OBJECT_TYPES.filter(
        (objectType) => objectType !== "TABLE",
      ),
    },
  );
  try {
    const result = await indexOracleDdlFolder(root, { schema: "CWF" });

    assert.equal(result.status, "VALID");
    assert.deepEqual(
      result.objectTypeDirectories.map((objectType) => objectType.name),
      ["TABLE"],
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("allows dependent indexes bundled with a primary table file", async () => {
  const root = await ddlFixture({
    "TABLE/t_task.sql": `
      CREATE TABLE CWF.T_TASK (TASK_ID NUMBER);
      CREATE UNIQUE INDEX CWF.PK_TASK ON CWF.T_TASK (TASK_ID);
      ALTER TABLE CWF.T_TASK ADD CONSTRAINT PK_TASK PRIMARY KEY (TASK_ID);
    `,
  });
  try {
    const result = await indexOracleDdlFolder(root, { schema: "CWF" });

    assert.equal(result.status, "VALID");
    assert.equal(result.indexedObjects, 1);
    assert.deepEqual(result.files[0].auxiliaryDefinitions, [
      {
        objectKind: "index",
        displayIdentity: "CWF.PK_TASK",
        objectKey: "index:CWF.PK_TASK",
      },
    ]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("rejects a primary object placed in the wrong type directory", async () => {
  const root = await ddlFixture({
    "FUNCTION/p_wrong.sql":
      "CREATE OR REPLACE PROCEDURE CWF.P_WRONG AS BEGIN NULL; END;",
  });
  try {
    const result = await indexOracleDdlFolder(root, { schema: "CWF" });

    assert.equal(result.status, "INVALID");
    assert.match(
      result.errors.join("\n"),
      /parsed object kinds \(procedure\) do not match object-type folder FUNCTION/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("indexes additional user-defined object-type folders by DDL or filename", async () => {
  const root = await ddlFixture({
    "DATABASE_LINK/REMOTE_REPORTING.sql": `
      CREATE DATABASE LINK REMOTE_REPORTING
      CONNECT TO reporting IDENTIFIED BY secret
      USING 'REPORTING';
    `,
  });
  try {
    const result = await indexOracleDdlFolder(root, { schema: "CWF" });

    assert.equal(result.status, "VALID");
    const file = result.files.find(
      (candidate) =>
        candidate.objectTypeDirectory === "DATABASE_LINK",
    );
    assert.equal(file.objectKind, "database link");
    assert.equal(file.oracleSchema, "CWF");
    assert.equal(file.oracleObjectName, "REMOTE_REPORTING");
    assert.equal(file.identitySource, "folder-and-filename");
    assert.ok(result.warnings.some((warning) => /DATABASE_LINK/.test(warning)));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

const OBJECT_TYPES = [
  "FUNCTION",
  "INDEX",
  "PACKAGE",
  "PROCEDURE",
  "SCHEMA",
  "SEQUENCE",
  "TABLE",
  "TRIGGER",
  "VIEW",
];

async function ddlFixture(
  files,
  {
    schemaRelativePath = "exports/CWF",
    omittedObjectTypes = [],
  } = {},
) {
  const root = await mkdtemp(path.join(os.tmpdir(), "oracle-ddl-index-"));
  const schemaRoot = path.join(root, ...schemaRelativePath.split("/"));
  for (const objectType of OBJECT_TYPES) {
    if (omittedObjectTypes.includes(objectType)) continue;
    await mkdir(path.join(schemaRoot, objectType), { recursive: true });
  }
  for (const [relativePath, content] of Object.entries(files)) {
    const absolutePath = path.join(schemaRoot, ...relativePath.split("/"));
    await mkdir(path.dirname(absolutePath), { recursive: true });
    await writeFile(absolutePath, content);
  }
  return root;
}
