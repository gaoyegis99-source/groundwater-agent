export const CANONICAL_OPTIONS = Object.freeze([
  "establish-baseline",
  "create-shims",
  "create-persistent-ddl",
  "test-target-writes",
  "start-docker-desktop",
  "remove-unused-artifacts",
  "create-unit-tests",
  "create-integration-tests",
  "fix-broken-tests",
]);

const TEST_OPTIONS = Object.freeze([
  "establish-baseline",
  "create-unit-tests",
  "create-integration-tests",
  "fix-broken-tests",
]);

const WRITE_ACCESS_MODES = new Set([
  "rw",
  "write",
  "read-write",
  "transaction read-write",
]);
const FORBIDDEN_OPERATIONS = new Set(["CREATE", "TRUNCATE", "ALTER", "REPLACE", "DROP"]);
const MARKER_PATTERN = /^MIG_TEST_[A-Za-z0-9_-]+$/;

export function resolveOptions(expression = "") {
  const resolved = Object.fromEntries(CANONICAL_OPTIONS.map((option) => [option, false]));
  const requested = expression
    .split(",")
    .map((option) => option.trim())
    .filter(Boolean);

  for (const option of requested) {
    if (option === "tests") {
      for (const testOption of TEST_OPTIONS) {
        resolved[testOption] = true;
      }
      continue;
    }

    if (!Object.hasOwn(resolved, option)) {
      throw new Error(`Unknown migration option: ${option}`);
    }
    resolved[option] = true;
  }

  return resolved;
}

export function authorizeTargetWrites(plan) {
  const errors = [];

  if (!plan.options?.["test-target-writes"]) {
    return {
      authorized: false,
      requested: false,
      optionSatisfied: true,
      status: "SKIPPED",
      errors: ["test-target-writes is off"],
    };
  }

  requireCondition(
    errors,
    plan.authorizationSource === "current-request",
    "authorization must come from the current request",
  );
  requireCondition(
    errors,
    isExactName(plan.authorizationSourceDigest),
    "current-request authorization digest is required",
  );
  requireCondition(errors, plan.profile?.saved === true, "a saved MCP profile is required");
  requireCondition(
    errors,
    WRITE_ACCESS_MODES.has(plan.profile?.accessMode),
    "the saved MCP profile is not write-enabled",
  );
  requireCondition(
    errors,
    plan.profile?.modifyToolAvailable === true,
    "the MCP modification path is unavailable for the saved profile",
  );
  requireCondition(
    errors,
    plan.server?.defaultTransactionReadOnly === false,
    "the server or role default transaction is read-only or indeterminate",
  );
  requireCondition(
    errors,
    plan.server?.serverInRecovery === false,
    "the selected target is a recovery/read-replica context or indeterminate",
  );
  requireCondition(errors, isExactName(plan.database), "an exact database is required");
  requireCondition(errors, isExactName(plan.schema), "an exact schema is required");
  requireCondition(
    errors,
    plan.databaseExplicit === true && plan.schemaExplicit === true,
    "database and schema must be explicit in the current request",
  );

  const refreshChecks = [
    ["identity", "target identity was not refreshed"],
    ["role", "target role was not refreshed"],
    ["accessMode", "access mode was not refreshed"],
    ["privileges", "privileges were not refreshed"],
    ["contracts", "object contracts were not refreshed"],
    ["locks", "relevant locks were not refreshed"],
  ];
  for (const [check, message] of refreshChecks) {
    requireCondition(errors, plan.refresh?.[check] === true, message);
  }

  requireCondition(
    errors,
    plan.authorizedTargetDigest === plan.refreshedTargetDigest,
    "target-context drift detected",
  );
  requireCondition(
    errors,
    plan.authorizedTargetDigest === plan.mutationTargetDigest,
    "MCP modification target differs from the authorized target",
  );
  requireCondition(
    errors,
    plan.authorizedImpactDigest === plan.refreshedImpactDigest,
    "write-impact drift detected",
  );
  requireCondition(errors, plan.locksSafe === true, "relevant target locks are unsafe");

  for (const privilege of plan.requiredPrivileges ?? []) {
    requireCondition(
      errors,
      plan.privileges?.[privilege] === true,
      `required privilege is missing: ${privilege}`,
    );
  }

  requireCondition(
    errors,
    Array.isArray(plan.productionPaths) &&
      plan.productionPaths.length > 0 &&
      plan.productionPaths.every((path) => path.dockerPassed === true),
    "every target production path must pass in Docker first",
  );
  validateImpactPreview(plan, errors);
  requireCondition(
    errors,
    setsEqual(
      new Set((plan.productionPaths ?? []).map((path) => path.entryPoint)),
      new Set(plan.impact?.productionEntryPoints ?? []),
    ),
    "Docker-proven production paths differ from the impact preview",
  );
  validatePlannedFixtures(plan, errors);
  validatePlannedSequences(plan, errors);
  validateOperations(plan, errors);

  return {
    authorized: errors.length === 0,
    requested: true,
    optionSatisfied: false,
    status: errors.length === 0 ? "AUTHORIZED" : "NOT_EXECUTED",
    readinessImpact: errors.length === 0 ? null : "PARTIAL",
    errors,
  };
}

export function finalizeTargetWriteValidation(plan, execution) {
  const authorization = authorizeTargetWrites(plan);
  if (!authorization.authorized) {
    return {
      status: authorization.status,
      requested: authorization.requested,
      optionSatisfied: authorization.optionSatisfied,
      writeAttempted: false,
      validationStatus:
        authorization.status === "SKIPPED" ? "➖ NOT REQUESTED" : "⚠️ NOT EXECUTED",
      errors: authorization.errors,
      readinessImpact: authorization.readinessImpact ?? null,
      stopFurtherTargetWrites: authorization.requested,
      sequenceIncrements: [],
    };
  }

  const errors = [];
  const cleanupErrors = [];
  const expectedEntryPoints = new Set(plan.impact.productionEntryPoints);
  const actualEntryPoints = new Set(
    (execution.productionPaths ?? []).map((path) => path.entryPoint),
  );

  requireCondition(
    errors,
    setsEqual(expectedEntryPoints, actualEntryPoints),
    "not every authorized production entry point was exercised",
  );
  for (const path of execution.productionPaths ?? []) {
    requireCondition(
      errors,
      [
        "pgsql_modify",
        "postgres_mcp_modify",
        "postgresql-driver",
      ].includes(path.invokedThrough),
      `target mutation did not use an authorized MCP modification or PostgreSQL driver path: ${path.entryPoint}`,
    );
    requireCondition(
      errors,
      path.targetDigest === plan.authorizedTargetDigest,
      `production path target drifted: ${path.entryPoint}`,
    );
    requireCondition(
      errors,
      path.assertionsPassed === true,
      `production path assertions failed: ${path.entryPoint}`,
    );
  }

  const expectedFixtureTables = new Set((plan.fixtures ?? []).map((fixture) => fixture.table));
  const actualFixtureTables = new Set(
    (execution.fixtures ?? []).map((fixture) => fixture.table),
  );
  requireCondition(
    errors,
    setsEqual(expectedFixtureTables, actualFixtureTables),
    "cleanup proof does not cover every planned fixture table",
  );

  for (const fixture of execution.fixtures ?? []) {
    const planned = (plan.fixtures ?? []).find(
      (candidate) => candidate.table === fixture.table,
    );
    requireCondition(
      errors,
      fixture.marker === plan.marker && fixture.markerOwned === true,
      `fixture ownership is not isolated: ${fixture.table}`,
    );
    requireCondition(
      errors,
      fixture.existingRowsUpdated === 0 && fixture.existingRowsDeleted === 0,
      `existing customer rows were changed: ${fixture.table}`,
    );
    requireCondition(
      errors,
      planned !== undefined && fixture.rowsInserted === planned.plannedRows,
      `actual fixture count differs from the impact preview: ${fixture.table}`,
    );
    if (
      fixture.cleanupSucceeded !== true ||
      fixture.rowsDeleted !== fixture.rowsInserted ||
      fixture.rowsRemaining !== 0
    ) {
      cleanupErrors.push(`cleanup did not leave zero marker rows: ${fixture.table}`);
    }
  }

  const sequenceIncrements = [];
  const expectedSequences = new Set((plan.sequences ?? []).map((sequence) => sequence.name));
  const actualSequences = new Set(
    (execution.sequences ?? []).map((sequence) => sequence.name),
  );
  requireCondition(
    errors,
    setsEqual(expectedSequences, actualSequences),
    "recorded sequences differ from the authorized sequence manifest",
  );
  for (const sequence of plan.sequences ?? []) {
    const recorded = (execution.sequences ?? []).find(
      (candidate) => candidate.name === sequence.name,
    );
    requireCondition(errors, recorded !== undefined, `sequence result missing: ${sequence.name}`);
    if (!recorded) {
      continue;
    }

    requireCondition(
      errors,
      recorded.values.length === sequence.expectedCalls,
      `not every sequence value was recorded: ${sequence.name}`,
    );
    requireCondition(
      errors,
      recorded.permanentNonRollbackRecorded === true,
      `sequence non-rollback effect was not acknowledged: ${sequence.name}`,
    );
    sequenceIncrements.push({
      name: sequence.name,
      values: [...recorded.values],
      permanentNonRollbackEffect: recorded.permanentNonRollbackRecorded === true,
    });
  }

  requireCondition(
    errors,
    Number.isInteger(execution.mcpCounts?.reads) &&
      Number.isInteger(execution.mcpCounts?.writes),
    "MCP read/write counts were not recorded",
  );
  requireCondition(
    errors,
    execution.mcpCounts?.writes > 0,
    "no authoritative target write operation was recorded",
  );

  errors.push(...cleanupErrors);
  const passed = errors.length === 0;
  return {
    status: passed ? "PASSED" : "FAILED",
    requested: true,
    optionSatisfied: passed,
    writeAttempted: true,
    validationStatus: passed ? "✅ PASSED" : "❌ FAILED",
    errors,
    readinessImpact: passed ? null : "PARTIAL",
    stopFurtherTargetWrites: cleanupErrors.length > 0,
    sequenceIncrements,
  };
}

function validateImpactPreview(plan, errors) {
  const impact = plan.impact;
  requireCondition(
    errors,
    Array.isArray(impact?.affectedObjects) && impact.affectedObjects.length > 0,
    "impact preview must list affected objects",
  );
  requireCondition(
    errors,
    impact?.fixtureCounts !== null &&
      typeof impact?.fixtureCounts === "object" &&
      !Array.isArray(impact.fixtureCounts),
    "impact preview must list fixture counts",
  );
  requireCondition(
    errors,
    impact?.sequenceCallCounts !== null &&
      typeof impact?.sequenceCallCounts === "object" &&
      !Array.isArray(impact.sequenceCallCounts),
    "impact preview must list sequence call counts",
  );
  requireCondition(
    errors,
    Array.isArray(impact?.productionEntryPoints) && impact.productionEntryPoints.length > 0,
    "impact preview must list production entry points",
  );
  requireCondition(
    errors,
    Array.isArray(impact?.assertions) && impact.assertions.length > 0,
    "impact preview must list assertions",
  );
  requireCondition(
    errors,
    Array.isArray(impact?.cleanupPlan) && impact.cleanupPlan.length > 0,
    "impact preview must include a cleanup plan",
  );
  requireCondition(
    errors,
    Array.isArray(impact?.nonRollbackEffects),
    "impact preview must list non-rollback effects",
  );
}

function validatePlannedFixtures(plan, errors) {
  requireCondition(errors, MARKER_PATTERN.test(plan.marker ?? ""), "invalid MIG_TEST marker");
  requireCondition(
    errors,
    (plan.fixtures ?? []).length === Object.keys(plan.impact?.fixtureCounts ?? {}).length,
    "fixture plan does not match every impact-preview table",
  );
  for (const fixture of plan.fixtures ?? []) {
    requireCondition(
      errors,
      fixture.marker === plan.marker && fixture.markerOwned === true,
      `planned fixture is not marker-owned: ${fixture.table}`,
    );
    requireCondition(
      errors,
      fixture.customerRowsTouched === 0,
      `planned fixture may touch customer rows: ${fixture.table}`,
    );
    requireCondition(
      errors,
      fixture.plannedRows === plan.impact.fixtureCounts?.[fixture.table],
      `planned fixture count differs from the impact preview: ${fixture.table}`,
    );
  }
}

function validatePlannedSequences(plan, errors) {
  requireCondition(
    errors,
    (plan.sequences ?? []).length ===
      Object.keys(plan.impact?.sequenceCallCounts ?? {}).length,
    "sequence manifest does not match every impact-preview sequence",
  );
  for (const sequence of plan.sequences ?? []) {
    requireCondition(
      errors,
      sequence.activeCodeReference === true,
      `sequence is not derived from active code: ${sequence.name}`,
    );
    requireCondition(
      errors,
      sequence.expectedCalls === plan.impact.sequenceCallCounts?.[sequence.name],
      `sequence call count differs from the impact preview: ${sequence.name}`,
    );
  }
}

function validateOperations(plan, errors) {
  for (const operation of plan.operations ?? []) {
    requireCondition(
      errors,
      !FORBIDDEN_OPERATIONS.has(operation.kind),
      `forbidden target-write operation: ${operation.kind}`,
    );
    if (operation.kind === "UPDATE" || operation.kind === "DELETE") {
      requireCondition(
        errors,
        operation.markerOwned === true && operation.customerRowsTouched === 0,
        `${operation.kind} is not limited to marker-owned rows`,
      );
    }
  }
}

function isExactName(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function requireCondition(errors, condition, message) {
  if (!condition) {
    errors.push(message);
  }
}

function setsEqual(left, right) {
  return left.size === right.size && [...left].every((value) => right.has(value));
}
