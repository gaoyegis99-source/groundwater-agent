import assert from "node:assert/strict";
import test from "node:test";
import {
  authorizeTargetWrites,
  finalizeTargetWriteValidation,
  resolveOptions,
} from "../skills/migrate-oracle-app/scripts/target-write-policy.mjs";

test("option resolution keeps test-target-writes explicit and off by default", () => {
  assert.equal(resolveOptions()["test-target-writes"], false);
  assert.equal(resolveOptions("tests")["test-target-writes"], false);
  assert.equal(
    resolveOptions("create-shims,tests,test-target-writes")["test-target-writes"],
    true,
  );
});

test("read-only saved profiles are rejected without a write attempt", () => {
  const plan = validPlan();
  plan.profile.accessMode = "ro";

  const result = authorizeTargetWrites(plan);

  assert.equal(result.authorized, false);
  assert.equal(result.status, "NOT_EXECUTED");
  assert.equal(result.optionSatisfied, false);
  assert.equal(result.readinessImpact, "PARTIAL");
  assert.match(result.errors.join("\n"), /not write-enabled/);
});

test("query-tool read-only transaction does not make a writable profile read-only", () => {
  const plan = validPlan();
  plan.inspection = {
    queryTransactionReadOnly: true,
  };

  const result = authorizeTargetWrites(plan);

  assert.equal(result.authorized, true);
  assert.deepEqual(result.errors, []);
});

test("missing privileges prevent requested target-write execution", () => {
  const plan = validPlan();
  plan.privileges.delete = false;

  const result = authorizeTargetWrites(plan);

  assert.equal(result.status, "NOT_EXECUTED");
  assert.match(result.errors.join("\n"), /required privilege is missing: delete/);
});

test("marker isolation rejects fixtures that are not provably run-owned", () => {
  const plan = validPlan();
  plan.fixtures[0].marker = "OTHER_RUN";
  plan.fixtures[0].markerOwned = false;

  const result = authorizeTargetWrites(plan);

  assert.equal(result.authorized, false);
  assert.match(result.errors.join("\n"), /not marker-owned/);
});

test("cleanup failure prevents a passed target runtime result", () => {
  const execution = validExecution();
  execution.fixtures[0].cleanupSucceeded = false;
  execution.fixtures[0].rowsRemaining = 1;

  const result = finalizeTargetWriteValidation(validPlan(), execution);

  assert.equal(result.validationStatus, "❌ FAILED");
  assert.equal(result.stopFurtherTargetWrites, true);
  assert.match(result.errors.join("\n"), /zero marker rows/);
});

test("sequence increments are recorded as permanent non-rollback effects", () => {
  const result = finalizeTargetWriteValidation(validPlan(), validExecution());

  assert.deepEqual(result.sequenceIncrements, [
    {
      name: "app.task_id_seq",
      values: [7001],
      permanentNonRollbackEffect: true,
    },
  ]);
});

test("target drift invalidates the current-request authorization", () => {
  const plan = validPlan();
  plan.refreshedTargetDigest = "target-digest-after-drift";

  const result = authorizeTargetWrites(plan);

  assert.equal(result.authorized, false);
  assert.match(result.errors.join("\n"), /target-context drift/);
});

test("successful authoritative target testing requires driver paths and zero-row cleanup", () => {
  const result = finalizeTargetWriteValidation(validPlan(), validExecution());

  assert.equal(result.validationStatus, "✅ PASSED");
  assert.deepEqual(result.errors, []);
  assert.equal(result.stopFurtherTargetWrites, false);
});

test("VS Code PostgreSQL extension modify path is authorized", () => {
  const execution = validExecution();
  execution.productionPaths[0].invokedThrough = "pgsql_modify";

  const result = finalizeTargetWriteValidation(validPlan(), execution);

  assert.equal(result.validationStatus, "✅ PASSED");
  assert.deepEqual(result.errors, []);
});

test("requested target writes cannot pass without a recorded write operation", () => {
  const execution = validExecution();
  execution.mcpCounts.writes = 0;

  const result = finalizeTargetWriteValidation(validPlan(), execution);

  assert.equal(result.optionSatisfied, false);
  assert.equal(result.validationStatus, "❌ FAILED");
  assert.match(result.errors.join("\n"), /no authoritative target write operation/);
});

function validPlan() {
  return {
    options: {
      "test-target-writes": true,
    },
    authorizationSource: "current-request",
    authorizationSourceDigest: "request-digest",
    profile: {
      saved: true,
      accessMode: "rw",
      modifyToolAvailable: true,
    },
    server: {
      defaultTransactionReadOnly: false,
      serverInRecovery: false,
    },
    database: "app_lab",
    schema: "app",
    databaseExplicit: true,
    schemaExplicit: true,
    refresh: {
      identity: true,
      role: true,
      accessMode: true,
      privileges: true,
      contracts: true,
      locks: true,
    },
    authorizedTargetDigest: "target-digest",
    refreshedTargetDigest: "target-digest",
    mutationTargetDigest: "target-digest",
    authorizedImpactDigest: "impact-digest",
    refreshedImpactDigest: "impact-digest",
    locksSafe: true,
    requiredPrivileges: ["insert", "delete", "usage"],
    privileges: {
      insert: true,
      delete: true,
      usage: true,
    },
    marker: "MIG_TEST_run-123",
    productionPaths: [
      {
        entryPoint: "TaskService.createTask",
        dockerPassed: true,
      },
    ],
    impact: {
      affectedObjects: ["app.task", "app.task_id_seq"],
      fixtureCounts: {
        "app.task": 1,
      },
      sequenceCallCounts: {
        "app.task_id_seq": 1,
      },
      productionEntryPoints: ["TaskService.createTask"],
      assertions: ["generated task ID", "task join", "transaction commit"],
      cleanupPlan: ["DELETE marker-owned app.task row by marker and primary key"],
      nonRollbackEffects: ["app.task_id_seq advances once"],
    },
    fixtures: [
      {
        table: "app.task",
        marker: "MIG_TEST_run-123",
        markerOwned: true,
        customerRowsTouched: 0,
        plannedRows: 1,
      },
    ],
    sequences: [
      {
        name: "app.task_id_seq",
        activeCodeReference: true,
        expectedCalls: 1,
      },
    ],
    operations: [
      {
        kind: "INSERT",
      },
      {
        kind: "DELETE",
        markerOwned: true,
        customerRowsTouched: 0,
      },
      {
        kind: "NEXTVAL",
      },
    ],
  };
}

function validExecution() {
  return {
    productionPaths: [
      {
        entryPoint: "TaskService.createTask",
        invokedThrough: "postgres_mcp_modify",
        targetDigest: "target-digest",
        assertionsPassed: true,
      },
    ],
    fixtures: [
      {
        table: "app.task",
        marker: "MIG_TEST_run-123",
        markerOwned: true,
        existingRowsUpdated: 0,
        existingRowsDeleted: 0,
        rowsInserted: 1,
        rowsDeleted: 1,
        cleanupSucceeded: true,
        rowsRemaining: 0,
      },
    ],
    sequences: [
      {
        name: "app.task_id_seq",
        values: [7001],
        permanentNonRollbackRecorded: true,
      },
    ],
    mcpCounts: {
      reads: 12,
      writes: 3,
    },
  };
}
