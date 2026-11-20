import { readFile, readdir, rename, stat, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import path from "node:path";
import process from "node:process";
import {
  detectMigrationScope,
  readExecutionArtifactBaseline,
} from "./detect-migration-scope.mjs";
import { inspectDriverPackaging } from "./inspect-driver-packaging.mjs";
import { indexOracleDdlFolder } from "./index-oracle-ddl.mjs";
import { planPostgresToolDiscovery } from "./postgres-provider-policy.mjs";
import { renderRunMetrics } from "./format-run-metrics.mjs";
import { verifyMigrationBoundary } from "./migration-runtime.mjs";
import { codingNotesInputDigest, validateCodingNotesReport } from "./coding-notes.mjs";
import {
  MINIMUM_TEST_WAVES,
  MAXIMUM_TEST_WAVES,
  validateOptionalWaveValue,
} from "./run-maven-waves.mjs";

const root = path.resolve(process.argv[2] ?? ".");
const requestedMode = process.argv[3] ?? "both";
const mode = new Map([
  ["assess", "assessment"],
  ["assessment", "assessment"],
  ["convert", "conversion"],
  ["conversion", "conversion"],
  ["assess-and-convert", "both"],
  ["both", "both"],
]).get(requestedMode);
if (!mode) {
  console.error(
    `ERROR: unsupported validation mode ${requestedMode}; use assessment, conversion, or both`,
  );
  process.exit(2);
}
const record = process.argv.includes("--record");
const requireRuntime = process.argv.includes("--require-runtime");
const runtimeDirectoryIndex = process.argv.indexOf("--runtime-directory");
const runtimeDirectoryArgument = runtimeDirectoryIndex >= 0
  ? process.argv[runtimeDirectoryIndex + 1]
  : undefined;
if (runtimeDirectoryIndex >= 0 &&
  (!runtimeDirectoryArgument || runtimeDirectoryArgument.startsWith("--"))) {
  console.error("ERROR: --runtime-directory requires a private runtime directory path");
  process.exit(2);
}
const runtimeDirectory = runtimeDirectoryArgument
  ? path.resolve(root, runtimeDirectoryArgument)
  : undefined;
const errors = [];
const warnings = [];
const runMetricsReports = [];
const codingNotesInputs = [];
let testSourceInventoryPromise;
let missingDiscoveryInventoryReported = false;

if (mode === "assessment" || mode === "both") {
  await validateAssessment();
}
if (mode === "conversion" || mode === "both") {
  await validateConversion();
}
if (
  mode === "both" &&
  runMetricsReports.length === 2 &&
  runMetricsReports[0] !== runMetricsReports[1]
) {
  errors.push("Assessment and conversion Run Metrics must use the same final timing snapshot for a combined run");
}
if (mode === "both" && codingNotesInputs.length === 2 && codingNotesInputs[0] !== codingNotesInputs[1]) {
  errors.push("Combined reports require the same current coding notes input snapshot");
}

for (const warning of warnings) {
  console.warn(`WARNING: ${warning}`);
}
if (errors.length > 0) {
  for (const error of errors) {
    console.error(`ERROR: ${error}`);
  }
  process.exitCode = 1;
} else {
  if (record) {
    await recordArtifactValidation();
    console.log(`Migration artifact validation passed and was recorded for ${mode}.`);
  } else {
    console.log(`Migration artifact validation passed for ${mode}.`);
  }
}

async function validateNotes(state, report, statePath, phase) {
  try {
    await validateCodingNotesReport(root, state, report, phase);
    codingNotesInputs.push(codingNotesInputDigest(state.codingNotes));
  } catch (error) {
    const reason = error.code && !error.code.startsWith("MIGRATION_") ? error.code : error.message;
    errors.push(`${statePath}: ${reason}`);
  }
}

async function validateAssessment() {
  const reportPath = ".github/migrate-oracle-app/assessment/assessment-report.md";
  const statePath = ".github/migrate-oracle-app/assessment/assessment-data.json";
  const discoveryPath =
    ".github/migrate-oracle-app/assessment/test-discovery-baseline.json";
  const report = await requiredText(reportPath);
  const state = await requiredJson(statePath);
  if (!report || !state) return;
  await validateNotes(state, report, statePath, "assessment");
  await validateRuntimeExecution("assessment", state, report, statePath);
  validateRunMetrics(state, report, statePath, reportPath);
  if (isStaticScopeStop(state)) {
    await validateStaticScopeStop(
      state,
      report,
      statePath,
      reportPath,
      "assessment",
      null,
      normalizedScopeStatus(state),
    );
    validateArtifactRecord(state, report, null, statePath, reportPath);
    return;
  }
  const discovery = await requiredJson(discoveryPath);
  if (!discovery) await reportUnbaselinedSourceInventory(discoveryPath);
  if (!discovery) return;

  requireMatch(report, /^# Oracle to PostgreSQL Application Code Assessment$/m, reportPath);
  requireOrderedHeadings(report, [
    "## Table of Contents",
    "## Executive Summary",
    "## Application & Database Architecture",
    "## Migration Impact Assessment",
    "## PostgreSQL Target Readiness",
    "## Test & Validation Readiness",
    "## Conversion Success Criteria",
    "## Assessment Outcome",
    "## Appendix / Run Evidence",
  ], reportPath);
  requireIncludes(
    report,
    "| Hotspot | Evidence / scope | Status | Conversion action / impact |",
    reportPath,
  );
  requireMatch(report, /#### Coverage Heatmap/, reportPath);
  requireMatch(report, /### Coverage by Lane/, reportPath);
  requireMatch(report, /### Migration-Sensitive Coverage/, reportPath);
  requireMatch(report, /### Test Discovery Reconciliation/, reportPath);
  requireIncludes(
    report,
    "| Language | Framework / adapter | Source files | Test cases | Adapter available | Eligible containers | Executed containers | Executed tests | Missing containers | Result |",
    reportPath,
  );
  requireMatch(report, /\| Report artifact validation \|/, reportPath);
  validateNavigation(report, reportPath);
  rejectPlaceholders(report, reportPath);
  validatePostgresProviderSelection(state, statePath);
  validateTargetMetadataAcquisition(state, statePath);
  await validateOracleDdlSource(
    state,
    report,
    statePath,
    reportPath,
    "assessment",
  );

  const baselineTests = firstNumber(
    state?.enabledBaseline?.tests?.unit,
    state?.baselineEnablement?.enabledBaseline?.testsRun,
    state?.baseline?.enabled?.unitTests?.tests,
    state?.coverage?.lanes?.unit?.tests,
  );
  if (!Number.isInteger(baselineTests) || baselineTests <= 0) {
    const reason = state?.enabledBaseline?.noTestsReason ?? state?.baselineEnablement?.noTestsReason;
    if (!reason) errors.push(`${statePath}: enabled baseline test total or noTestsReason is required`);
  }
  validateDiscoveryBaseline(discovery, discoveryPath);
  await validateSourceDiscoveryInventory(discovery, discoveryPath);
  const discoveryEvidence = state?.testDiscovery;
  if (
    discoveryEvidence?.baselinePath !== discoveryPath ||
    discoveryEvidence?.baselineSha256 !== sha256(JSON.stringify(discovery))
  ) {
    errors.push(`${statePath}: reviewed test-discovery baseline path/digest is missing or stale`);
  }
  const expectedDiscoveryStatus =
    normalizeStatus(discovery?.review?.status) === "approved"
      ? "passed"
      : "action needed";
  if (normalizeStatus(discoveryEvidence?.status) !== expectedDiscoveryStatus) {
    errors.push(`${statePath}: test-discovery status does not match the reviewed baseline`);
  }
  validateArtifactRecord(state, report, null, statePath, reportPath);
}

async function validateConversion() {
  const reportPath = ".github/migrate-oracle-app/conversions/conversion-report.md";
  const statePath = ".github/migrate-oracle-app/conversions/conversion-state.json";
  const diffPath = ".github/migrate-oracle-app/conversions/file-diffs.md";
  const discoveryPath =
    ".github/migrate-oracle-app/assessment/test-discovery-baseline.json";
  const report = await requiredText(reportPath);
  const state = await requiredJson(statePath);
  const assessmentState = await optionalJson(
    ".github/migrate-oracle-app/assessment/assessment-data.json",
  );
  if (!report || !state) return;
  await validateNotes(state, report, statePath, "conversion");
  await validateRuntimeExecution("conversion", state, report, statePath);
  validateRunMetrics(state, report, statePath, reportPath);
  if (isStaticScopeStop(state)) {
    const diffs = await optionalText(diffPath);
    await validateStaticScopeStop(
      state,
      report,
      statePath,
      reportPath,
      "conversion",
      assessmentState,
      normalizedScopeStatus(state),
    );
    if (diffs) {
      errors.push(`${diffPath}: no-conversion run must not create file-diff evidence`);
    }
    validateArtifactRecord(state, report, null, statePath, reportPath);
    return;
  }
  const discovery = await requiredJson(discoveryPath);
  const noModifiedReason = state?.fileInventory?.noModifiedTrackedFilesReason;
  const diffs = noModifiedReason
    ? await optionalText(diffPath)
    : await requiredText(diffPath);
  if (!discovery) await reportUnbaselinedSourceInventory(discoveryPath);
  if (!diffs || !discovery) return;

  requireMatch(report, /^# Oracle to PostgreSQL Application Code Conversion$/m, reportPath);
  requireOrderedHeadings(report, [
    "## Table of Contents",
    "## Executive Conversion Summary",
    "## Conversion Transformation",
    "## Validation Results",
    "## Test & Coverage Results",
    "## Database Test Environment",
    "## Remaining Work & Exceptions",
    "## Conversion Outcome",
    "## Appendix / Execution Evidence",
  ], reportPath);
  for (const tableHeader of [
    "| Area | Result | Status |",
    "| Priority | Area | Action needed | Owner |",
    "| Stage / gate | Scope / evidence | Result | Readiness impact |",
    "| Lane | Baseline line | Final line | Baseline branch | Final branch | Evidence |",
    "| Environment | Table | Fixture loads | Rows created | Peak rows present | Rows reused | Rows cleaned / rolled back | Rows remaining | Status |",
    "| Object kind | Active / code-derived in tested scope | Exercised | Evidence / gap |",
    "| Object / signature | Kind | Production entry point | Synthetic fixtures | Invocation form | Assertions / side effects | Environment | Result |",
    "| Test / scenario | Production entry point | PostgreSQL objects | Fixture tables / rows | Behavior exercised | Assertions / observed outcome | Result |",
    "| Language | Framework / adapter | Source files | Test cases | Adapter version | Eligible containers | Executed containers | Executed tests | Missing containers | Result |",
    "| Requested | Applicable mutation scenarios | Writable context | Write attempted | MCP modification / driver scenarios | Marker rows inserted / deleted / remaining | Sequence values consumed | Recorded write operations | Option satisfied | Result |",
    "| Category | Discovered | Converted | Intentionally retained | Deferred | Safely convertible remaining | Evidence / exact reason |",
    "| Missing PostgreSQL object | Oracle source / reconstruction provenance | Source DDL file / digest | Body completeness / confidence | PostgreSQL DDL and application call updates | Synthetic Docker validation | Persistent target result / customer validation |",
    "| Failure class / suite | Exposed failures / errors | Classification / root cause | Repair attempts | Files repaired | Final result / next action |",
    "| Batch | Agent / type | Objective / unit | Mode / owned paths | Started | Completed | Result |",
    "| Control | Evidence | Result |",
    "| Option enabled | Engine running at preflight | Desktop detected | Start attempted | Desktop state changed | Engine responsive | Result / limitation |",
    "| Path | Kind | Unused evidence | Build/package references | Action | Result |",
    "| Archive | Source → packaged runtime descriptor | Required driver path / pattern | Matching deployment entries | WAR-only copies | Result |",
  ]) {
    requireIncludes(report, tableHeader, reportPath);
  }
  validateExecutiveConversionSummary(state, report, statePath, reportPath);
  requireMatch(report, /### Synthetic Test Data/, reportPath);
  requireMatch(report, /### Database Objects Exercised with Synthetic Data/, reportPath);
  requireMatch(report, /### Docker Engine Availability/, reportPath);
  requireMatch(report, /### Coverage Convergence/, reportPath);
  requireMatch(report, /### Test Discovery Reconciliation/, reportPath);
  requireMatch(report, /### Target Write Test Reconciliation/, reportPath);
  requireMatch(report, /### Conversion Scope Reconciliation/, reportPath);
  requireMatch(report, /### Unused Artifacts/, reportPath);
  requireMatch(report, /### Broken-Test Repair Reconciliation/, reportPath);
  requireMatch(report, /### Parallel Execution & Agent Utilization/, reportPath);
  requireMatch(report, /### Performance & Execution Efficiency/, reportPath);
  requireMatch(report, /### Runtime Driver Packaging Reconciliation/, reportPath);
  requireMatch(report, /### Changes Inventory/, reportPath);
  for (const gate of [
    "| Test runner/adapter availability |",
    "| Test discovery reconciliation |",
    "| Broken-test repair |",
  ]) {
    requireIncludes(report, gate, reportPath);
  }
  requireMatch(report, /\| Report artifact validation \|/, reportPath);
  validateNavigation(report, reportPath);
  validateDiffNavigation(report, diffs ?? "", reportPath, diffPath, noModifiedReason);
  rejectPlaceholders(report, reportPath);
  validatePostgresProviderSelection(state, statePath);
  validateTargetMetadataAcquisition(state, statePath);
  await validateOracleDdlSource(
    state,
    report,
    statePath,
    reportPath,
    "conversion",
  );

  const baselineTests = state?.assessmentBaseline?.unitTests;
  const finalUnitTests = state?.validation?.unitTests?.tests;
  const baselineNoTestsReason = state?.assessmentBaseline?.noTestsReason;
  const finalNoTestsReason = state?.validation?.unitTests?.noTestsReason;
  if ((!Number.isInteger(baselineTests) || baselineTests <= 0) && !baselineNoTestsReason) {
    errors.push(`${statePath}: assessmentBaseline.unitTests or noTestsReason is required`);
  }
  if ((!Number.isInteger(finalUnitTests) || finalUnitTests <= 0) && !finalNoTestsReason) {
    errors.push(`${statePath}: validation.unitTests.tests or noTestsReason is required`);
  }
  if (Number.isInteger(baselineTests) && Number.isInteger(finalUnitTests) && finalUnitTests < baselineTests) {
    const reconciliation = state?.validation?.testReconciliation;
    if (!reconciliation) {
      errors.push(
        `${statePath}: final unit tests (${finalUnitTests}) are below baseline (${baselineTests}) without a reconciliation record`,
      );
    }
  }
  if (Number.isInteger(finalUnitTests) && !includesFormattedNumber(report, finalUnitTests)) {
    errors.push(`${reportPath}: final unit test total ${finalUnitTests} is not present`);
  }

  validateDiscoveryBaseline(discovery, discoveryPath);
  await validateSourceDiscoveryInventory(discovery, discoveryPath);
  await validateConversionTestDiscovery(
    state,
    discovery,
    report,
    statePath,
    reportPath,
  );
  validatePersistentDdlOption(state, report, statePath, reportPath);
  validateTargetWriteOption(state, report, statePath, reportPath);
  validateDockerDesktopPolicy(state, report, statePath, reportPath);
  validateUnusedArtifactDisposition(state, report, statePath, reportPath);
  validateOrchestration(state, report, statePath, reportPath);
  validatePerformanceExecution(state, report, statePath, reportPath);
  await validateRuntimeDriverPackaging(state, report, statePath, reportPath);
  validateConversionScope(state, report, statePath, reportPath);
  validateWaves(state, statePath);
  validateFixturesAndObjects(state, report, statePath, reportPath);
  validateCoverage(state, report, statePath);
  validateArtifactRecord(state, report, diffs, statePath, reportPath);
}

function validateRunMetrics(state, report, statePath, reportPath) {
  let expected;
  try {
    expected = renderRunMetrics(state.runMetrics);
  } catch (error) {
    if (!(error instanceof TypeError || error instanceof RangeError)) throw error;
    errors.push(`${statePath}: invalid Run Metrics: ${error.message}`);
    return;
  }
  runMetricsReports.push(expected);
  const lines = report.replace(/\r\n/g, "\n").split("\n");
  const heading = "### Run Metrics";
  if (lines.filter((line) => line === heading).length !== 1) {
    errors.push(`${reportPath}: exactly one Run Metrics section is required`);
    return;
  }
  const start = lines.indexOf(heading);
  const end = lines.findIndex(
    (line, index) =>
      index > start && (/^#{1,3} /.test(line) || line.startsWith("[Back to Top]")),
  );
  const actual = lines.slice(start, end < 0 ? lines.length : end).join("\n").trim();
  if (actual !== expected) {
    errors.push(`${reportPath}: Run Metrics must match recorded timings, the local time zone, and the Assessment/Conversion/Total Runs table format`);
  }
}

function validateUnusedArtifactDisposition(state, report, statePath, reportPath) {
  const optionEnabled = state?.options?.["remove-unused-artifacts"];
  const evidence = state?.validation?.unusedArtifactDisposition;
  if (
    typeof optionEnabled !== "boolean" ||
    !evidence ||
    evidence.optionEnabled !== optionEnabled ||
    !Array.isArray(evidence.candidates) ||
    !Array.isArray(evidence.deletedPaths)
  ) {
    errors.push(`${statePath}: unused-artifact disposition evidence is required`);
    return;
  }
  const candidatePaths = new Set();
  const removedPaths = new Set();
  for (const candidate of evidence.candidates) {
    if (
      !candidate?.path ||
      candidatePaths.has(candidate.path) ||
      !candidate?.kind ||
      normalizeStatus(candidate?.classification) !== "unused" ||
      !candidate?.beforeDigest ||
      !Array.isArray(candidate?.unusedEvidence) ||
      candidate.unusedEvidence.length === 0 ||
      !Number.isInteger(candidate?.activeReferences) ||
      candidate.activeReferences < 0 ||
      typeof candidate?.requiredByBuild !== "boolean" ||
      typeof candidate?.requiredByPackage !== "boolean"
    ) {
      errors.push(`${statePath}: unused-artifact candidate is incomplete or duplicated`);
      continue;
    }
    candidatePaths.add(candidate.path);
    if (!report.includes(candidate.path)) {
      errors.push(`${reportPath}: unused artifact ${candidate.path} is missing`);
    }
    if (!optionEnabled) {
      if (
        normalizeStatus(candidate.action) !== "retained" ||
        candidate.removed !== false
      ) {
        errors.push(
          `${statePath}: unused artifact was removed while remove-unused-artifacts was off: ${candidate.path}`,
        );
      }
      continue;
    }
    if (candidate.removed === true) {
      removedPaths.add(candidate.path);
      const post = candidate.postRemovalValidation;
      if (
        normalizeStatus(candidate.action) !== "removed" ||
        candidate.activeReferences !== 0 ||
        candidate.requiredByBuild !== false ||
        candidate.requiredByPackage !== false ||
        !candidate.rollback ||
        post?.buildPassed !== true ||
        post?.testsPassed !== true ||
        post?.packagePassed !== true ||
        post?.archiveInspectionPassed !== true
      ) {
        errors.push(`${statePath}: removed unused artifact lacks safety evidence: ${candidate.path}`);
      }
    } else if (normalizeStatus(candidate.action) !== "retained") {
      errors.push(`${statePath}: unused artifact action is invalid: ${candidate.path}`);
    }
  }
  if (
    evidence.deletedPaths.length !== removedPaths.size ||
    evidence.deletedPaths.some((deletedPath) => !removedPaths.has(deletedPath))
  ) {
    errors.push(`${statePath}: unused-artifact deletedPaths do not reconcile`);
  }
  if (
    !optionEnabled &&
    (evidence.deletedPaths.length > 0 || removedPaths.size > 0)
  ) {
    errors.push(`${statePath}: remove-unused-artifacts is off but deleted paths were recorded`);
  }
  const actualDeletedArtifacts = readDeletedRepositoryArtifacts(state);
  for (const deletedPath of actualDeletedArtifacts) {
    if (!optionEnabled) {
      errors.push(
        `${statePath}: repository artifact was deleted while remove-unused-artifacts was off: ${deletedPath}`,
      );
    }
    if (!candidatePaths.has(deletedPath) || !removedPaths.has(deletedPath)) {
      errors.push(
        `${statePath}: deleted repository artifact lacks reconciled removal evidence: ${deletedPath}`,
      );
    }
  }
  if (
    evidence.candidates.length === 0 &&
    !report.includes("| None detected |")
  ) {
    errors.push(`${reportPath}: empty unused-artifact inventory lacks a None detected row`);
  }
}

function readDeletedRepositoryArtifacts(state) {
  const startingCommit =
    state?.repository?.startingCommit ??
    state?.source?.commit ??
    "HEAD";
  const result = spawnSync(
    "git",
    [
      "-C",
      root,
      "--no-pager",
      "diff",
      "--name-only",
      "--diff-filter=D",
      startingCommit,
      "--",
    ],
    { encoding: "utf8", windowsHide: true },
  );
  if (result.status !== 0) return [];
  return result.stdout
    .split(/\r?\n/)
    .map((entry) => entry.trim().replaceAll("\\", "/"))
    .filter(
      (entry) =>
        entry &&
        /\.(?:jar|war|ear|zip|dll|so|dylib|a|lib|nupkg|whl)$/i.test(entry),
    );
}

async function validateRuntimeDriverPackaging(
  state,
  report,
  statePath,
  reportPath,
) {
  const descriptors = await findPostgresqlRuntimeDescriptors(root);
  const packaging = state?.validation?.runtimeDriverPackaging;
  const reportSection = markdownSection(
    report,
    "Runtime Driver Packaging Reconciliation",
  );
  if (!packaging || typeof packaging.required !== "boolean") {
    errors.push(`${statePath}: runtimeDriverPackaging evidence is required`);
    return;
  }
  if (descriptors.length === 0) {
    if (
      packaging.required !== false ||
      normalizeStatus(packaging.status) !== "not applicable" ||
      !packaging.reason
    ) {
      errors.push(`${statePath}: runtime driver packaging is not correctly marked not applicable`);
    }
    if (!reportSection.includes("| None |")) {
      errors.push(`${reportPath}: not-applicable runtime driver packaging row is missing`);
    }
    return;
  }
  if (
    packaging.required !== true ||
    normalizeStatus(packaging.status) !== "passed" ||
    !packaging.planPath ||
    !Array.isArray(packaging.descriptorSourcePaths) ||
    descriptors.some(
      (descriptor) => !packaging.descriptorSourcePaths.includes(descriptor),
    )
  ) {
    errors.push(`${statePath}: PostgreSQL runtime descriptor packaging evidence is incomplete`);
    return;
  }
  const planPath = safeArtifactPath(packaging.planPath, statePath);
  if (!planPath) return;
  let plan;
  try {
    plan = JSON.parse(await readFile(planPath, "utf8"));
  } catch (error) {
    errors.push(`${statePath}: driver packaging plan is missing or invalid (${error.message})`);
    return;
  }
  let actual;
  try {
    actual = await inspectDriverPackaging(root, plan);
  } catch (error) {
    errors.push(`${statePath}: driver packaging inspection failed (${error.message})`);
    return;
  }
  if (
    actual.status !== "passed" ||
    actual.archives.length === 0 ||
    actual.archives.some(
      (archive) =>
        archive.status !== "passed" ||
        archive.warOnlyDriver ||
        archive.archiveDriverEntries.length === 0 ||
        archive.descriptorResults.some(
          (descriptor) =>
            descriptor.status !== "passed" ||
            descriptor.matchingDriverEntries.length === 0,
        ),
    )
  ) {
    errors.push(
      `${statePath}: deployment archive does not satisfy runtime PostgreSQL driver paths: ${actual.errors.join("; ")}`,
    );
  }
  if (
    packaging.archivesInspected !== actual.archives.length ||
    packaging.warOnlyDriverDetected !== false
  ) {
    errors.push(`${statePath}: runtime driver packaging counters do not reconcile`);
  }
  for (const archive of actual.archives) {
    if (!reportSection.includes(archive.path)) {
      errors.push(`${reportPath}: driver packaging archive ${archive.path} is missing`);
    }
    for (const descriptor of archive.descriptorResults) {
      if (
        !reportSection.includes(descriptor.sourcePath) ||
        !reportSection.includes(descriptor.entry) ||
        descriptor.runtimeDriverPatterns.some(
          (pattern) => !reportSection.includes(pattern),
        ) ||
        descriptor.matchingDriverEntries.some(
          (entry) => !reportSection.includes(entry),
        )
      ) {
        errors.push(
          `${reportPath}: descriptor-to-driver evidence is incomplete for ${descriptor.entry}`,
        );
      }
    }
    for (const nestedEntry of archive.nestedWarDrivers) {
      if (!reportSection.includes(nestedEntry)) {
        errors.push(
          `${reportPath}: nested WAR driver evidence is missing for ${nestedEntry}`,
        );
      }
    }
  }
}

async function findPostgresqlRuntimeDescriptors(codebaseRoot) {
  const results = [];
  await walkRuntimeDescriptors(codebaseRoot, codebaseRoot, results);
  return results.sort();
}

async function walkRuntimeDescriptors(codebaseRoot, directory, results) {
  let entries;
  try {
    entries = await readdir(directory, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (
      entry.isDirectory() &&
      [
        ".git",
        ".github",
        "node_modules",
        "target",
        "build",
        "dist",
        "bin",
        "obj",
      ].includes(entry.name)
    ) {
      continue;
    }
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      await walkRuntimeDescriptors(codebaseRoot, absolute, results);
      continue;
    }
    if (
      !entry.isFile() ||
      !/\.(?:xml|properties|ya?ml|conf|config)$/i.test(entry.name)
    ) {
      continue;
    }
    try {
      const metadata = await stat(absolute);
      if (metadata.size > 1_000_000) continue;
      const content = await readFile(absolute, "utf8");
      if (
        /<fileset\b[^>]*(?:postgresql|pgjdbc)[^>]*\.jar[^>]*>/i.test(
          content,
        ) &&
        /<(?:library|jdbcDriver)\b/i.test(content)
      ) {
        results.push(
          path.relative(codebaseRoot, absolute).replaceAll("\\", "/"),
        );
      }
    } catch {
      // Other validation surfaces report unreadable source/configuration files.
    }
  }
}

function markdownSection(report, heading) {
  const match = report.match(
    new RegExp(
      `^### ${escapeRegExp(heading)}\\s*$([\\s\\S]*?)(?=^#{2,3} |\\Z)`,
      "m",
    ),
  );
  return match?.[1] ?? "";
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function validateTargetMetadataAcquisition(state, statePath) {
  const evidence = state?.targetMetadataAcquisition;
  if (!evidence || typeof evidence !== "object") {
    errors.push(`${statePath}: targetMetadataAcquisition evidence is required`);
    return;
  }
  const prohibitedTools = [
    "postgres_mcp_db_context",
    "pgsql_db_context",
  ];
  if (
    !Array.isArray(evidence.dbContextToolsProhibited) ||
    prohibitedTools.some(
      (tool) => !evidence.dbContextToolsProhibited.includes(tool),
    )
  ) {
    errors.push(`${statePath}: all PostgreSQL _db_context tools must be explicitly prohibited`);
  }
  const toolCallCounts = evidence.toolCallCounts;
  const expectedQueryTool =
    state?.postgresProvider?.toolFamily === "pgsql"
      ? "pgsql_query"
      : state?.postgresProvider?.toolFamily === "postgres_mcp"
        ? "postgres_mcp_query"
        : null;
  const otherQueryTool =
    expectedQueryTool === "pgsql_query"
      ? "postgres_mcp_query"
      : "pgsql_query";
  if (!toolCallCounts || typeof toolCallCounts !== "object") {
    errors.push(`${statePath}: target metadata toolCallCounts are required`);
  } else {
    for (const [tool, count] of Object.entries(toolCallCounts)) {
      if (!Number.isInteger(count) || count < 0) {
        errors.push(`${statePath}: invalid target metadata tool count for ${tool}`);
      }
      if (tool.endsWith("_db_context") && count !== 0) {
        errors.push(
          `${statePath}: prohibited target metadata call recorded for ${tool}`,
        );
      }
    }
    for (const tool of prohibitedTools) {
      if (toolCallCounts[tool] !== 0) {
        errors.push(`${statePath}: ${tool} call count must be recorded as zero`);
      }
    }
  }
  for (const field of [
    "dbContextCalls",
    "schemaWideCatalogQueries",
    "broadInformationSchemaQueries",
    "catalogSelectStarQueries",
  ]) {
    if (evidence[field] !== 0) {
      errors.push(`${statePath}: targetMetadataAcquisition.${field} must be zero`);
    }
  }
  if (
    !Array.isArray(evidence.policyViolations) ||
    evidence.policyViolations.length !== 0
  ) {
    errors.push(`${statePath}: target metadata policy violations must be empty`);
  }
  if (!Array.isArray(evidence.queries) || !Array.isArray(evidence.timeouts)) {
    errors.push(`${statePath}: target metadata queries/timeouts are required arrays`);
    return;
  }

  const strategy = normalizeStatus(evidence.strategy);
  if (strategy === "not requested") {
    if (
      evidence.queryCalls !== 0 ||
      evidence.queries.length !== 0 ||
      !expectedQueryTool ||
      toolCallCounts?.[expectedQueryTool] !== 0 ||
      (toolCallCounts?.[otherQueryTool] ?? 0) !== 0 ||
      typeof evidence.reason !== "string" ||
      !evidence.reason.trim()
    ) {
      errors.push(`${statePath}: not-requested target metadata evidence is incomplete`);
    }
    return;
  }
  if (strategy !== "code derived bounded catalog queries") {
    errors.push(
      `${statePath}: target metadata strategy must be code-derived-bounded-catalog-queries`,
    );
    return;
  }

  if (
    !Number.isInteger(evidence.manifestObjectCount) ||
    evidence.manifestObjectCount < 0 ||
    !Number.isInteger(evidence.identityBatchLimit) ||
    evidence.identityBatchLimit < 1 ||
    evidence.identityBatchLimit > 20 ||
    !Number.isInteger(evidence.detailBatchLimit) ||
    evidence.detailBatchLimit < 1 ||
    evidence.detailBatchLimit > 10 ||
    !Number.isInteger(evidence.queryCalls) ||
    evidence.queryCalls < 1 ||
    evidence.queryCalls !== evidence.queries.length ||
    !expectedQueryTool ||
    toolCallCounts?.[expectedQueryTool] !== evidence.queryCalls ||
    (toolCallCounts?.[otherQueryTool] ?? 0) !== 0
  ) {
    errors.push(`${statePath}: target metadata query counts or batch limits are invalid`);
  }

  const queryIds = new Set();
  for (const query of evidence.queries) {
    const status = normalizeStatus(query?.status);
    if (
      typeof query?.id !== "string" ||
      !query.id.trim() ||
      queryIds.has(query.id) ||
      typeof query?.purpose !== "string" ||
      !query.purpose.trim() ||
      !["scalar", "exact-name", "oid"].includes(query?.boundedBy) ||
      !Number.isInteger(query?.objectCount) ||
      query.objectCount < 0 ||
      !Number.isInteger(query?.oidCount) ||
      query.oidCount < 0 ||
      !Number.isInteger(query?.maxExpectedRows) ||
      query.maxExpectedRows < 1 ||
      query.usesSelectStar !== false ||
      query.usesInformationSchema !== false ||
      !/^[a-f0-9]{64}$/i.test(query?.queryDigest ?? "") ||
      !isIsoDate(query?.startedAt) ||
      !isIsoDate(query?.completedAt) ||
      !Number.isFinite(query?.elapsedMs) ||
      query.elapsedMs < 0 ||
      !["passed", "failed", "timed out"].includes(status)
    ) {
      errors.push(`${statePath}: target metadata query evidence is incomplete or duplicated`);
      continue;
    }
    queryIds.add(query.id);
    if (
      (query.boundedBy === "scalar" &&
        (query.objectCount !== 0 ||
          query.oidCount !== 0 ||
          query.maxExpectedRows > 10)) ||
      (query.boundedBy === "exact-name" &&
        (query.objectCount < 1 ||
          query.objectCount > evidence.identityBatchLimit ||
          query.oidCount !== 0 ||
          query.maxExpectedRows > 25)) ||
      (query.boundedBy === "oid" &&
        (query.objectCount !== 0 ||
          query.oidCount < 1 ||
          query.oidCount > evidence.detailBatchLimit ||
          query.maxExpectedRows > 500))
    ) {
      errors.push(`${statePath}: target metadata query ${query.id} exceeds its bounded scope`);
    }
  }
  const timeoutIds = new Set(
    evidence.timeouts
      .map((timeout) => timeout?.queryId)
      .filter((queryId) => typeof queryId === "string"),
  );
  for (const query of evidence.queries) {
    if (normalizeStatus(query?.status) === "timed out" && !timeoutIds.has(query.id)) {
      errors.push(`${statePath}: timed-out target metadata query ${query.id} lacks timeout evidence`);
    }
  }
}

async function validateOracleDdlSource(
  state,
  report,
  statePath,
  reportPath,
  phase,
) {
  const evidence = state?.oracleDdlSource;
  if (!evidence || typeof evidence !== "object") {
    errors.push(`${statePath}: oracleDdlSource evidence is required`);
    return;
  }
  if (
    typeof evidence.supplied !== "boolean" ||
    !Array.isArray(evidence.files) ||
    !Array.isArray(evidence.errors) ||
    !Array.isArray(evidence.warnings) ||
    !Array.isArray(evidence.missingTargetObjects) ||
    !Array.isArray(evidence.missingObjectResolutions)
  ) {
    errors.push(`${statePath}: oracleDdlSource evidence is incomplete`);
    return;
  }

  const status = normalizeStatus(evidence.status);
  if (!evidence.supplied) {
    if (
      status !== "not supplied" ||
      evidence.folderPath !== null ||
      evidence.folderDigest !== null ||
      evidence.indexPath !== null ||
      evidence.indexSha256 !== null ||
      evidence.files.length !== 0 ||
      evidence.errors.length !== 0 ||
      evidence.warnings.length !== 0
    ) {
      errors.push(`${statePath}: unsupplied oracle-ddl-folder evidence is inconsistent`);
    }
  } else {
    if (
      typeof evidence.folderPath !== "string" ||
      !path.isAbsolute(evidence.folderPath) ||
      typeof evidence.schema !== "string" ||
      !evidence.schema.trim() ||
      typeof evidence.resolvedSchemaPath !== "string" ||
      !path.isAbsolute(evidence.resolvedSchemaPath) ||
      typeof evidence.relativeSchemaPath !== "string" ||
      !evidence.relativeSchemaPath ||
      ![
        "canonical-postgres-migrations",
        "schema-object-type-folders",
      ].includes(evidence.layout) ||
      !(
        evidence.migrationName === null ||
        (typeof evidence.migrationName === "string" &&
          evidence.migrationName.trim())
      ) ||
      (evidence.layout === "canonical-postgres-migrations" &&
        (typeof evidence.migrationName !== "string" ||
          !evidence.migrationName.trim())) ||
      !Array.isArray(evidence.objectTypeDirectories) ||
      evidence.objectTypeDirectories.length === 0 ||
      !Array.isArray(evidence.discoveredSchemaPaths) ||
      !evidence.limits ||
      typeof evidence.limits !== "object" ||
      !Number.isInteger(evidence.limits.maxFiles) ||
      !Number.isInteger(evidence.limits.maxFileBytes) ||
      !Number.isInteger(evidence.limits.maxTotalBytes) ||
      !Number.isInteger(evidence.limits.maxLayoutDepth) ||
      !Number.isInteger(evidence.limits.maxLayoutDirectories) ||
      evidence.limits.maxFiles < 1 ||
      evidence.limits.maxFiles > 10_000 ||
      evidence.limits.maxFileBytes < 1 ||
      evidence.limits.maxFileBytes > 10_000_000 ||
      evidence.limits.maxTotalBytes < 1 ||
      evidence.limits.maxTotalBytes > 250_000_000 ||
      evidence.limits.maxLayoutDepth < 1 ||
      evidence.limits.maxLayoutDepth > 12 ||
      evidence.limits.maxLayoutDirectories < 1 ||
      evidence.limits.maxLayoutDirectories > 50_000 ||
      !Number.isInteger(evidence.totalBytes) ||
      evidence.totalBytes < 0 ||
      !/^[a-f0-9]{64}$/i.test(evidence.folderDigest ?? "") ||
      typeof evidence.indexPath !== "string" ||
      !evidence.indexPath.trim() ||
      !/^[a-f0-9]{64}$/i.test(evidence.indexSha256 ?? "") ||
      !Number.isInteger(evidence.candidateFiles) ||
      evidence.candidateFiles < 0 ||
      !Number.isInteger(evidence.indexedObjects) ||
      evidence.indexedObjects < 0 ||
      !["valid", "invalid"].includes(status)
    ) {
      errors.push(`${statePath}: supplied Oracle DDL source metadata is incomplete`);
    }
    const objectTypeDirectoryNames = new Set();
    for (const objectType of evidence.objectTypeDirectories ?? []) {
      const normalizedName = objectType?.name?.toLowerCase();
      if (
        typeof objectType?.name !== "string" ||
        !objectType.name.trim() ||
        objectTypeDirectoryNames.has(normalizedName) ||
        typeof objectType?.relativePath !== "string" ||
        !objectType.relativePath.trim() ||
        !Array.isArray(objectType?.expectedObjectKinds)
      ) {
        errors.push(`${statePath}: Oracle DDL object-type directory evidence is incomplete or duplicated`);
        continue;
      }
      objectTypeDirectoryNames.add(normalizedName);
    }
    const relativeResolvedSchema = path
      .relative(evidence.folderPath, evidence.resolvedSchemaPath)
      .replaceAll("\\", "/") || ".";
    if (
      relativeResolvedSchema.startsWith("..") ||
      path.isAbsolute(relativeResolvedSchema) ||
      relativeResolvedSchema !== evidence.relativeSchemaPath
    ) {
      errors.push(`${statePath}: resolved Oracle DDL schema path is outside or inconsistent with the supplied root`);
    }
    if (
      (status === "valid" && evidence.errors.length !== 0) ||
      (status === "invalid" && evidence.errors.length === 0)
    ) {
      errors.push(`${statePath}: Oracle DDL source status does not match index errors`);
    }
    try {
      const fresh = await indexOracleDdlFolder(evidence.folderPath, {
        schema: evidence.schema,
        ...evidence.limits,
      });
      if (
        normalizeStatus(fresh.status) !== status ||
        fresh.schema !== evidence.schema ||
        fresh.resolvedSchemaPath !== evidence.resolvedSchemaPath ||
        fresh.relativeSchemaPath !== evidence.relativeSchemaPath ||
        fresh.layout !== evidence.layout ||
        fresh.migrationName !== evidence.migrationName ||
        JSON.stringify(fresh.objectTypeDirectories) !==
          JSON.stringify(evidence.objectTypeDirectories) ||
        JSON.stringify(fresh.discoveredSchemaPaths) !==
          JSON.stringify(evidence.discoveredSchemaPaths) ||
        JSON.stringify(fresh.limits) !== JSON.stringify(evidence.limits) ||
        fresh.totalBytes !== evidence.totalBytes ||
        fresh.folderDigest !== evidence.folderDigest ||
        fresh.indexedObjects !== evidence.indexedObjects ||
        fresh.candidateFiles !== evidence.candidateFiles ||
        JSON.stringify(fresh.files) !== JSON.stringify(evidence.files) ||
        JSON.stringify(fresh.errors) !== JSON.stringify(evidence.errors) ||
        JSON.stringify(fresh.warnings) !== JSON.stringify(evidence.warnings)
      ) {
        errors.push(`${statePath}: Oracle DDL source folder/index evidence is stale`);
      }
    } catch (error) {
      errors.push(`${statePath}: Oracle DDL source cannot be re-indexed (${error.message})`);
    }
    const indexAbsolutePath = path.resolve(root, evidence.indexPath);
    if (
      indexAbsolutePath !== root &&
      !indexAbsolutePath.startsWith(`${root}${path.sep}`)
    ) {
      errors.push(`${statePath}: Oracle DDL index path escapes the codebase`);
    } else {
      try {
        const indexText = await readFile(indexAbsolutePath, "utf8");
        const index = JSON.parse(indexText);
        if (
          sha256(indexText) !== evidence.indexSha256 ||
          index.folderDigest !== evidence.folderDigest ||
          index.schema !== evidence.schema ||
          index.resolvedSchemaPath !== evidence.resolvedSchemaPath ||
          index.relativeSchemaPath !== evidence.relativeSchemaPath ||
          index.layout !== evidence.layout ||
          index.migrationName !== evidence.migrationName ||
          JSON.stringify(index.objectTypeDirectories) !==
            JSON.stringify(evidence.objectTypeDirectories) ||
          JSON.stringify(index.discoveredSchemaPaths) !==
            JSON.stringify(evidence.discoveredSchemaPaths) ||
          JSON.stringify(index.limits) !== JSON.stringify(evidence.limits) ||
          index.totalBytes !== evidence.totalBytes ||
          JSON.stringify(index.files) !== JSON.stringify(evidence.files) ||
          JSON.stringify(index.errors) !== JSON.stringify(evidence.errors) ||
          JSON.stringify(index.warnings) !== JSON.stringify(evidence.warnings)
        ) {
          errors.push(`${statePath}: persisted Oracle DDL index evidence is stale`);
        }
      } catch (error) {
        errors.push(`${statePath}: Oracle DDL index cannot be read (${error.message})`);
      }
    }
  }

  const filesByPath = new Map();
  const filesByObject = new Map();
  const indexedObjectTypeNames = new Set(
    (evidence.objectTypeDirectories ?? []).map((objectType) =>
      objectType.name.toLowerCase(),
    ),
  );
  for (const file of evidence.files) {
    if (
      typeof file?.relativePath !== "string" ||
      !file.relativePath ||
      filesByPath.has(file.relativePath) ||
      typeof file?.objectTypeDirectory !== "string" ||
      !indexedObjectTypeNames.has(file.objectTypeDirectory.toLowerCase()) ||
      !Array.isArray(file?.expectedObjectKinds) ||
      !["ddl-primary-definition", "folder-and-filename"].includes(
        file?.identitySource,
      ) ||
      typeof file?.identityKey !== "string" ||
      !file.identityKey ||
      typeof file?.objectKey !== "string" ||
      !file.objectKey ||
      typeof file?.objectKind !== "string" ||
      !/^[a-f0-9]{64}$/i.test(file?.sha256 ?? "") ||
      !Number.isInteger(file?.bytes) ||
      file.bytes < 0 ||
      !Array.isArray(file?.statementKinds) ||
      !Array.isArray(file?.packageMembers) ||
      !Array.isArray(file?.auxiliaryDefinitions)
    ) {
      errors.push(`${statePath}: Oracle DDL indexed file evidence is incomplete or duplicated`);
      continue;
    }
    filesByPath.set(file.relativePath, file);
    const objectFiles = filesByObject.get(file.objectKey) ?? [];
    objectFiles.push(file);
    filesByObject.set(file.objectKey, objectFiles);
  }
  for (const [objectKey, objectFiles] of filesByObject) {
    const componentKinds = objectFiles.flatMap((file) => file.statementKinds);
    const validPackageCompanions =
      objectFiles.every((file) => file.objectKind === "package") &&
      componentKinds.every((kind) =>
        ["package", "package body"].includes(kind),
      ) &&
      componentKinds.length === new Set(componentKinds).size;
    if (objectFiles.length > 1 && !validPackageCompanions) {
      errors.push(
        `${statePath}: Oracle DDL object ${objectKey} has duplicate source files`,
      );
    }
  }
  if (evidence.supplied && evidence.indexedObjects !== filesByObject.size) {
    errors.push(`${statePath}: Oracle DDL indexed object count does not match object identities`);
  }

  const expectedTargets = new Map();
  for (const missing of evidence.missingTargetObjects) {
    if (
      typeof missing?.targetObject !== "string" ||
      !missing.targetObject ||
      expectedTargets.has(missing.targetObject) ||
      typeof missing?.targetKind !== "string" ||
      !missing.targetKind ||
      typeof missing?.oracleSourceIdentity !== "string" ||
      !missing.oracleSourceIdentity ||
      !(
        missing.oracleMemberName === null ||
        (typeof missing.oracleMemberName === "string" &&
          missing.oracleMemberName.trim())
      ) ||
      !Array.isArray(missing?.applicationCallSites) ||
      missing.applicationCallSites.length === 0 ||
      missing.applicationCallSites.some(
        (callSite) => typeof callSite !== "string" || !callSite.trim(),
      ) ||
      new Set(missing.applicationCallSites).size !==
        missing.applicationCallSites.length
    ) {
      errors.push(`${statePath}: missing target object manifest is incomplete or duplicated`);
      continue;
    }
    expectedTargets.set(missing.targetObject, missing);
  }

  const targets = new Set();
  if (report) {
    const tableHeader =
      phase === "assessment"
        ? "| Missing PostgreSQL object | Oracle source object | Source DDL file / digest | Reconstruction provenance | Body completeness / confidence | Customer validation |"
        : "| Missing PostgreSQL object | Oracle source / reconstruction provenance | Source DDL file / digest | Body completeness / confidence | PostgreSQL DDL and application call updates | Synthetic Docker validation | Persistent target result / customer validation |";
    requireIncludes(report, tableHeader, reportPath);
  }
  let requiresCustomerValidation = false;
  for (const resolution of evidence.missingObjectResolutions) {
    const resolutionStatus = normalizeStatus(resolution?.resolutionStatus);
    if (
      typeof resolution?.targetObject !== "string" ||
      !resolution.targetObject ||
      targets.has(resolution.targetObject) ||
      typeof resolution?.targetKind !== "string" ||
      !["converted", "action needed"].includes(resolutionStatus)
    ) {
      errors.push(`${statePath}: missing-object Oracle DDL resolution is incomplete or duplicated`);
      continue;
    }
    targets.add(resolution.targetObject);
    const missing = expectedTargets.get(resolution.targetObject);
    if (!missing || missing.targetKind !== resolution.targetKind) {
      errors.push(
        `${statePath}: missing-object resolution is absent from or disagrees with the target manifest: ${resolution.targetObject}`,
      );
    }
    if (report && !report.includes(`| ${resolution.targetObject} |`)) {
      errors.push(
        `${reportPath}: missing Oracle DDL reconciliation row for ${resolution.targetObject}`,
      );
    }
    if (resolutionStatus === "action needed") {
      if (
        resolution.generatedDeployPath !== null ||
        resolution.generatedDeploySha256 !== null ||
        resolution.generatedUndoPath !== null ||
        resolution.generatedUndoSha256 !== null ||
        !Array.isArray(resolution.applicationCallSitesUpdated) ||
        resolution.applicationCallSitesUpdated.length !== 0 ||
        resolution.dockerValidated !== false ||
        resolution.persistentCreateAttempted !== false ||
        resolution.persistentCreateVerified !== false ||
        typeof resolution.unresolvedReason !== "string" ||
        !resolution.unresolvedReason.trim()
      ) {
        errors.push(
          `${statePath}: action-needed missing object contains invalid generated implementation evidence`,
        );
      }
      continue;
    }

    const semanticsSource = normalizeStatus(resolution.semanticsSource);
    const bodyCompleteness = normalizeStatus(resolution.bodyCompleteness);
    const semanticConfidence = normalizeStatus(resolution.semanticConfidence);
    const inferred = [
      "best effort reconstruction",
      "signature only fail closed",
    ].includes(semanticsSource);
    const synthetic = resolution.syntheticValidation;
    if (
      ![
        "oracle ddl",
        "code and tests",
        "best effort reconstruction",
        "signature only fail closed",
      ].includes(semanticsSource) ||
      resolution.signatureReconciled !== true ||
      !Array.isArray(resolution.behaviorEvidence) ||
      resolution.behaviorEvidence.length === 0 ||
      typeof resolution.sourceBodyAvailable !== "boolean" ||
      !["complete", "inferred", "missing fail closed"].includes(
        bodyCompleteness,
      ) ||
      !["high", "medium", "low"].includes(semanticConfidence) ||
      typeof resolution.businessSemanticsProven !== "boolean" ||
      typeof resolution.generatedFromInference !== "boolean" ||
      !Array.isArray(resolution.assumptions) ||
      !Array.isArray(resolution.limitations) ||
      typeof resolution.generatedDeployPath !== "string" ||
      !resolution.generatedDeployPath ||
      !/^[a-f0-9]{64}$/i.test(resolution.generatedDeploySha256 ?? "") ||
      typeof resolution.generatedUndoPath !== "string" ||
      !resolution.generatedUndoPath ||
      !/^[a-f0-9]{64}$/i.test(resolution.generatedUndoSha256 ?? "") ||
      !Array.isArray(resolution.applicationCallSitesUpdated) ||
      resolution.applicationCallSitesUpdated.length === 0 ||
      resolution.applicationCallSitesUpdated.some(
        (callSite) => typeof callSite !== "string" || !callSite.trim(),
      ) ||
      new Set(resolution.applicationCallSitesUpdated).size !==
        resolution.applicationCallSitesUpdated.length ||
      missing?.applicationCallSites?.some(
        (callSite) =>
          !resolution.applicationCallSitesUpdated.includes(callSite),
      ) ||
      resolution.dockerValidated !== true ||
      !synthetic ||
      !Number.isInteger(synthetic.syntheticInputsCreated) ||
      synthetic.syntheticInputsCreated < 1 ||
      !Number.isInteger(synthetic.fixtureRowsCreated) ||
      synthetic.fixtureRowsCreated < 0 ||
      synthetic.fixtureRowsRemaining !== 0 ||
      synthetic.productionPathInvoked !== true ||
      !Array.isArray(synthetic.assertions) ||
      synthetic.assertions.length === 0 ||
      normalizeStatus(synthetic.status) !== "passed" ||
      !(
        synthetic.limitation === null ||
        (typeof synthetic.limitation === "string" &&
          synthetic.limitation.trim())
      ) ||
      typeof resolution.customerValidationRequired !== "boolean" ||
      !(
        resolution.customerValidationAction === null ||
        (typeof resolution.customerValidationAction === "string" &&
          resolution.customerValidationAction.trim())
      ) ||
      typeof resolution.persistentCreateRequested !== "boolean" ||
      typeof resolution.persistentCreateAttempted !== "boolean" ||
      typeof resolution.persistentCreateVerified !== "boolean" ||
      (resolution.persistentCreateVerified &&
        !resolution.persistentCreateAttempted)
    ) {
      errors.push(`${statePath}: converted missing object lacks complete semantic/Docker evidence`);
    }
    if (
      (semanticsSource === "oracle ddl" &&
        (!resolution.sourceBodyAvailable ||
          bodyCompleteness !== "complete" ||
          resolution.generatedFromInference ||
          !resolution.businessSemanticsProven)) ||
      (semanticsSource === "code and tests" &&
        (resolution.sourceBodyAvailable ||
          bodyCompleteness !== "inferred" ||
          !resolution.generatedFromInference ||
          !resolution.businessSemanticsProven)) ||
      (inferred &&
        (resolution.sourceBodyAvailable ||
          resolution.businessSemanticsProven ||
          !resolution.generatedFromInference ||
          !["medium", "low"].includes(semanticConfidence) ||
          !resolution.customerValidationRequired ||
          typeof resolution.customerValidationAction !== "string" ||
          !resolution.customerValidationAction.trim() ||
          resolution.limitations.length === 0 ||
          typeof synthetic?.limitation !== "string" ||
          !synthetic.limitation.trim())) ||
      (semanticsSource === "best effort reconstruction" &&
        bodyCompleteness !== "inferred") ||
      (semanticsSource === "signature only fail closed" &&
        (bodyCompleteness !== "missing fail closed" ||
          semanticConfidence !== "low"))
    ) {
      errors.push(
        `${statePath}: ${resolution.targetObject} provenance/body-confidence evidence is inconsistent`,
      );
    }
    requiresCustomerValidation ||= resolution.customerValidationRequired === true;
    if (report) {
      const reportRow = report
        .split(/\r?\n/)
        .find((line) => line.startsWith(`| ${resolution.targetObject} |`));
      if (
        !reportRow ||
        !normalizeStatus(reportRow).includes(semanticsSource) ||
        !normalizeStatus(reportRow).includes(bodyCompleteness) ||
        !normalizeStatus(reportRow).includes(semanticConfidence) ||
        (resolution.customerValidationRequired &&
          !plainMarkdown(reportRow).includes(
            resolution.customerValidationAction,
          ))
      ) {
        errors.push(
          `${reportPath}: ${resolution.targetObject} row omits reconstruction provenance/body/customer validation`,
        );
      }
    }

    let generatedDeployContent = "";
    for (const [pathField, digestField] of [
      ["generatedDeployPath", "generatedDeploySha256"],
      ["generatedUndoPath", "generatedUndoSha256"],
    ]) {
      const artifactPath = safeArtifactPath(
        resolution[pathField],
        `${statePath}: ${resolution.targetObject}`,
      );
      if (!artifactPath) continue;
      try {
        const content = await readFile(artifactPath, "utf8");
        if (pathField === "generatedDeployPath") {
          generatedDeployContent = content;
        }
        if (!content.trim() || sha256(content) !== resolution[digestField]) {
          errors.push(
            `${statePath}: ${resolution.targetObject} ${pathField} is empty or stale`,
          );
        }
      } catch (error) {
        errors.push(
          `${statePath}: ${resolution.targetObject} ${pathField} cannot be read (${error.message})`,
        );
      }
    }
    for (const callSite of resolution.applicationCallSitesUpdated ?? []) {
      const callSitePath = safeArtifactPath(
        callSite,
        `${statePath}: ${resolution.targetObject}`,
      );
      if (!callSitePath) continue;
      try {
        const metadata = await stat(callSitePath);
        if (!metadata.isFile()) {
          errors.push(
            `${statePath}: reconciled application call site is not a file: ${callSite}`,
          );
        }
      } catch (error) {
        errors.push(
          `${statePath}: reconciled application call site is missing: ${callSite} (${error.message})`,
        );
      }
    }
    if (
      semanticsSource === "signature only fail closed" &&
      !/\bRAISE\s+EXCEPTION\b|\bRAISE_APPLICATION_ERROR\b/i.test(
        generatedDeployContent,
      )
    ) {
      errors.push(
        `${statePath}: ${resolution.targetObject} missing-body implementation is not explicitly fail-closed`,
      );
    }
    if (semanticsSource === "oracle ddl") {
      const source = filesByPath.get(resolution.sourceRelativePath);
      const memberMatched =
        missing?.oracleMemberName === null ||
        source?.packageMembers?.some(
          (member) =>
            member.name.toLowerCase() ===
            missing.oracleMemberName.toLowerCase(),
        );
      if (
        !evidence.supplied ||
        status !== "valid" ||
        !source ||
        source.sha256 !== resolution.sourceSha256 ||
        source.displayIdentity !== missing?.oracleSourceIdentity ||
        resolution.oracleSourceIdentity !== missing?.oracleSourceIdentity ||
        (missing?.oracleMemberName !== null &&
          !source.statementKinds.includes("package body")) ||
        !memberMatched ||
        typeof resolution.matchMethod !== "string" ||
        !resolution.matchMethod.trim()
      ) {
        errors.push(`${statePath}: converted missing object lacks a unique Oracle DDL source match`);
      }
    }
  }
  if (
    requiresCustomerValidation &&
    normalizeStatus(state.status) === "ready"
  ) {
    errors.push(
      `${statePath}: inferred or missing-body objects requiring customer validation cannot be READY`,
    );
  }

  for (const targetObject of expectedTargets.keys()) {
    if (!targets.has(targetObject)) {
      errors.push(
        `${statePath}: missing target object has no Oracle DDL resolution: ${targetObject}`,
      );
    }
  }
  for (const targetObject of targets) {
    if (!expectedTargets.has(targetObject)) {
      errors.push(
        `${statePath}: Oracle DDL resolution has no missing target object: ${targetObject}`,
      );
    }
  }

  if (report && evidence.missingObjectResolutions.length === 0) {
    requireMatch(report, /^\|\s*None\s*\|/m, reportPath);
  }
}

function validatePostgresProviderSelection(state, statePath) {
  const evidence = state?.postgresProvider;
  if (!evidence || typeof evidence !== "object") {
    errors.push(`${statePath}: postgresProvider evidence is required`);
    return;
  }
  if (
    !["vscode-copilot", "copilot-cli"].includes(evidence.harness) ||
    typeof evidence.available !== "boolean" ||
    evidence.fallbackAllowed !== false ||
    evidence.fallbackAttempted !== false ||
    evidence.mixedProviderOperationCount !== 0 ||
    !Array.isArray(evidence.prohibitedOperations) ||
    !evidence.prohibitedOperations.includes("postgres_mcp_db_context") ||
    !evidence.prohibitedOperations.includes("pgsql_db_context")
  ) {
    errors.push(`${statePath}: postgresProvider selection/fallback evidence is invalid`);
    return;
  }
  const discovery = evidence.discovery;
  if (
    discovery?.strategy !== "exact-operation-names" ||
    !Array.isArray(discovery.initialTools) ||
    !Array.isArray(discovery.searches)
  ) {
    errors.push(`${statePath}: postgresProvider exact-name discovery evidence is required`);
    return;
  }
  let plan;
  try {
    plan = planPostgresToolDiscovery({
      harness: evidence.harness,
      initialTools: discovery.initialTools,
      searches: discovery.searches,
    });
  } catch (error) {
    if (!(error instanceof TypeError || error instanceof RangeError)) throw error;
    errors.push(`${statePath}: invalid PostgreSQL discovery evidence: ${error.message}`);
    return;
  }
  if (plan.status === "search-required") {
    errors.push(`${statePath}: exact-name discovery is incomplete; required searches: ${plan.nextQueries.join("; ")}`);
    return;
  }
  if (
    discovery.status !== plan.status ||
    evidence.available !== plan.route.available
  ) {
    errors.push(`${statePath}: PostgreSQL availability does not match exact-name discovery`);
  }
  const route = plan.route;
  if (
    route.provider !== evidence.provider ||
    route.serverId !== evidence.serverId ||
    route.toolFamily !== evidence.toolFamily ||
    evidence.profileStore !== evidence.provider
  ) {
    errors.push(`${statePath}: postgresProvider does not match deterministic harness routing`);
  }
  if (!evidence.available) {
    if (
      Object.keys(evidence.selectedTools ?? {}).length !== 0 ||
      typeof evidence.reason !== "string" ||
      !evidence.reason.trim()
    ) {
      errors.push(`${statePath}: unavailable postgresProvider lacks exact evidence`);
    }
    return;
  }
  if (
    !route.available ||
    Object.keys(route.selectedTools).length !==
      Object.keys(evidence.selectedTools ?? {}).length ||
    Object.entries(route.selectedTools).some(
      ([operation, tool]) => evidence.selectedTools?.[operation] !== tool,
    ) ||
    evidence.reason !== null
  ) {
    errors.push(`${statePath}: postgresProvider does not match deterministic harness routing`);
  }
}

function validateDockerDesktopPolicy(state, report, statePath, reportPath) {
  const optionEnabled = state?.options?.["start-docker-desktop"];
  const evidence = state?.validation?.dockerEngineAvailability;
  const dockerStatus = normalizeStatus(
    state?.validation?.dockerPostgreSqlRuntime?.status,
  );
  if (typeof optionEnabled !== "boolean" || !evidence) {
    errors.push(`${statePath}: start-docker-desktop option/evidence is required`);
    return;
  }
  if (evidence.optionEnabled !== optionEnabled) {
    errors.push(`${statePath}: Docker Desktop option/evidence state is inconsistent`);
  }
  const status = normalizeStatus(evidence.status);
  if (evidence.engineRunningAtPreflight === true) {
    if (
      status !== "already running" ||
      evidence.startAttempted !== false ||
      evidence.desktopStateChanged !== false ||
      evidence.startupCommand !== null ||
      evidence.engineResponsive !== true
    ) {
      errors.push(`${statePath}: already-running Docker engine evidence is invalid`);
    }
  } else if (!optionEnabled) {
    if (
      status !== "not running option off" ||
      evidence.startAttempted !== false ||
      evidence.desktopStateChanged !== false ||
      evidence.startupCommand !== null ||
      evidence.engineResponsive !== false ||
      !evidence.reason
    ) {
      errors.push(`${statePath}: Docker Desktop changed state while its option was off`);
    }
    if (dockerStatus === "passed") {
      errors.push(`${statePath}: Docker runtime passed despite unavailable non-started engine`);
    }
  } else if (evidence.desktopDetected !== true) {
    if (
      status !== "desktop not available" ||
      evidence.startAttempted !== false ||
      evidence.desktopStateChanged !== false ||
      evidence.engineResponsive !== false ||
      !evidence.reason
    ) {
      errors.push(`${statePath}: missing Docker Desktop evidence is invalid`);
    }
  } else if (
    status === "started" &&
    evidence.startAttempted === true &&
    evidence.desktopStateChanged === true &&
    typeof evidence.startupCommand === "string" &&
    evidence.startupCommand.length > 0 &&
    evidence.engineResponsive === true
  ) {
    // Valid explicit startup.
  } else if (
    status === "start failed" &&
    evidence.startAttempted === true &&
    typeof evidence.startupCommand === "string" &&
    evidence.startupCommand.length > 0 &&
    evidence.engineResponsive === false &&
    evidence.reason
  ) {
    if (dockerStatus === "passed") {
      errors.push(`${statePath}: Docker runtime passed after Desktop start failure`);
    }
  } else {
    errors.push(`${statePath}: explicit Docker Desktop startup evidence is invalid`);
  }
  const expectedOption = optionEnabled ? "Yes" : "No";
  if (!new RegExp(`^\\|\\s*${expectedOption}\\s*\\|`, "m").test(report)) {
    errors.push(`${reportPath}: Docker Engine Availability option state is missing`);
  }
}

function normalizedScopeStatus(state) {
  return normalizeStatus(state?.scopeDecision?.status);
}

function isStaticScopeStop(state) {
  return ["no conversion required", "scope review required"].includes(
    normalizedScopeStatus(state),
  );
}

async function validateStaticScopeStop(
  state,
  report,
  statePath,
  reportPath,
  phase,
  assessmentState = null,
  expectedStatus = "no conversion required",
) {
  const scope = state.scopeDecision;
  const noConversion = expectedStatus === "no conversion required";
  const scopeReview = expectedStatus === "scope review required";
  if (
    scope?.schemaVersion !== 1 ||
    !Number.isInteger(scope?.scanLimits?.maxFiles) ||
    scope.scanLimits.maxFiles <= 0 ||
    !Number.isInteger(scope?.scanLimits?.maxTextBytes) ||
    scope.scanLimits.maxTextBytes <= 0 ||
    !Number.isInteger(scope?.scanLimits?.maxDurationMs) ||
    scope.scanLimits.maxDurationMs <= 0 ||
    !Number.isInteger(scope?.scanLimits?.candidateFiles) ||
    scope.scanLimits.candidateFiles < 0 ||
    !Number.isInteger(scope?.scanLimits?.candidateTextBytes) ||
    scope.scanLimits.candidateTextBytes < 0 ||
    !Number.isInteger(scope?.scannedFiles) ||
    scope.scannedFiles < 0 ||
    !Array.isArray(scope?.detectedTechnologies) ||
    (noConversion && scope.detectedTechnologies.length === 0) ||
    !Array.isArray(scope?.activeDatabaseFindings) ||
    scope.activeDatabaseFindings.length !== 0 ||
    !Array.isArray(scope?.activeOracleFindings) ||
    scope.activeOracleFindings.length !== 0 ||
    !Array.isArray(scope?.passiveArtifacts) ||
    !Array.isArray(scope?.skippedFiles) ||
    (noConversion && scope.skippedFiles.length !== 0) ||
    (scopeReview &&
      scope.skippedFiles.length === 0 &&
      scope.detectedTechnologies.length > 0 &&
      scope.scannedFiles > 0) ||
    !Array.isArray(scope?.scannedFileDigests) ||
    scope.scannedFileDigests.length === 0 ||
    !isIsoDate(scope?.scannedAt) ||
    scope?.gitBaselineAvailable !== true ||
    !Array.isArray(scope?.baselineGitStatus) ||
    !Array.isArray(scope?.executionArtifactBaseline) ||
    !scope?.inventoryDigest ||
    !scope?.reason
  ) {
    errors.push(`${statePath}: no-conversion scope decision evidence is incomplete`);
    return;
  }
  let fresh;
  try {
    fresh = await detectMigrationScope(root);
  } catch (error) {
    errors.push(`${statePath}: fresh static scope scan failed (${error.message})`);
  }
  if (
    !fresh ||
    normalizeStatus(fresh.status) !== expectedStatus ||
    fresh.inventoryDigest !== scope.inventoryDigest
  ) {
    errors.push(
      `${statePath}: persisted no-conversion decision does not match a fresh static scan`,
    );
  }
  const suppression = scope.executionSuppression;
  const suppressionFields = [
    "dependencyInstallationAttempted",
    "baselineBuildAttempted",
    "testsAttempted",
    "coverageAttempted",
    "testGenerationAttempted",
    "sourceChangesAttempted",
    "dockerAttempted",
    "targetDatabaseCallsAttempted",
    "ddlAttempted",
  ];
  if (
    !suppression ||
    suppressionFields.some((field) => suppression[field] !== false)
  ) {
    errors.push(
      `${statePath}: no-conversion run attempted dependency/build/test/source/database work`,
    );
  }
  for (const technology of scope.detectedTechnologies) {
    if (
      !technology?.name ||
      !Array.isArray(technology?.evidencePaths) ||
      technology.evidencePaths.length === 0 ||
      !report.includes(technology.name)
    ) {
      errors.push(`${reportPath}: detected application technology evidence is incomplete`);
    }
  }
  for (const skippedFile of scope.skippedFiles) {
    if (
      !skippedFile?.path ||
      !skippedFile?.reason ||
      !report.includes(skippedFile.path)
    ) {
      errors.push(`${reportPath}: scope-review file evidence is incomplete`);
    }
  }
  for (const artifact of scope.passiveArtifacts) {
    if (
      !artifact?.path ||
      !artifact?.kind ||
      normalizeStatus(artifact?.action) !== "retained" ||
      !Array.isArray(artifact?.evidence) ||
      artifact.evidence.length === 0 ||
      !report.includes(artifact.path)
    ) {
      errors.push(`${statePath}: passive artifact was not retained and reported`);
    }
  }
  if (
    !(noConversion
      ? report.includes("No application conversion required")
      : report.includes("Scope review required")) ||
    !report.includes("Not applicable") ||
    !report.includes("Not run by design")
  ) {
    errors.push(`${reportPath}: no-conversion outcome/limitations are missing`);
  }
  if (
    !state?.fileInventory?.noModifiedTrackedFilesReason ||
    !/(?:no.*conversion|scope.*review)/i.test(
      state.fileInventory.noModifiedTrackedFilesReason,
    )
  ) {
    errors.push(`${statePath}: no-conversion file inventory reason is required`);
  }
  const gitComparison = readNonMigrationArtifactChanges(
    scope.baselineGitStatus,
  );
  if (gitComparison.error) {
    errors.push(`${statePath}: ${gitComparison.error}`);
  } else if (gitComparison.changes.length > 0) {
    errors.push(
      `${statePath}: no-conversion run changed application files: ${gitComparison.changes.join(", ")}`,
    );
  }
  for (const key of [
    "assessmentBaseline",
    "conversionScope",
    "coverage",
    "fixtures",
    "options",
    "optionResults",
    "orchestration",
    "performanceExecution",
    "connectionContext",
    "target",
    "targetWrites",
  ]) {
    if (state[key] !== undefined) {
      errors.push(`${statePath}: no-conversion state must not contain ${key}`);
    }
  }
  const extraValidationKeys = Object.keys(state.validation ?? {}).filter(
    (key) => key !== "artifactValidation",
  );
  if (extraValidationKeys.length > 0) {
    errors.push(
      `${statePath}: no-conversion state contains executed validation gates: ${extraValidationKeys.join(", ")}`,
    );
  }
  for (const forbiddenPath of [
    ".github/migrate-oracle-app/assessment/test-discovery-baseline.json",
    ".github/migrate-oracle-app/assessment/coverage",
    ".github/migrate-oracle-app/conversions/file-diffs.md",
    ".github/migrate-oracle-app/conversions/coverage",
    ".github/migrate-oracle-app/conversions/database-ddl",
  ]) {
    if (await exists(path.join(root, forbiddenPath))) {
      errors.push(
        `${statePath}: no-conversion run created prohibited evidence ${forbiddenPath}`,
      );
    }
  }
  const currentExecutionArtifacts = await readExecutionArtifactBaseline(root);
  const currentExecutionByPath = new Map(
    currentExecutionArtifacts.map((entry) => [entry.path, entry]),
  );
  for (const baseline of scope.executionArtifactBaseline) {
    const current = currentExecutionByPath.get(baseline.path);
    if (
      !current ||
      current.exists !== baseline.exists ||
      current.treeDigest !== baseline.treeDigest
    ) {
      errors.push(
        `${statePath}: no-conversion run changed build/dependency output ${baseline.path}`,
      );
    }
  }
  if (
    phase === "conversion" &&
    assessmentState &&
    normalizedScopeStatus(assessmentState) !== expectedStatus
  ) {
    errors.push(
      `${statePath}: conversion no-conversion decision contradicts assessment scope`,
    );
  }
  if (normalizeStatus(state.status) !== expectedStatus) {
    errors.push(
      `${statePath}: status must be ${expectedStatus.toUpperCase().replaceAll(" ", "_")}`,
    );
  }
  if (phase === "assessment") {
    requireMatch(
      report,
      /^# Oracle to PostgreSQL Application Code Assessment$/m,
      reportPath,
    );
  } else {
    requireMatch(
      report,
      /^# Oracle to PostgreSQL Application Code Conversion$/m,
      reportPath,
    );
  }
  validateNavigation(report, reportPath);
  rejectPlaceholders(report, reportPath);
}

function readNonMigrationArtifactChanges(baselineEntries) {
  if (!Array.isArray(baselineEntries)) {
    return {
      changes: [],
      error: "no-conversion Git baseline status is missing",
    };
  }
  const result = spawnSync(
    "git",
    [
      "-C",
      root,
      "--no-pager",
      "status",
      "--porcelain=v1",
      "--untracked-files=all",
      "--ignored=matching",
    ],
    { encoding: "utf8", windowsHide: true },
  );
  if (result.status !== 0) {
    return {
      changes: [],
      error: "Git status failed during no-conversion validation",
    };
  }

  const baseline = new Set(
    baselineEntries.filter(
      (line) => !line.includes(".github/migrate-oracle-app/"),
    ),
  );
  const current = result.stdout
    .split(/\r?\n/)
    .filter(Boolean)
    .filter(
      (line) => !line.includes(".github/migrate-oracle-app/"),
    );
  return {
    changes: current.filter((line) => !baseline.has(line)),
    error: null,
  };
}

async function exists(target) {
  try {
    await stat(target);
    return true;
  } catch {
    return false;
  }
}

function validatePerformanceExecution(state, report, statePath, reportPath) {
  const performance = state?.performanceExecution;
  if (!performance?.buildSystem || !performance?.strategy) {
    errors.push(`${statePath}: performanceExecution evidence is required`);
    return;
  }
  if (performance.buildSystem !== "maven") {
    if (!performance.nativeStrategyEvidence) {
      errors.push(`${statePath}: non-Maven performance strategy lacks native evidence`);
    }
    return;
  }
  const expectedWaveCount =
    (state?.coverage?.unitWaves?.length ?? 0) +
    (state?.coverage?.integrationWaves?.length ?? 0);
  const warmup = performance.dependencyWarmup;
  const compile = performance.testCompile;
  const runner = performance.waveRunner;
  const fullSuite = performance.fullSuite;
  const coverage = performance.coverage;
  const packaging = performance.packaging;
  const agentScheduling = performance.agentScheduling;
  const targetMetadata = performance.targetMetadata;
  if (
    performance.strategy !== "precompiled-waves" ||
    !performance.runnerScript?.endsWith("scripts/run-maven-waves.mjs") ||
    warmup?.phaseCount !== 1 ||
    !Array.isArray(warmup?.commands) ||
    warmup.commands.length === 0 ||
    normalizeStatus(warmup?.status) !== "passed"
  ) {
    errors.push(`${statePath}: Maven dependency warmup/precompiled strategy evidence is invalid`);
  }
  if (
    !Number.isInteger(compile?.count) ||
    compile.count < 1 ||
    !compile?.compiledInputDigest ||
    !Array.isArray(compile?.invalidations) ||
    compile.count !== compile.invalidations.length + 1 ||
    compile.invalidations.some((invalidation) =>
      !invalidation?.reason || !invalidation.beforeDigest || !invalidation.afterDigest)
  ) {
    errors.push(`${statePath}: Maven waves require one test-compile per verified input/output generation`);
  }
  if (
    runner?.directPluginGoals !== true ||
    runner?.offline !== true ||
    runner?.lifecycleCompileInvocations !== 0 ||
    runner?.uniqueReportDirectories !== expectedWaveCount ||
    runner?.uniqueExecutionDataFiles !== expectedWaveCount
  ) {
    errors.push(`${statePath}: precompiled wave runner evidence does not reconcile`);
  }
  const docker = performance.dockerEnvironment;
  if (
    normalizeStatus(state?.validation?.dockerPostgreSqlRuntime?.status) === "passed" &&
    (docker?.scope !== "migration-run" ||
      !Number.isInteger(docker.containerStartCount) ||
      !Array.isArray(docker.invalidations) ||
      docker.containerStartCount !== docker.invalidations.length + 1 ||
      docker.invalidations.some((invalidation) =>
        !["image-change", "extensions-change", "bootstrap-ddl-change", "configuration-change", "contamination"].includes(invalidation?.kind) ||
        !invalidation.reason || !invalidation.evidence) ||
      docker.sharedAcrossTestProcesses !== true ||
      docker.ownershipVerified !== true ||
      docker.readinessVerified !== true ||
      docker.endpointBindingsVerified !== true ||
      docker.fixtureIsolationVerified !== true ||
      docker.cleanupVerified !== true ||
      !docker.containerIdDigest)
  ) {
    errors.push(`${statePath}: Docker tests require one verified run-scoped shared environment per configuration generation and cleanup proof`);
  }
  if (
    !Number.isInteger(fullSuite?.generationCount) ||
    fullSuite.generationCount < 1 ||
    fullSuite.generationCount > compile?.count ||
    !Number.isInteger(fullSuite?.runnerExecutions) ||
    fullSuite.runnerExecutions < 1 ||
    fullSuite.runnerExecutions > fullSuite.generationCount * 2 ||
    fullSuite?.directPrecompiledExecution !== true ||
    normalizeStatus(fullSuite?.status) !== "passed"
  ) {
    errors.push(
      `${statePath}: complete precompiled suite needs one or two direct runner executions per compiled generation`,
    );
  }
  if (
    coverage?.mergeTool !== "jacoco-cli" ||
    coverage?.execPerWave !== true ||
    coverage?.nativeTestXmlPerWave !== true ||
    !Number.isInteger(coverage?.htmlGenerationCount) ||
    coverage.htmlGenerationCount < 0 ||
    coverage.htmlGenerationCount > 1 ||
    !coverage?.combinedXml
  ) {
    errors.push(`${statePath}: JaCoCo CLI/XML-first coverage evidence is invalid`);
  }
  if (
    normalizeStatus(packaging?.prePackageCheck) !== "passed" ||
    packaging?.cleanPackageCount !== 1 ||
    packaging?.testsSkippedBecauseFullSuitePassed !== true ||
    !Number.isInteger(packaging?.archivesInspected) ||
    packaging.archivesInspected <= 0 ||
    normalizeStatus(packaging?.status) !== "passed"
  ) {
    errors.push(`${statePath}: final clean-package/archive evidence is invalid`);
  }
  if (
    agentScheduling?.backgroundNotifications !== true ||
    agentScheduling?.blockingWaitSecondsWhileIndependentWorkReady !== 0
  ) {
    errors.push(`${statePath}: blocking child-agent waits were not eliminated`);
  }
  if (
    targetMetadata?.batchedReadOnlyChecks !== true ||
    !Number.isInteger(targetMetadata?.parallelBatches) ||
    targetMetadata.parallelBatches < 1 ||
    !Array.isArray(targetMetadata?.checks) ||
    targetMetadata.checks.length < 2 ||
    targetMetadata?.databaseMutationsParallelized !== false
  ) {
    errors.push(`${statePath}: target metadata was not safely batched in parallel`);
  }
  for (const control of [
    "Dependency warmup",
    "Test compilation",
    "Precompiled waves",
    "Coverage generation",
    "Complete suite",
    "Pre-package checks",
    "Final clean package",
    "Archive inspection",
    "Agent waits",
    "Target metadata",
  ]) {
    if (!report.includes(`| ${control} |`)) {
      errors.push(`${reportPath}: performance control ${control} is missing`);
    }
  }
}

function validateOrchestration(state, report, statePath, reportPath) {
  const orchestration = state?.orchestration;
  if (!orchestration || typeof orchestration.agentFacilityAvailable !== "boolean") {
    errors.push(`${statePath}: orchestration capability evidence is required`);
    return;
  }
  const eligibleUnits = orchestration.eligibleIndependentUnits;
  const assignments = orchestration.assignments;
  const batches = orchestration.parallelBatches;
  const primaryOnlyPaths = orchestration.sharedPrimaryOnlyPaths;
  if (
    !Array.isArray(eligibleUnits) ||
    new Set(eligibleUnits).size !== eligibleUnits.length ||
    !Array.isArray(assignments) ||
    !Array.isArray(batches) ||
    !Array.isArray(primaryOnlyPaths) ||
    !primaryOnlyPaths.includes(".github/migrate-oracle-app/**")
  ) {
    errors.push(`${statePath}: orchestration unit/assignment/path evidence is invalid`);
    return;
  }
  const parallelExpected =
    orchestration.agentFacilityAvailable && eligibleUnits.length >= 2;
  if (orchestration.parallelRequired !== parallelExpected) {
    errors.push(`${statePath}: orchestration.parallelRequired is inconsistent`);
  }
  if (!orchestration.agentFacilityAvailable) {
    if (!orchestration.fallbackReason) {
      errors.push(`${statePath}: unavailable child-agent facility lacks an exact fallback reason`);
    }
    return;
  }
  if (eligibleUnits.length < 2 && !orchestration.fallbackReason) {
    errors.push(`${statePath}: serial agent plan lacks an exact no-parallel-work reason`);
  }

  const assignmentIds = new Set();
  const assignedUnits = new Map();
  const writeAssignments = [];
  for (const assignment of assignments) {
    if (
      !assignment?.id ||
      assignmentIds.has(assignment.id) ||
      !assignment?.type ||
      !assignment?.objective ||
      !["read-only", "write-enabled"].includes(assignment?.mode) ||
      !Array.isArray(assignment?.unitIds) ||
      assignment.unitIds.length === 0 ||
      !Array.isArray(assignment?.ownedPaths) ||
      !Array.isArray(assignment?.prohibitedPaths) ||
      !assignment.prohibitedPaths.includes(".github/migrate-oracle-app/**") ||
      assignment.databaseMutationAllowed !== false ||
      !isIsoDate(assignment.startedAt) ||
      !isIsoDate(assignment.completedAt) ||
      !["completed", "failed", "cancelled"].includes(normalizeStatus(assignment.status)) ||
      !assignment.resultSummary ||
      !Array.isArray(assignment.changedPaths) ||
      !Number.isInteger(assignment.commandsExecuted) ||
      !Number.isInteger(assignment.testRuns)
    ) {
      errors.push(`${statePath}: orchestration assignment is incomplete or unsafe`);
      continue;
    }
    assignmentIds.add(assignment.id);
    if (Date.parse(assignment.completedAt) < Date.parse(assignment.startedAt)) {
      errors.push(`${statePath}: orchestration assignment ${assignment.id} has invalid timestamps`);
    }
    for (const unitId of assignment.unitIds) {
      assignedUnits.set(unitId, (assignedUnits.get(unitId) ?? 0) + 1);
    }
    if (assignment.mode === "write-enabled") {
      if (
        assignment.ownedPaths.length === 0 ||
        assignment.changedPaths.some(
          (changedPath) =>
            !assignment.ownedPaths.some((ownedPath) =>
              pathWithinOwnership(changedPath, ownedPath),
            ),
        )
      ) {
        errors.push(`${statePath}: write-enabled assignment ${assignment.id} violated path ownership`);
      }
      writeAssignments.push(assignment);
    } else if (assignment.changedPaths.length > 0) {
      errors.push(`${statePath}: read-only assignment ${assignment.id} changed files`);
    }
    if (!report.includes(assignment.id)) {
      errors.push(`${reportPath}: orchestration row is missing for ${assignment.id}`);
    }
  }
  for (const unitId of eligibleUnits) {
    if (assignedUnits.get(unitId) !== 1) {
      errors.push(`${statePath}: eligible delegated unit ${unitId} is not assigned exactly once`);
    }
  }
  for (let left = 0; left < writeAssignments.length; left += 1) {
    for (let right = left + 1; right < writeAssignments.length; right += 1) {
      if (
        writeAssignments[left].ownedPaths.some((leftPath) =>
          writeAssignments[right].ownedPaths.some((rightPath) =>
            pathsOverlap(leftPath, rightPath),
          ),
        )
      ) {
        errors.push(
          `${statePath}: write-enabled assignments ${writeAssignments[left].id} and ${writeAssignments[right].id} overlap`,
        );
      }
    }
  }

  let overlappingBatchCount = 0;
  for (const batch of batches) {
    const members = (batch?.assignmentIds ?? [])
      .map((id) => assignments.find((assignment) => assignment.id === id))
      .filter(Boolean);
    if (
      !batch?.id ||
      members.length < 2 ||
      new Set(batch.assignmentIds).size !== batch.assignmentIds.length ||
      !isIsoDate(batch.startedAt) ||
      !isIsoDate(batch.completedAt)
    ) {
      errors.push(`${statePath}: orchestration parallel batch is invalid`);
      continue;
    }
    const latestStart = Math.max(
      ...members.map((assignment) => Date.parse(assignment.startedAt)),
    );
    const earliestEnd = Math.min(
      ...members.map((assignment) => Date.parse(assignment.completedAt)),
    );
    if (latestStart < earliestEnd) overlappingBatchCount += 1;
    else errors.push(`${statePath}: parallel batch ${batch.id} has no overlapping execution`);
  }
  if (parallelExpected && (assignments.length < 2 || overlappingBatchCount < 1)) {
    errors.push(`${statePath}: independent work existed but no child-agent parallel batch executed`);
  }
  const metrics = orchestration.metrics;
  if (
    !metrics ||
    metrics.delegatedTasks !== assignments.length ||
    metrics.parallelBatches !== batches.length ||
    !Number.isInteger(metrics.delegatedCommands) ||
    !Number.isInteger(metrics.delegatedTestRuns) ||
    metrics.ownershipViolations !== 0
  ) {
    errors.push(`${statePath}: orchestration metrics do not reconcile`);
  }
}

function normalizeOwnedPath(value) {
  return String(value)
    .replaceAll("\\", "/")
    .replace(/\/\*\*$/, "")
    .replace(/\/$/, "");
}

function pathWithinOwnership(changedPath, ownedPath) {
  const changed = normalizeOwnedPath(changedPath);
  const owned = normalizeOwnedPath(ownedPath);
  return changed === owned || changed.startsWith(`${owned}/`);
}

function pathsOverlap(leftPath, rightPath) {
  const left = normalizeOwnedPath(leftPath);
  const right = normalizeOwnedPath(rightPath);
  return (
    left === right ||
    left.startsWith(`${right}/`) ||
    right.startsWith(`${left}/`)
  );
}

function validateConversionScope(state, report, statePath, reportPath) {
  const scope = state?.conversionScope;
  if (
    !scope?.manifestDigest ||
    !Array.isArray(scope?.categories) ||
    scope.categories.length === 0 ||
    !Array.isArray(scope?.findings) ||
    scope.findings.length === 0
  ) {
    errors.push(`${statePath}: exhaustive conversionScope manifest is required`);
    return;
  }
  const seenFindingIds = new Set();
  for (const finding of scope.findings) {
    if (
      !finding?.id ||
      seenFindingIds.has(finding.id) ||
      !finding?.path ||
      !finding?.category ||
      !["converted", "retained", "deferred"].includes(
        normalizeStatus(finding.status),
      ) ||
      !finding?.reason
    ) {
      errors.push(`${statePath}: conversionScope contains an invalid or duplicate finding`);
      continue;
    }
    seenFindingIds.add(finding.id);
    if (
      normalizeStatus(finding.status) === "deferred" &&
      finding.safeToConvert === true
    ) {
      errors.push(
        `${statePath}: safely convertible finding was deferred: ${finding.id}`,
      );
    }
  }
  for (const category of scope.categories) {
    const fields = [
      "discovered",
      "converted",
      "retained",
      "deferred",
      "safeConvertibleRemaining",
    ];
    if (
      !category?.category ||
      fields.some(
        (field) =>
          !Number.isInteger(category[field]) || category[field] < 0,
      ) ||
      category.discovered !==
        category.converted + category.retained + category.deferred ||
      category.safeConvertibleRemaining !== 0 ||
      !category.evidence
    ) {
      errors.push(
        `${statePath}: conversionScope category ${category?.category ?? "<missing>"} does not reconcile`,
      );
      continue;
    }
    const findings = scope.findings.filter(
      (finding) => finding.category === category.category,
    );
    const counts = {
      converted: findings.filter(
        (finding) => normalizeStatus(finding.status) === "converted",
      ).length,
      retained: findings.filter(
        (finding) => normalizeStatus(finding.status) === "retained",
      ).length,
      deferred: findings.filter(
        (finding) => normalizeStatus(finding.status) === "deferred",
      ).length,
    };
    if (
      findings.length !== category.discovered ||
      counts.converted !== category.converted ||
      counts.retained !== category.retained ||
      counts.deferred !== category.deferred
    ) {
      errors.push(
        `${statePath}: conversionScope findings do not match category ${category.category}`,
      );
    }
    if (!report.includes(`| ${category.category} |`)) {
      errors.push(
        `${reportPath}: conversion scope category ${category.category} is missing`,
      );
    }
  }
}

function validatePersistentDdlOption(state, report, statePath, reportPath) {
  const requested = state?.options?.["create-persistent-ddl"];
  if (typeof requested !== "boolean") {
    errors.push(`${statePath}: options.create-persistent-ddl must be an explicit boolean`);
    return;
  }
  const outcome = state?.optionResults?.["create-persistent-ddl"];
  const missingObjects = state?.oracleDdlSource?.missingTargetObjects;
  const resolutions = state?.oracleDdlSource?.missingObjectResolutions;
  if (!outcome || !Array.isArray(missingObjects) || !Array.isArray(resolutions)) {
    errors.push(`${statePath}: persistent-DDL option result and missing-object evidence are required`);
    return;
  }
  if (
    outcome.requested !== requested ||
    outcome.applicableMissingObjects !== missingObjects.length
  ) {
    errors.push(`${statePath}: persistent-DDL requested state or missing-object count is inconsistent`);
  }

  const statuses = resolutions.map((resolution) =>
    validatePersistentCreateResolution(
      state,
      resolution,
      requested,
      report,
      statePath,
      reportPath,
    ),
  );
  const expectedStatus = !requested
    ? "not requested"
    : missingObjects.length === 0
      ? "not applicable"
      : statuses.length === missingObjects.length &&
          statuses.every((status) => ["created", "already present"].includes(status))
        ? "completed"
        : statuses.includes("failed")
          ? "failed"
          : "not executed";
  const satisfied = ["not requested", "not applicable", "completed"].includes(expectedStatus);
  if (
    normalizeStatus(outcome.status) !== expectedStatus ||
    outcome.optionSatisfied !== satisfied
  ) {
    errors.push(`${statePath}: persistent-DDL option result does not match verified target creation`);
  }
  if (expectedStatus === "not applicable" && !hasText(outcome.reason)) {
    errors.push(`${statePath}: zero-object persistent-DDL option requires a not-applicable reason`);
  }
  if (
    !satisfied &&
    (!hasText(outcome.reason) ||
      !hasText(outcome.nextAction) ||
      !(expectedStatus === "failed"
        ? ["partial", "blocked"].includes(normalizeStatus(state.status))
        : normalizeStatus(state.status) === "partial"))
  ) {
    errors.push(`${statePath}: requested but incomplete persistent DDL must record a reason, next action, and PARTIAL result`);
  }
}

function validatePersistentCreateResolution(
  state,
  resolution,
  requested,
  report,
  statePath,
  reportPath,
) {
  if (!resolution || typeof resolution !== "object" || !hasText(resolution.targetObject)) {
    errors.push(`${statePath}: persistent CREATE requires an exact missing-object identity`);
    return "";
  }
  const label = `${statePath}: ${resolution.targetObject} persistent CREATE`;
  const proof = resolution.persistentCreate;
  const status = normalizeStatus(proof?.status);
  const attempted = ["created", "failed"].includes(status);
  if (
    !proof ||
    !["not requested", "not executed", "created", "already present", "failed"].includes(status) ||
    resolution.persistentCreateRequested !== requested ||
    resolution.persistentCreateAttempted !== attempted ||
    resolution.persistentCreateVerified !== (status === "created") ||
    (normalizeStatus(resolution.resolutionStatus) !== "converted" &&
      !["not requested", "not executed"].includes(status))
  ) {
    errors.push(`${label}: requested/attempted/verified state is inconsistent`);
  }
  if (!requested) {
    if (
      status !== "not requested" ||
      resolution.persistentCreateAttempted !== false ||
      resolution.persistentCreateVerified !== false ||
      proof?.authorization !== null ||
      proof?.execution !== null ||
      proof?.verification !== null
    ) {
      errors.push(`${label}: disabled option must not perform a persistent write`);
    }
  } else if (status === "not requested") {
    errors.push(`${label}: enabled option cannot be recorded as not-requested`);
  }

  const authorization = proof?.authorization;
  const contextValid =
    authorization?.source === "current-request" &&
    /^[a-f0-9]{64}$/i.test(authorization.sourceDigest ?? "") &&
    /^[a-f0-9]{64}$/i.test(authorization.authorizedTargetDigest ?? "") &&
    authorization.authorizedTargetDigest === authorization.refreshedTargetDigest &&
    isIsoDate(authorization.contextRefreshedAt) &&
    authorization.savedProfile === true &&
    hasText(authorization.database) &&
    hasText(authorization.schema) &&
    state.postgresProvider?.available === true;
  const writeReady =
    contextValid &&
    ["rw", "write", "read write", "transaction read write"].includes(
      normalizeStatus(authorization.profileAccessMode),
    ) &&
    authorization.modifyToolAvailable === true &&
    authorization.defaultTransactionReadOnly === false &&
    authorization.serverInRecovery === false &&
    resolution.dockerValidated === true &&
    [
      "identity",
      "role",
      "privileges",
      "absence",
      "dependencies",
      "locks",
      "createOnly",
      "dockerValidation",
    ].every((check) => authorization.safetyChecks?.[check] === true);
  if (["created", "already present", "failed"].includes(status) && !contextValid) {
    errors.push(`${label}: current-request authorization and fresh exact target context are required`);
  }
  if (attempted) {
    const execution = proof?.execution;
    if (
      !writeReady ||
      !execution ||
      execution.tool !== state.postgresProvider?.selectedTools?.modify ||
      execution.targetDigest !== authorization?.authorizedTargetDigest ||
      execution.ddlSha256 !== resolution.generatedDeploySha256 ||
      !/^[a-f0-9]{64}$/i.test(execution.ddlSha256 ?? "") ||
      !isIsoDate(execution.executedAt) ||
      Date.parse(execution.executedAt) < Date.parse(authorization?.contextRefreshedAt) ||
      ![true, false, null].includes(execution.succeeded) ||
      (execution.succeeded === true
        ? execution.error !== null
        : !hasText(execution.error)) ||
      (status === "created" && execution.succeeded !== true)
    ) {
      errors.push(`${label}: execution lacks write safety, selected modify-tool evidence, or the exact deployed DDL hash`);
    }
  } else if (proof?.execution !== null) {
    errors.push(`${label}: unattempted creation must not contain execution evidence`);
  }
  if (
    status === "not executed" &&
    normalizeStatus(resolution.resolutionStatus) === "converted" &&
    writeReady
  ) {
    errors.push(`${label}: validated missing object must be created when create-persistent-ddl is enabled`);
  }
  if (["created", "already present"].includes(status)) {
    const verification = proof?.verification;
    const earliestVerification = attempted
      ? proof?.execution?.executedAt
      : authorization?.contextRefreshedAt;
    if (
      !verification ||
      verification.tool !== state.postgresProvider?.selectedTools?.query ||
      verification.targetDigest !== authorization?.authorizedTargetDigest ||
      verification.expectedDdlSha256 !== resolution.generatedDeploySha256 ||
      verification.objectIdentity !== resolution.targetObject ||
      verification.objectKind !== resolution.targetKind ||
      verification.contractMatches !== true ||
      !isIsoDate(verification.verifiedAt) ||
      !isIsoDate(earliestVerification) ||
      Date.parse(verification.verifiedAt) < Date.parse(earliestVerification)
    ) {
      errors.push(`${label}: immediate exact-object target verification is required`);
    }
  } else if (proof?.verification !== null) {
    errors.push(`${label}: unsuccessful creation must not claim successful verification`);
  }
  if (
    ["not executed", "failed"].includes(status) &&
    (!hasText(proof?.reason) || !hasText(proof?.nextAction))
  ) {
    errors.push(`${label}: unfulfilled creation requires an exact reason and next action`);
  }

  const reportRow = report
    .split(/\r?\n/)
    .find((line) => line.startsWith(`| ${resolution.targetObject} |`));
  const resultCell = reportRow?.split("|").at(-2) ?? "";
  const resultLabels = {
    "not requested": /option off|not requested/i,
    "not executed": /not executed/i,
    created: /created and verified/i,
    "already present": /already present and verified/i,
    failed: /failed/i,
  };
  if (
    !resultLabels[status]?.test(resultCell) ||
    (["not executed", "failed"].includes(status) &&
      (!plainMarkdown(resultCell).includes(proof?.reason) ||
        !plainMarkdown(resultCell).includes(proof?.nextAction)))
  ) {
    errors.push(`${reportPath}: ${resolution.targetObject} persistent target result does not match execution evidence`);
  }
  return status;
}

function hasText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function validateTargetWriteOption(state, report, statePath, reportPath) {
  const requested = state?.options?.["test-target-writes"];
  if (typeof requested !== "boolean") {
    errors.push(`${statePath}: options.test-target-writes must be an explicit boolean`);
    return;
  }
  const outcome = state?.optionResults?.["test-target-writes"];
  const evidence = state?.validation?.targetWriteTests;
  if (!outcome || !evidence) {
    errors.push(`${statePath}: target-write option result and validation evidence are required`);
    return;
  }
  if (outcome.requested !== requested || evidence.requested !== requested) {
    errors.push(`${statePath}: target-write requested state is inconsistent`);
  }

  const status = normalizeStatus(evidence.status);
  const outcomeStatus = normalizeStatus(outcome.status);
  const applicablePaths = evidence.applicableMutationScenarios;
  if (
    !Number.isInteger(applicablePaths) ||
    applicablePaths < 0 ||
    outcome.applicableMutationScenarios !== applicablePaths
  ) {
    errors.push(`${statePath}: target-write applicable mutation-scenario count is invalid`);
  }

  if (!requested) {
    if (
      status !== "not requested" ||
      outcomeStatus !== "not requested" ||
      evidence.writeAttempted !== false ||
      evidence.optionSatisfied !== true ||
      outcome.optionSatisfied !== true
    ) {
      errors.push(`${statePath}: disabled target-write option is not recorded as not-requested`);
    }
    requireMatch(report, /^\|\s*No\s*\|.*\|\s*➖ Not requested\s*\|$/m, reportPath);
    return;
  }

  if (applicablePaths === 0) {
    if (
      status !== "not applicable" ||
      outcomeStatus !== "not applicable" ||
      evidence.writeAttempted !== false ||
      evidence.optionSatisfied !== true ||
      outcome.optionSatisfied !== true ||
      !evidence.reason
    ) {
      errors.push(`${statePath}: zero-path target-write option lacks exact not-applicable evidence`);
    }
    requireMatch(report, /^\|\s*Yes\s*\|\s*0\s*\|.*\|\s*➖ Not applicable\s*\|$/m, reportPath);
    return;
  }

  if (status === "passed") {
    const safetyChecks = evidence.safetyChecks;
    const requiredSafetyChecks = [
      "identity",
      "role",
      "privileges",
      "contracts",
      "locks",
    ];
    const integerFields = [
      "mcpModificationScenarios",
      "markerRowsInserted",
      "markerRowsDeleted",
      "remainingMarkerRows",
      "customerRowsUpdated",
      "customerRowsDeleted",
      "mcpReads",
      "mcpWrites",
    ];
    if (
      outcomeStatus !== "completed" ||
      evidence.optionSatisfied !== true ||
      outcome.optionSatisfied !== true ||
      evidence.writeAttempted !== true ||
      evidence.savedProfile !== true ||
      !["rw", "write", "read write", "transaction read write"].includes(
        normalizeStatus(evidence.profileAccessMode),
      ) ||
      evidence.modifyToolAvailable !== true ||
      evidence.defaultTransactionReadOnly !== false ||
      evidence.inspectionTransactionReadOnly !== true ||
      evidence.serverInRecovery !== false ||
      !isIsoDate(evidence.contextRefreshedAt) ||
      evidence.dockerPrerequisitesPassed !== true ||
      evidence.mcpModificationScenarios !== applicablePaths ||
      !safetyChecks ||
      requiredSafetyChecks.some((check) => safetyChecks?.[check] !== true) ||
      integerFields.some((field) => !Number.isInteger(evidence[field])) ||
      evidence.mcpWrites <= 0 ||
      evidence.markerRowsInserted !== evidence.markerRowsDeleted ||
      evidence.remainingMarkerRows !== 0 ||
      evidence.customerRowsUpdated !== 0 ||
      evidence.customerRowsDeleted !== 0 ||
      !Array.isArray(evidence.sequenceValuesConsumed)
    ) {
      errors.push(`${statePath}: passed target-write option lacks complete write/cleanup evidence`);
    }
    requireMatch(report, /^\|\s*Yes\s*\|.*\|\s*✅ Passed\s*\|$/m, reportPath);
    return;
  }

  if (status === "not executed") {
    if (
      outcomeStatus !== "not executed" ||
      evidence.optionSatisfied !== false ||
      outcome.optionSatisfied !== false ||
      evidence.writeAttempted !== false ||
      !evidence.reason ||
      !evidence.nextAction ||
      normalizeStatus(state.status) !== "partial"
    ) {
      errors.push(`${statePath}: requested but unexecuted target writes must make conversion PARTIAL`);
    }
    requireMatch(report, /^\|\s*Yes\s*\|.*\|\s*⚠️ Not executed\s*\|$/m, reportPath);
    return;
  }

  if (status === "failed") {
    if (
      outcomeStatus !== "failed" ||
      evidence.optionSatisfied !== false ||
      outcome.optionSatisfied !== false ||
      evidence.writeAttempted !== true ||
      !Array.isArray(evidence.errors) ||
      evidence.errors.length === 0 ||
      !["partial", "blocked"].includes(normalizeStatus(state.status))
    ) {
      errors.push(`${statePath}: failed target-write evidence/readiness is incomplete`);
    }
    requireMatch(report, /^\|\s*Yes\s*\|.*\|\s*❌ Failed\s*\|$/m, reportPath);
    return;
  }

  errors.push(
    `${statePath}: requested target-write status must be passed, not-executed, failed, or not-applicable`,
  );
}

function validateDiscoveryBaseline(discovery, discoveryPath) {
  if (![1, 2].includes(discovery?.schemaVersion)) {
    errors.push(`${discoveryPath}: schemaVersion must be 1 or 2`);
  }
  if (!Array.isArray(discovery?.frameworks) || discovery.frameworks.length === 0) {
    errors.push(`${discoveryPath}: at least one discovered test framework is required`);
    return;
  }
  validateRunnerPolicy(discovery.runnerPolicy, discoveryPath);
  const reviewStatus = normalizeStatus(discovery?.review?.status);
  if (!["approved", "action needed"].includes(reviewStatus)) {
    errors.push(`${discoveryPath}: review.status must be approved or action needed`);
  }
  if (
    reviewStatus === "action needed" &&
    (!discovery?.review?.reason || !discovery?.review?.nextAction)
  ) {
    errors.push(`${discoveryPath}: action-needed review requires reason and nextAction`);
  }
  const adapterEvidence = discovery?.adapterEvidence ?? discovery?.dependencyEvidence;
  const effectiveEngines =
    adapterEvidence?.availableAdapters ?? adapterEvidence?.effectiveEngines;
  if (
    !(adapterEvidence?.command || Array.isArray(adapterEvidence?.commands)) ||
    !Array.isArray(effectiveEngines)
  ) {
    errors.push(`${discoveryPath}: effective runner/adapter evidence is required`);
  }
  if (!Array.isArray(discovery.intentionalExclusions)) {
    errors.push(`${discoveryPath}: intentionalExclusions must be an array`);
  } else {
    for (const exclusion of discovery.intentionalExclusions) {
      if (
        !(exclusion?.containerId ?? exclusion?.className) ||
        !exclusion?.reason ||
        !exclusion?.reviewedAt
      ) {
        errors.push(
          `${discoveryPath}: every intentional exclusion needs containerId, reason, and reviewedAt`,
        );
      }
    }
  }
  for (const framework of discovery.frameworks) {
    const label = framework?.id ?? "<missing-id>";
    const requiredAdapter = getRequiredAdapter(framework);
    const adapterAvailable = isAdapterAvailable(framework);
    if (!framework?.id || !framework?.sourceFrameworkId || !requiredAdapter) {
      errors.push(
        `${discoveryPath}: framework ${label} lacks id, sourceFrameworkId, or requiredAdapter`,
      );
    }
    if (
      adapterAvailable === true &&
      !effectiveEngines?.some((adapter) => adapter.includes(requiredAdapter))
    ) {
      errors.push(`${discoveryPath}: effective adapter evidence lacks ${requiredAdapter}`);
    }
    if (reviewStatus === "approved" && adapterAvailable !== true) {
      errors.push(`${discoveryPath}: approved baseline lacks adapter ${requiredAdapter}`);
    }
    const testCases = getTestCaseCount(framework);
    const eligibleContainers = getEligibleContainers(framework);
    const representativeContainers = getRepresentativeContainers(framework);
    const minimumContainers = getMinimumExecutedContainers(framework);
    if (!Number.isInteger(framework?.sourceFiles) || framework.sourceFiles <= 0) {
      errors.push(`${discoveryPath}: framework ${label} sourceFiles must be positive`);
    }
    if (!Number.isInteger(testCases) || testCases <= 0) {
      errors.push(`${discoveryPath}: framework ${label} testCases must be positive`);
    }
    if (eligibleContainers.length === 0) {
      errors.push(`${discoveryPath}: framework ${label} eligibleContainers is empty`);
    }
    if (representativeContainers.length === 0) {
      errors.push(`${discoveryPath}: framework ${label} representativeContainers is empty`);
    }
    const minimumCountsInvalid =
      !Number.isInteger(minimumContainers) ||
      !Number.isInteger(framework?.minimumExecutedTests) ||
      minimumContainers < 0 ||
      framework.minimumExecutedTests < 0 ||
      (reviewStatus === "approved" &&
        (minimumContainers === 0 || framework.minimumExecutedTests === 0));
    if (minimumCountsInvalid) {
      errors.push(`${discoveryPath}: framework ${label} minimum execution counts are invalid`);
    }
  }
}

async function validateSourceDiscoveryInventory(discovery, discoveryPath) {
  testSourceInventoryPromise ??= inventoryTestSources();
  const actualFrameworks = await testSourceInventoryPromise;
  if (actualFrameworks.size === 0) return;

  for (const [frameworkId, actual] of actualFrameworks) {
    const baseline = discovery.frameworks.find(
      (item) => (item.sourceFrameworkId ?? item.id) === frameworkId,
    );
    if (!baseline) {
      errors.push(
        `${discoveryPath}: source inventory contains ${frameworkId}, but the reviewed baseline omits it`,
      );
      continue;
    }
    const baselineContainers = new Set(getEligibleContainers(baseline));
    const missingFromBaseline = actual.containers.filter(
      (container) => !baselineContainers.has(container),
    );
    const absentFromSource = [...baselineContainers].filter(
      (container) => !actual.containers.includes(container),
    );
    if (
      baseline.sourceFiles !== actual.sourceFiles ||
      getTestCaseCount(baseline) !== actual.testCases ||
      missingFromBaseline.length > 0 ||
      absentFromSource.length > 0
    ) {
      errors.push(
        `${discoveryPath}: ${frameworkId} baseline does not match source inventory ` +
          `(files ${baseline.sourceFiles}/${actual.sourceFiles}, cases ${getTestCaseCount(baseline)}/${actual.testCases}, ` +
          `unrecorded containers ${missingFromBaseline.join(", ") || "none"}, absent containers ${absentFromSource.join(", ") || "none"})`,
      );
    }
  }
  for (const baseline of discovery.frameworks) {
    const sourceFrameworkId = baseline.sourceFrameworkId ?? baseline.id;
    if (!actualFrameworks.has(sourceFrameworkId)) {
      errors.push(`${discoveryPath}: baseline framework ${baseline.id} has no matching test sources`);
    }
  }
}

async function reportUnbaselinedSourceInventory(discoveryPath) {
  if (missingDiscoveryInventoryReported) return;
  missingDiscoveryInventoryReported = true;
  testSourceInventoryPromise ??= inventoryTestSources();
  const actualFrameworks = await testSourceInventoryPromise;
  for (const [frameworkId, actual] of actualFrameworks) {
    errors.push(
      `${discoveryPath}: unbaselined ${frameworkId} source inventory has ${actual.sourceFiles} files, ${actual.testCases} test cases, and ${actual.containers.length} eligible containers`,
    );
  }
}

async function inventoryTestSources() {
  const inventory = new Map();
  await walkTestSources(root, inventory);
  for (const value of inventory.values()) value.containers.sort();
  return inventory;
}

async function walkTestSources(directory, inventory) {
  let entries;
  try {
    entries = await readdir(directory, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (
      entry.isDirectory() &&
      [
        ".git",
        ".github",
        ".gradle",
        ".nox",
        ".tox",
        ".venv",
        ".vs",
        "__pycache__",
        "_deps",
        "bin",
        "build",
        "dist",
        "node_modules",
        "obj",
        "out",
        "packages",
        "target",
        "third_party",
        "vendor",
        "venv",
      ].includes(entry.name.toLowerCase())
    ) {
      continue;
    }
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      await walkTestSources(absolute, inventory);
      continue;
    }
    const normalized = absolute.replaceAll("\\", "/");
    if (!entry.isFile()) continue;
    const isJavaTest =
      entry.name.endsWith(".java") && normalized.includes("/src/test/");
    const isDotNet = entry.name.endsWith(".cs");
    const isPythonTest =
      entry.name.endsWith(".py") &&
      /(?:^|\/)(?:tests?|test_[^/]*|[^/]*_test)\b/i.test(normalized);
    const isCpp = /\.(?:c|cc|cpp|cxx)$/i.test(entry.name);
    if (!isJavaTest && !isDotNet && !isPythonTest && !isCpp) continue;
    const source = await readFile(absolute, "utf8");
    const inventorySource = isPythonTest ? source : stripCStyleComments(source);
    if (isJavaTest) {
      inventoryJavaSource(entry.name, inventorySource, inventory);
    } else if (isDotNet) {
      inventoryDotNetSource(entry.name, inventorySource, inventory);
    } else if (isPythonTest) {
      inventoryPythonSource(absolute, inventorySource, inventory);
    } else if (isCpp) {
      inventoryCppSource(inventorySource, inventory);
    }
  }
}

function stripCStyleComments(source) {
  let result = "";
  let state = "code";
  let quote = "";
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    const next = source[index + 1];
    if (state === "line-comment") {
      if (character === "\n") {
        result += "\n";
        state = "code";
      } else {
        result += " ";
      }
      continue;
    }
    if (state === "block-comment") {
      if (character === "*" && next === "/") {
        result += "  ";
        index += 1;
        state = "code";
      } else {
        result += character === "\n" ? "\n" : " ";
      }
      continue;
    }
    if (state === "string") {
      result += character;
      if (character === "\\" && index + 1 < source.length) {
        result += source[index + 1];
        index += 1;
      } else if (character === quote) {
        state = "code";
      }
      continue;
    }
    if (character === "/" && next === "/") {
      result += "  ";
      index += 1;
      state = "line-comment";
    } else if (character === "/" && next === "*") {
      result += "  ";
      index += 1;
      state = "block-comment";
    } else {
      result += character;
      if (character === '"' || character === "'") {
        state = "string";
        quote = character;
      }
    }
  }
  return result;
}

function inventoryJavaSource(fileName, source, inventory) {
  let frameworkId;
  if (
    /import\s+org\.junit\.jupiter\.(api\.(Test|RepeatedTest|TestFactory)|params\.ParameterizedTest)\s*;/.test(
      source,
    )
  ) {
    frameworkId = "junit5";
  } else if (
    /import\s+org\.junit\.(Test|runner\.RunWith)\s*;/.test(source) ||
    /extends\s+(?:junit\.framework\.)?TestCase\b/.test(source)
  ) {
    frameworkId = "junit4";
  }
  if (!frameworkId) return;
  const packageName = source.match(/^\s*package\s+([\w.]+)\s*;/m)?.[1];
  const className = fileName.slice(0, -".java".length);
  addSourceInventory(
    inventory,
    frameworkId,
    [
      ...source.matchAll(
        /^\s*@(Test|ParameterizedTest|RepeatedTest|TestFactory)\b/gm,
      ),
    ].length,
    [packageName ? `${packageName}.${className}` : className],
  );
}

function inventoryDotNetSource(fileName, source, inventory) {
  const candidates = [
    ["dotnet-xunit", /\[(?:Fact|Theory)\b[^\]]*\]/g],
    ["dotnet-nunit", /\[(?:Test|TestCase|TestCaseSource)\b[^\]]*\]/g],
    [
      "dotnet-mstest",
      /\[(?:TestMethod|DataTestMethod|DynamicData)\b[^\]]*\]/g,
    ],
  ];
  const namespaceName = source.match(/^\s*namespace\s+([\w.]+)\s*[;{]/m)?.[1];
  const className =
    source.match(/\bclass\s+([A-Za-z_]\w*)/)?.[1] ??
    fileName.slice(0, -".cs".length);
  const qualifiedName = namespaceName ? `${namespaceName}.${className}` : className;
  for (const [frameworkId, pattern] of candidates) {
    const testCases = [...source.matchAll(pattern)].length;
    if (testCases > 0) {
      addSourceInventory(inventory, frameworkId, testCases, [qualifiedName]);
    }
  }
}

function inventoryPythonSource(normalizedPath, source, inventory) {
  const testCases = [...source.matchAll(/^\s*(?:async\s+)?def\s+test_[A-Za-z0-9_]*\s*\(/gm)]
    .length;
  if (testCases === 0) return;
  const relativePath = path.relative(root, normalizedPath).replaceAll("\\", "/");
  const moduleName = relativePath
    .replace(/\.py$/, "")
    .split("/")
    .filter((part) => part !== "__init__")
    .join(".");
  const frameworkId =
    /(?:^|\n)\s*(?:from\s+unittest|import\s+unittest)\b/.test(source) &&
    /\b(?:unittest\.)?TestCase\b/.test(source)
      ? "python-unittest"
      : "python-pytest";
  addSourceInventory(inventory, frameworkId, testCases, [moduleName]);
}

function inventoryCppSource(source, inventory) {
  const gtestCases = [
    ...source.matchAll(
      /\b(?:TEST|TEST_F|TEST_P|TYPED_TEST|TYPED_TEST_P)\s*\(\s*([^,()]+)\s*,\s*([^,()]+)\s*\)/g,
    ),
  ].map((match) => `${match[1].trim()}.${match[2].trim()}`);
  if (gtestCases.length > 0) {
    addSourceInventory(inventory, "cpp-gtest", gtestCases.length, gtestCases);
  }
  const catchCases = [
    ...source.matchAll(
      /\b(?:TEST_CASE|SCENARIO|TEMPLATE_TEST_CASE)\s*\(\s*"([^"]+)"/g,
    ),
  ].map((match) => match[1]);
  if (catchCases.length > 0) {
    addSourceInventory(inventory, "cpp-catch2", catchCases.length, catchCases);
  }
  const boostCases = [
    ...source.matchAll(
      /\b(?:BOOST_AUTO_TEST_CASE|BOOST_DATA_TEST_CASE)\s*\(\s*([A-Za-z_]\w*)/g,
    ),
  ].map((match) => match[1]);
  if (boostCases.length > 0) {
    addSourceInventory(inventory, "cpp-boost-test", boostCases.length, boostCases);
  }
  const cmockaCases = [
    ...source.matchAll(
      /\bcmocka_unit_test(?:_setup_teardown)?\s*\(\s*([A-Za-z_]\w*)/g,
    ),
  ].map((match) => match[1]);
  if (cmockaCases.length > 0) {
    addSourceInventory(inventory, "c-cmocka", cmockaCases.length, cmockaCases);
  }
  const unityCases = [
    ...source.matchAll(/\bRUN_TEST\s*\(\s*([A-Za-z_]\w*)/g),
  ].map((match) => match[1]);
  if (unityCases.length > 0) {
    addSourceInventory(inventory, "c-unity", unityCases.length, unityCases);
  }
}

function addSourceInventory(inventory, frameworkId, testCases, containers) {
  const framework = inventory.get(frameworkId) ?? {
    sourceFiles: 0,
    testCases: 0,
    containers: [],
  };
  framework.sourceFiles += 1;
  framework.testCases += testCases;
  framework.containers.push(...containers);
  inventory.set(frameworkId, framework);
}

function getRequiredAdapter(framework) {
  return framework?.requiredAdapter ?? framework?.requiredEngine;
}

function isAdapterAvailable(framework) {
  return framework?.adapterAvailable ?? framework?.engineAvailable;
}

function getAdapterVersion(framework) {
  return framework?.adapterVersion ?? framework?.engineVersion;
}

function getTestCaseCount(framework) {
  return framework?.testCases ?? framework?.testMethods;
}

function getEligibleContainers(framework) {
  const value = framework?.eligibleContainers ?? framework?.eligibleClasses;
  return Array.isArray(value) ? value : [];
}

function getRepresentativeContainers(framework) {
  const value =
    framework?.representativeContainers ?? framework?.representativeClasses;
  return Array.isArray(value) ? value : [];
}

function getMinimumExecutedContainers(framework) {
  return (
    framework?.minimumExecutedContainers ?? framework?.minimumExecutedClasses
  );
}

function getEligibleContainerCount(framework) {
  const value = framework?.eligibleContainers ?? framework?.eligibleClasses;
  return Array.isArray(value) ? value.length : value;
}

function getExecutedContainerCount(framework) {
  return framework?.executedContainers ?? framework?.executedClasses;
}

function getMissingContainers(framework) {
  const value = framework?.missingContainers ?? framework?.missingClasses;
  return Array.isArray(value) ? value : null;
}

function getExecutedRepresentatives(framework) {
  const value =
    framework?.representativeContainersExecuted ??
    framework?.representativeClassesExecuted;
  return Array.isArray(value) ? value : null;
}

async function validateConversionTestDiscovery(
  state,
  discovery,
  report,
  statePath,
  reportPath,
) {
  const evidence = state?.validation?.testDiscovery;
  if (!evidence) {
    errors.push(`${statePath}: validation.testDiscovery is required`);
    return;
  }
  const discoveryPassed = normalizeStatus(evidence.status) === "passed";
  if (!discoveryPassed) {
    errors.push(`${statePath}: validation.testDiscovery must pass before conversion coverage`);
  }
  if (
    evidence.baselinePath !==
    ".github/migrate-oracle-app/assessment/test-discovery-baseline.json"
  ) {
    errors.push(`${statePath}: testDiscovery.baselinePath is missing or non-canonical`);
  }
  if (evidence.baselineSha256 !== sha256(JSON.stringify(discovery))) {
    errors.push(`${statePath}: reviewed test-discovery baseline digest does not match`);
  }
  validateRunnerPolicy(evidence.runnerPolicy, statePath);

  const fullSuite = evidence.fullSuite;
  if (
    !fullSuite?.command ||
    !isIsoDate(fullSuite.startedAt) ||
    !isIsoDate(fullSuite.completedAt) ||
    !Number.isInteger(fullSuite.tests) ||
    fullSuite.tests <= 0 ||
    fullSuite.failures !== 0 ||
    fullSuite.errors !== 0
  ) {
    errors.push(`${statePath}: complete-suite command, timestamps, totals, and zero failures/errors are required`);
  }
  const repair = evidence.failureRepair;
  const establishBaseline = state?.options?.["establish-baseline"];
  const fixBrokenTests = state?.options?.["fix-broken-tests"];
  if (typeof establishBaseline !== "boolean" || typeof fixBrokenTests !== "boolean") {
    errors.push(`${statePath}: establish-baseline and fix-broken-tests must be explicit booleans`);
  }
  if (
    repair?.fixBrokenTestsEnabled !== fixBrokenTests ||
    !Array.isArray(repair?.repairedTests)
  ) {
    errors.push(`${statePath}: broken-test repair gate is incomplete`);
  }
  validateFailureRepairEvidence(
    repair,
    fixBrokenTests,
    discoveryPassed,
    report,
    statePath,
    reportPath,
  );
  if (
    fixBrokenTests === true &&
    Number(repair?.exposedFailures) > 0 &&
    repair.repairedTests.length === 0
  ) {
    errors.push(`${statePath}: exposed failures were not reconciled to recorded repairs`);
  }
  const coverageGate = evidence.coverageEligibility;
  if (
    coverageGate?.discoveryReconciled !== true ||
    !isIsoDate(coverageGate?.reconciledAt) ||
    coverageGate?.coverageGeneratedAfterReconciliation !== true ||
    (coverageGate?.coverageArtifactsFresh ?? coverageGate?.jacocoFresh) !== true
  ) {
    errors.push(`${statePath}: coverage was not proven fresh and generated after discovery reconciliation`);
  }

  const reportFiles = fullSuite?.reportFiles;
  if (!Array.isArray(reportFiles) || reportFiles.length === 0) {
    errors.push(`${statePath}: fullSuite.reportFiles must list fresh native test reports`);
    return;
  }
  const nativeEvidence = await readNativeTestReports(
    reportFiles,
    fullSuite?.startedAt,
    statePath,
  );
  if (Number.isInteger(fullSuite?.tests) && nativeEvidence.tests !== fullSuite.tests) {
    errors.push(
      `${statePath}: native report test total ${nativeEvidence.tests} does not match full-suite total ${fullSuite.tests}`,
    );
  }

  const exclusions = new Set(
    (discovery.intentionalExclusions ?? []).map(
      (item) => item.containerId ?? item.className,
    ),
  );
  for (const baselineFramework of discovery.frameworks) {
    const current = (evidence.frameworks ?? []).find(
      (item) => item.id === baselineFramework.id,
    );
    const requiredAdapter = getRequiredAdapter(baselineFramework);
    if (!isAdapterAvailable(baselineFramework)) {
      errors.push(
        `${statePath}: required adapter ${requiredAdapter} was unavailable in the reviewed baseline`,
      );
    }
    if (!current) {
      errors.push(`${statePath}: adapter evidence is missing for ${baselineFramework.id}`);
      continue;
    }
    if (!isAdapterAvailable(current) || !getAdapterVersion(current)) {
      errors.push(`${statePath}: adapter evidence is missing for ${baselineFramework.id}`);
    }
    const expectedContainers = getEligibleContainers(baselineFramework).filter(
      (container) => !exclusions.has(container),
    );
    const missingFromReports = expectedContainers.filter(
      (container) => !hasReportedContainer(nativeEvidence.containers, container),
    );
    const missingRepresentatives = getRepresentativeContainers(
      baselineFramework,
    ).filter(
      (container) => !hasReportedContainer(nativeEvidence.containers, container),
    );
    if (
      current.sourceFiles !== baselineFramework.sourceFiles ||
      getTestCaseCount(current) !== getTestCaseCount(baselineFramework) ||
      getEligibleContainerCount(current) !== expectedContainers.length
    ) {
      errors.push(`${statePath}: ${baselineFramework.id} source inventory differs from the reviewed baseline`);
    }
    if (
      getExecutedContainerCount(current) <
        getMinimumExecutedContainers(baselineFramework) ||
      current.executedTests < baselineFramework.minimumExecutedTests
    ) {
      errors.push(`${statePath}: ${baselineFramework.id} execution count decreased below the reviewed minimum`);
    }
    const currentMissing = getMissingContainers(current);
    if (
      currentMissing === null ||
      currentMissing.length > 0 ||
      missingFromReports.length > 0
    ) {
      errors.push(
        `${statePath}: ${baselineFramework.id} has eligible containers without fresh execution reports: ${[
          ...(currentMissing ?? []),
          ...missingFromReports,
        ].join(", ")}`,
      );
    }
    if (
      !Array.isArray(getExecutedRepresentatives(current)) ||
      missingRepresentatives.length > 0
    ) {
      errors.push(`${statePath}: ${baselineFramework.id} representative-test execution is incomplete`);
    }
  }
  if ((evidence.frameworks ?? []).length !== discovery.frameworks.length) {
    errors.push(`${statePath}: current framework count differs from the reviewed discovery baseline`);
  }

  await validateCoverageLaneFreshness(state, discovery, evidence, statePath);
  for (const framework of discovery.frameworks) {
    if (!report.includes(framework.id) || !report.includes(String(framework.sourceFiles))) {
      errors.push(`${reportPath}: discovery evidence for ${framework.id} is missing`);
    }
  }
}

function validateFailureRepairEvidence(
  repair,
  fixBrokenTests,
  discoveryPassed,
  report,
  statePath,
  reportPath,
) {
  if (!repair) return;
  const exposedClasses = repair.exposedFailureClasses;
  const attempts = repair.repairAttempts;
  const unresolved = repair.unresolvedFailureClasses;
  if (
    !Number.isInteger(repair.exposedFailures) ||
    repair.exposedFailures < 0 ||
    !Number.isInteger(repair.exposedErrors) ||
    repair.exposedErrors < 0 ||
    !Array.isArray(exposedClasses) ||
    !Array.isArray(attempts) ||
    !Array.isArray(unresolved)
  ) {
    errors.push(`${statePath}: failure-repair inventory/attempt evidence is incomplete`);
    return;
  }
  if (fixBrokenTests && exposedClasses.length > 0) {
    const attemptedClasses = new Set(
      attempts.map((attempt) => attempt.failureClass),
    );
    const unattempted = exposedClasses.filter(
      (failureClass) => !attemptedClasses.has(failureClass),
    );
    if (unattempted.length > 0) {
      errors.push(
        `${statePath}: fix-broken-tests left failure classes without repair attempts: ${unattempted.join(", ")}`,
      );
    }
    for (const failureClass of exposedClasses) {
      if (!report.includes(failureClass)) {
        errors.push(
          `${reportPath}: broken-test repair row is missing for ${failureClass}`,
        );
      }
    }
    for (const failureClass of unresolved) {
      const classAttempts = attempts.filter(
        (attempt) => attempt.failureClass === failureClass,
      );
      const finalAttempt = classAttempts.at(-1);
      if (
        classAttempts.length < 3 &&
        !(
          finalAttempt?.unsafeToRepair === true &&
          finalAttempt?.classification &&
          finalAttempt?.reason &&
          finalAttempt?.nextAction
        )
      ) {
        errors.push(
          `${statePath}: unresolved failure class ${failureClass} lacks three repair attempts or an exact unsafe-to-repair record`,
        );
      }
    }
  }
  if (
    discoveryPassed &&
    (normalizeStatus(repair.status) !== "passed" ||
      repair.remainingFailures !== 0 ||
      repair.remainingErrors !== 0 ||
      unresolved.length > 0)
  ) {
    errors.push(`${statePath}: passing discovery retains unresolved test failures/errors`);
  }
}

function validateRunnerPolicy(policy, evidencePath) {
  if (
    policy?.failIfNoTests !== true ||
    policy?.failIfNoSpecifiedTests !== true ||
    policy?.skipTests !== false ||
    policy?.testFailureIgnore !== false
  ) {
    errors.push(`${evidencePath}: test runner fail-closed settings are missing or unsafe`);
  }
  if (!Array.isArray(policy?.newExclusions) || policy.newExclusions.length > 0) {
    errors.push(`${evidencePath}: new test exclusions are prohibited`);
  }
  if (
    !Array.isArray(policy?.unexplainedDisabledTests) ||
    policy.unexplainedDisabledTests.length > 0
  ) {
    errors.push(`${evidencePath}: unexplained disabled tests are prohibited`);
  }
}

async function readNativeTestReports(reportFiles, startedAt, statePath) {
  const containers = new Set();
  let tests = 0;
  const started = Date.parse(startedAt);
  for (const reportEntry of reportFiles) {
    const reportFile =
      typeof reportEntry === "string" ? reportEntry : reportEntry?.path;
    const format =
      (typeof reportEntry === "object" && reportEntry?.format) ||
      (String(reportFile).toLowerCase().endsWith(".trx") ? "trx" : "junit-xml");
    const absolute = safeArtifactPath(reportFile, statePath);
    if (!absolute) continue;
    try {
      const [xml, metadata] = await Promise.all([
        readFile(absolute, "utf8"),
        stat(absolute),
      ]);
      if (Number.isFinite(started) && metadata.mtimeMs < started) {
        errors.push(`${statePath}: native test report is stale: ${reportFile}`);
      }
      if (format === "trx") {
        for (const match of xml.matchAll(/<UnitTestResult\b[^>]*>/g)) {
          const testName = xmlAttribute(match[0], "testName");
          if (testName) containers.add(testName);
          tests += 1;
        }
        for (const match of xml.matchAll(/<TestMethod\b[^>]*>/g)) {
          const className = xmlAttribute(match[0], "className");
          const methodName = xmlAttribute(match[0], "name");
          if (className) containers.add(className);
          if (className && methodName) containers.add(`${className}.${methodName}`);
          if (methodName) containers.add(methodName);
        }
      } else {
        for (const match of xml.matchAll(/<testcase\b[^>]*>/g)) {
          const className = xmlAttribute(match[0], "classname");
          const testName = xmlAttribute(match[0], "name");
          if (className) containers.add(className);
          if (className && testName) containers.add(`${className}.${testName}`);
          if (testName) containers.add(testName);
          tests += 1;
        }
      }
    } catch (error) {
      errors.push(`${statePath}: native test report is missing or unreadable: ${reportFile} (${error.message})`);
    }
  }
  return { containers, tests };
}

async function validateCoverageLaneFreshness(state, discovery, evidence, statePath) {
  const lanes = state?.coverage?.lanes;
  if (!Array.isArray(lanes)) {
    errors.push(`${statePath}: separate engine/integration/combined coverage lanes are required`);
    return;
  }
  const requiredLaneIds = new Set([
    ...discovery.frameworks.map(
      (framework) => framework.coverageLaneId ?? framework.id,
    ),
    "integration",
    "combined",
  ]);
  const startedAt = Date.parse(
    state.performanceExecution?.waveRunner?.measurementStartedAt ??
      evidence.fullSuite.startedAt,
  );
  const reconciledAt = Date.parse(evidence.coverageEligibility.reconciledAt);
  for (const laneId of requiredLaneIds) {
    const lane = lanes.find((item) => item.id === laneId);
    if (!lane) {
      errors.push(`${statePath}: coverage lane ${laneId} is missing`);
      continue;
    }
    const laneStatus = normalizeStatus(lane.status);
    if (laneStatus === "unavailable") {
      if (!lane.unavailableReason) {
        errors.push(`${statePath}: unavailable coverage lane ${laneId} lacks an exact reason`);
      }
      continue;
    }
    if (laneStatus !== "passed" || !lane.executionDataPath || !lane.reportPath) {
      errors.push(`${statePath}: coverage lane ${laneId} lacks passed execution/report evidence`);
      continue;
    }
    for (const [artifactPath, minimumTime, label] of [
      [lane.executionDataPath, startedAt, "execution data"],
      [lane.reportPath, reconciledAt, "coverage report"],
    ]) {
      const absolute = safeArtifactPath(artifactPath, statePath);
      if (!absolute) continue;
      try {
        const metadata = await stat(absolute);
        if (Number.isFinite(minimumTime) && metadata.mtimeMs < minimumTime) {
          errors.push(`${statePath}: ${laneId} ${label} is stale: ${artifactPath}`);
        }
      } catch (error) {
        errors.push(`${statePath}: ${laneId} ${label} is missing: ${artifactPath} (${error.message})`);
      }
    }
  }
}

function safeArtifactPath(relativePath, evidencePath) {
  if (typeof relativePath !== "string" || path.isAbsolute(relativePath)) {
    errors.push(`${evidencePath}: artifact path must be codebase-relative: ${relativePath}`);
    return null;
  }
  const absolute = path.resolve(root, relativePath);
  const relative = path.relative(root, absolute);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    errors.push(`${evidencePath}: artifact path escapes the codebase: ${relativePath}`);
    return null;
  }
  return absolute;
}

function hasReportedContainer(containers, expected) {
  for (const actual of containers) {
    if (
      actual === expected ||
      actual.startsWith(`${expected}$`) ||
      actual.startsWith(`${expected}.`) ||
      actual.endsWith(`.${expected}`) ||
      actual.includes(expected)
    ) {
      return true;
    }
  }
  return false;
}

function xmlAttribute(tag, name) {
  const value = tag.match(new RegExp(`\\b${name}="([^"]*)"`))?.[1];
  return value ? decodeXml(value) : null;
}

function decodeXml(value) {
  return value
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&amp;", "&");
}

function isIsoDate(value) {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function validateWaves(state, statePath) {
  const unitWaves = state?.coverage?.unitWaves ?? [];
  const integrationWaves = state?.coverage?.integrationWaves ?? [];
  const createUnitTests = state?.options?.["create-unit-tests"];
  const createIntegrationTests = state?.options?.["create-integration-tests"];
  if (
    typeof createUnitTests !== "boolean" ||
    typeof createIntegrationTests !== "boolean"
  ) {
    errors.push(
      `${statePath}: create-unit-tests and create-integration-tests must be explicit booleans`,
    );
  }
  const policy = state?.coverage?.wavePolicy;
  if (createUnitTests || createIntegrationTests) {
    if (
      policy?.unitMinimum !== MINIMUM_TEST_WAVES ||
      policy?.integrationMinimum !== MINIMUM_TEST_WAVES ||
      policy?.recommended !== MINIMUM_TEST_WAVES ||
      policy?.maximum !== MAXIMUM_TEST_WAVES ||
      policy?.aspirationalCombinedLinePercentage !== 80 ||
      policy?.readinessGate !== false
    ) {
      errors.push(
        `${statePath}: exact two-unit/two-integration/ten-maximum wave policy and non-gating 80% aspiration are missing`,
      );
    }
  }
  if (createUnitTests && unitWaves.length < MINIMUM_TEST_WAVES) {
    errors.push(
      `${statePath}: unit-test generation is enabled but fewer than two unit waves were recorded`,
    );
  }
  if (createIntegrationTests && integrationWaves.length < MINIMUM_TEST_WAVES) {
    errors.push(
      `${statePath}: integration-test generation is enabled but fewer than two integration waves were recorded`,
    );
  }
  if (unitWaves.length > MAXIMUM_TEST_WAVES || integrationWaves.length > MAXIMUM_TEST_WAVES) {
    errors.push(`${statePath}: more than ten waves were recorded in a test lane`);
  }
  validateWaveLane(unitWaves, "unit", statePath);
  validateWaveLane(integrationWaves, "integration", statePath);
  if (
    (unitWaves.length > 0 || integrationWaves.length > 0) &&
    !state?.coverage?.stopReason &&
    !state?.stopReason
  ) {
    errors.push(`${statePath}: coverage waves lack an exact stop reason`);
  }
  const finalLine = firstNumber(
    percentage(state?.coverage?.final?.combined?.line),
  );
  if (
    (createUnitTests || createIntegrationTests) &&
    Number.isFinite(finalLine) &&
    finalLine < 80 &&
    (!createUnitTests || unitWaves.length >= MINIMUM_TEST_WAVES) &&
    (!createIntegrationTests || integrationWaves.length >= MINIMUM_TEST_WAVES)
  ) {
    const finalBacklog =
      state?.coverage?.rankedBacklogFinal ??
      [];
    if (
      !Array.isArray(finalBacklog) ||
      finalBacklog.length === 0 ||
      finalBacklog.some(
        (item) =>
          !item?.classification ||
          (item?.safelyTestable !== false &&
            !isDocumentedLowYieldGap(item, state.coverage.stopDecision)) ||
          !item?.nextAction,
      )
    ) {
      errors.push(
        `${statePath}: below-80% stop requires classified unsafe/external or documented low-yield gaps and exact next actions`,
      );
    }
  }
}

function isDocumentedLowYieldGap(item, decision) {
  return decision?.kind === "low-marginal-value" &&
    typeof decision.reason === "string" &&
    decision.reason.trim().length > 0 &&
    item.classification === "deferred-low-yield" &&
    item.safelyTestable === true &&
    item.priority === "low" &&
    item.highRisk !== true &&
    typeof item.evidence === "string" &&
    item.evidence.trim().length > 0 &&
    Number.isInteger(item.expectedNewCoveredLines) &&
    item.expectedNewCoveredLines >= 0 &&
    Number.isInteger(item.expectedNewCoveredBranches) &&
    item.expectedNewCoveredBranches >= 0 &&
    Number.isSafeInteger(item.estimatedAdditionalRuntimeMs) &&
    item.estimatedAdditionalRuntimeMs > 0;
}

function validateWaveLane(waves, lane, statePath) {
  const numbers = new Set();
  for (const [index, wave] of waves.entries()) {
    const validationWave = wave?.kind === "validation";
    if (
      !Number.isInteger(wave?.wave) ||
      wave.wave !== index + 1 ||
      !["generation", "validation"].includes(wave.kind ?? "generation") ||
      numbers.has(wave.wave) ||
      !Array.isArray(wave.plannedBehaviors) ||
      wave.plannedBehaviors.length === 0 ||
      wave.plannedBehaviors.length > 5 ||
      !Array.isArray(wave.testsAddedOrRepaired) ||
      (!validationWave && wave.testsAddedOrRepaired.length === 0) ||
      (validationWave && (!Array.isArray(wave.validatedTests) || wave.validatedTests.length === 0)) ||
      normalizeStatus(wave.result) !== "passed"
    ) {
      errors.push(`${statePath}: ${lane} wave evidence is incomplete or invalid`);
      continue;
    }
    if (wave.wave > MINIMUM_TEST_WAVES) {
      try {
        validateOptionalWaveValue(wave.continuationEvidence);
      } catch (error) {
        if (!(error instanceof TypeError)) throw error;
        errors.push(`${statePath}: ${lane} wave ${wave.wave}: ${error.message}`);
      }
    }
    numbers.add(wave.wave);
  }
}

function validateFixturesAndObjects(state, report, statePath, reportPath) {
  const fixtureRoot = state?.fixtures;
  const fixtureRows = fixtureRoot?.ledger ?? [];
  const objectMatrix = fixtureRoot?.databaseObjectTestMatrix ?? [];
  const dockerStatus = normalizeStatus(state?.validation?.dockerPostgreSqlRuntime?.status);
  if (!["passed", "failed", "skipped", "not available"].includes(dockerStatus)) {
    errors.push(`${statePath}: validation.dockerPostgreSqlRuntime.status is missing or invalid`);
  }
  const dockerPassed = dockerStatus === "passed";

  if (dockerPassed && fixtureRows.length === 0) {
    const noRowsReason = fixtureRoot?.noRowsReason;
    if (!noRowsReason) {
      errors.push(`${statePath}: Docker runtime passed but no synthetic fixture ledger or exact no-row reason was recorded`);
    }
  }
  if (objectMatrix.length === 0) {
    errors.push(`${statePath}: database-object test matrix is missing`);
  } else {
    for (const kind of [
      "function",
      "procedure",
      "sequence",
      "table",
      "view",
      "trigger",
      "native-query-path",
    ]) {
      if (!objectMatrix.some((item) => String(item.kind).toLowerCase() === kind)) {
        errors.push(`${statePath}: database-object matrix lacks explicit ${kind} count/evidence`);
      }
    }
  }
  if (fixtureRows.length > 0) {
    const badCleanup = fixtureRows.filter(
      (row) => Number(row.remaining ?? row.rowsRemaining) !== 0,
    );
    if (badCleanup.length > 0) {
      errors.push(`${statePath}: ${badCleanup.length} fixture rows do not prove zero remaining`);
    }
  }
  if (!report.includes("### Database Objects Exercised with Synthetic Data")) {
    errors.push(`${reportPath}: database-object exercise section is missing`);
  }
}

function validateCoverage(state, report, statePath) {
  const baselineLine = percentage(state?.coverage?.baseline?.combined?.line);
  const finalLine = percentage(state?.coverage?.final?.combined?.line);
  if (!Number.isFinite(baselineLine) || !Number.isFinite(finalLine)) {
    if (!state?.coverage?.unavailableReason) {
      errors.push(`${statePath}: baseline/final combined line coverage or unavailableReason is required`);
    }
  } else if (!report.includes(`${finalLine.toFixed(2)}%`)) {
    errors.push(`${statePath}: final combined line coverage ${finalLine.toFixed(2)}% is not present in the report`);
  }
}

function validateExecutiveConversionSummary(state, report, statePath, reportPath) {
  const expectedAreas = [
    "Application conversion",
    "Build & package",
    "Unit tests",
    "PostgreSQL integration tests",
    "Target validation",
    "Production readiness",
  ];
  const section = sectionBetween(
    report,
    "## Executive Conversion Summary",
    "## Conversion Transformation",
  );
  if (!section) {
    errors.push(`${reportPath}: executive conversion summary section is missing`);
    return;
  }

  const scorecardHeading = "### Conversion Scorecard";
  const nextStepsHeading = "### Next Steps / Actions Needed";
  const scorecardIndex = section.indexOf(scorecardHeading);
  const nextStepsIndex = section.indexOf(nextStepsHeading);
  if (scorecardIndex < 0 || nextStepsIndex <= scorecardIndex) {
    errors.push(
      `${reportPath}: Conversion Scorecard must be followed immediately by Next Steps / Actions Needed`,
    );
    return;
  }
  if (/^### /m.test(section.slice(scorecardIndex + scorecardHeading.length, nextStepsIndex))) {
    errors.push(
      `${reportPath}: no subsection may appear between Conversion Scorecard and Next Steps / Actions Needed`,
    );
  }
  const scorecardBlock = section.slice(
    scorecardIndex + scorecardHeading.length,
    nextStepsIndex,
  );
  if (
    scorecardBlock
      .split(/\r?\n/)
      .some((line) => line.trim() && !line.trim().startsWith("|"))
  ) {
    errors.push(
      `${reportPath}: Next Steps / Actions Needed must immediately follow the scorecard table`,
    );
  }

  const openingParagraphs = section
    .slice(0, scorecardIndex)
    .trim()
    .split(/\r?\n\s*\r?\n/)
    .map((paragraph) => paragraph.replace(/\r?\n/g, " ").trim())
    .filter(Boolean);
  if (openingParagraphs.length !== 2) {
    errors.push(
      `${reportPath}: Executive Conversion Summary must begin with exactly two concise paragraphs`,
    );
  }

  const summary = state?.executiveSummary;
  if (!summary || typeof summary !== "object") {
    errors.push(`${statePath}: executiveSummary evidence is required`);
    return;
  }
  for (const [field, paragraphIndex] of [
    ["conversionResult", 0],
    ["customerActionSummary", 1],
  ]) {
    const value = summary[field];
    if (typeof value !== "string" || !value.trim()) {
      errors.push(`${statePath}: executiveSummary.${field} is required`);
    } else if (
      openingParagraphs[paragraphIndex] &&
      plainMarkdown(openingParagraphs[paragraphIndex]) !== plainMarkdown(value)
    ) {
      errors.push(
        `${reportPath}: executive paragraph ${paragraphIndex + 1} does not match executiveSummary.${field}`,
      );
    }
  }

  const scorecard = markdownTableAfterHeading(section, scorecardHeading);
  if (!scorecard || scorecard.header.join("|") !== "Area|Result|Status") {
    errors.push(`${reportPath}: executive scorecard must use Area | Result | Status`);
  } else {
    if (scorecard.rows.length !== expectedAreas.length) {
      errors.push(
        `${reportPath}: executive scorecard must contain exactly six outcome rows`,
      );
    }
    const stateRows = summary.scorecard;
    if (!Array.isArray(stateRows) || stateRows.length !== expectedAreas.length) {
      errors.push(`${statePath}: executiveSummary.scorecard must contain exactly six rows`);
    }
    expectedAreas.forEach((area, index) => {
      const reportRow = scorecard.rows[index];
      const stateRow = Array.isArray(stateRows) ? stateRows[index] : null;
      if (reportRow?.[0] !== area) {
        errors.push(`${reportPath}: scorecard row ${index + 1} must be ${area}`);
      }
      if (stateRow?.area !== area) {
        errors.push(`${statePath}: executiveSummary.scorecard[${index}].area must be ${area}`);
      }
      if (!reportRow || reportRow.length !== 3) return;
      if (!isExecutiveStatus(reportRow[2])) {
        errors.push(
          `${reportPath}: ${area} status must be one emoji plus a plain title-case label`,
        );
      }
      if (
        !stateRow ||
        typeof stateRow.result !== "string" ||
        !stateRow.result.trim() ||
        typeof stateRow.status !== "string" ||
        !stateRow.status.trim()
      ) {
        errors.push(`${statePath}: executiveSummary.scorecard[${index}] result/status is incomplete`);
        return;
      }
      if (plainMarkdown(reportRow[1]) !== plainMarkdown(stateRow.result)) {
        errors.push(`${reportPath}: ${area} result does not match canonical executive evidence`);
      }
      if (normalizeStatus(reportRow[2]) !== normalizeStatus(stateRow.status)) {
        errors.push(`${reportPath}: ${area} status does not match canonical executive evidence`);
      }
    });
    const inventoryPattern =
      /\b(?:baseline codebase|modified tracked files|new source\/?test files|total application files|generated migration artifacts|additions|deletions)\b/i;
    if (inventoryPattern.test(section)) {
      errors.push(`${reportPath}: executive scorecard must not contain file-count or change-inventory rows`);
    }
  }

  const nextSteps = markdownTableAfterHeading(section, nextStepsHeading);
  if (
    !nextSteps ||
    nextSteps.header.join("|") !== "Priority|Area|Action needed|Owner"
  ) {
    errors.push(
      `${reportPath}: next steps must use Priority | Area | Action needed | Owner`,
    );
  } else {
    if (nextSteps.rows.length < 1 || nextSteps.rows.length > 5) {
      errors.push(`${reportPath}: next steps must contain one to five customer actions`);
    }
    const stateSteps = summary.nextSteps;
    if (
      !Array.isArray(stateSteps) ||
      stateSteps.length < 1 ||
      stateSteps.length > 5 ||
      stateSteps.length !== nextSteps.rows.length
    ) {
      errors.push(
        `${statePath}: executiveSummary.nextSteps must contain the same one to five actions as the report`,
      );
    }
    nextSteps.rows.forEach((row, index) => {
      const stateStep = Array.isArray(stateSteps) ? stateSteps[index] : null;
      if (!["Required", "Recommended"].includes(row[0])) {
        errors.push(`${reportPath}: next-step priority must be Required or Recommended`);
      }
      if (row.length !== 4 || row.slice(1).some((cell) => !plainMarkdown(cell))) {
        errors.push(`${reportPath}: next-step row ${index + 1} is incomplete`);
      }
      if (
        !stateStep ||
        stateStep.customerAction !== true ||
        typeof stateStep.selectionReason !== "string" ||
        !stateStep.selectionReason.trim() ||
        typeof stateStep.sourceEvidence !== "string" ||
        !stateStep.sourceEvidence.trim()
      ) {
        errors.push(
          `${statePath}: executiveSummary.nextSteps[${index}] requires customerAction, selectionReason, and sourceEvidence`,
        );
        return;
      }
      for (const [column, field] of [
        [0, "priority"],
        [1, "area"],
        [2, "actionNeeded"],
        [3, "owner"],
      ]) {
        if (plainMarkdown(row[column]) !== plainMarkdown(stateStep[field])) {
          errors.push(
            `${reportPath}: next-step row ${index + 1} ${field} does not match canonical executive evidence`,
          );
        }
      }
      if (/\b(?:file count|change inventory|formatting|report artifact)\b/i.test(row.slice(1).join(" "))) {
        errors.push(
          `${reportPath}: next steps must contain customer production actions, not internal evidence work`,
        );
      }
    });
  }

  const recommendedPath = summary.recommendedProductionReadinessPath;
  if (
    !Array.isArray(recommendedPath) ||
    recommendedPath.length < 2 ||
    recommendedPath.length > 5 ||
    recommendedPath.some((step) => typeof step !== "string" || !step.trim())
  ) {
    errors.push(
      `${statePath}: executiveSummary.recommendedProductionReadinessPath requires two to five ordered actions`,
    );
  } else {
    const pathMatch = section.match(
      /^> \*\*Recommended production-readiness path:\*\* (.+)$/m,
    );
    if (
      !pathMatch ||
      plainMarkdown(pathMatch[1]).replace(/[.]$/, "") !==
        plainMarkdown(recommendedPath.join(" → "))
    ) {
      errors.push(
        `${reportPath}: recommended production-readiness path does not match canonical executive evidence`,
      );
    }
  }
  requireMatch(
    section,
    /The detailed conversion changes, validation evidence, test results, database activity, and\s+remaining exceptions are documented in the sections below\./,
    reportPath,
  );
}

function sectionBetween(content, startHeading, endHeading) {
  const start = content.indexOf(startHeading);
  const end = content.indexOf(endHeading, start + startHeading.length);
  if (start < 0 || end < 0) return null;
  return content.slice(start + startHeading.length, end);
}

function markdownTableAfterHeading(content, heading) {
  const headingIndex = content.indexOf(heading);
  if (headingIndex < 0) return null;
  const lines = content.slice(headingIndex + heading.length).split(/\r?\n/);
  const headerIndex = lines.findIndex((line) => line.trim().startsWith("|"));
  if (headerIndex < 0 || !/^\|(?:\s*:?-+:?\s*\|)+$/.test(lines[headerIndex + 1]?.trim() ?? "")) {
    return null;
  }
  const parseRow = (line) =>
    line
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((cell) => cell.trim());
  const rows = [];
  for (const line of lines.slice(headerIndex + 2)) {
    if (!line.trim().startsWith("|")) break;
    rows.push(parseRow(line));
  }
  return { header: parseRow(lines[headerIndex]), rows };
}

function plainMarkdown(value) {
  if (typeof value !== "string") return "";
  return value
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function isExecutiveStatus(value) {
  return /^(?:✅|⚠️|🧪|➖|❌)\s+[A-Z][A-Za-z]*(?: [a-z][A-Za-z-]*)*$/.test(value);
}

function validateNavigation(report, reportPath) {
  requireIncludes(report, '<a id="top"></a>', reportPath);
  requireIncludes(report, "## Table of Contents", reportPath);
  const headings = [...report.matchAll(/^## (?!Table of Contents$).+$/gm)].length;
  const backLinks = [...report.matchAll(/\[Back to Top\]\(#top\)/g)].length;
  if (headings !== backLinks) {
    errors.push(`${reportPath}: ${headings} content sections but ${backLinks} Back to Top links`);
  }
}

function validateDiffNavigation(report, diffs, reportPath, diffPath, noModifiedReason) {
  const reportLinks = [
    ...new Set(
      [...report.matchAll(/\]\(file-diffs\.md#(file-\d{5,})\)/g)].map(
        (match) => match[1],
      ),
    ),
  ];
  const tocLinks = [...diffs.matchAll(/^- \[[^\]]+\]\(#(file-\d{5,})\)$/gm)].map((match) => match[1]);
  const headings = [...diffs.matchAll(/^## (FILE-\d{5,})$/gm)].map((match) => match[1].toLowerCase());
  const backTop = [...diffs.matchAll(/\[Back to Top\]\(#top\)/g)].length;
  const backReport = [...diffs.matchAll(/\[Back to Conversion Report\]\(\.\/conversion-report\.md#modified-tracked-files\)/g)].length;
  if (reportLinks.length === 0 && !noModifiedReason) {
    errors.push(`${reportPath}: no modified-file links to file-diffs.md or noModifiedTrackedFilesReason`);
  }
  if (
    reportLinks.length !== tocLinks.length ||
    tocLinks.length !== headings.length ||
    headings.length !== backTop ||
    headings.length !== backReport
  ) {
    errors.push(
      `${diffPath}: report links (${reportLinks.length}), ToC links (${tocLinks.length}), headings (${headings.length}), Back to Top (${backTop}), and Back to Report (${backReport}) differ`,
    );
  }
  for (const link of [...reportLinks, ...tocLinks]) {
    if (!headings.includes(link)) errors.push(`${diffPath}: missing target ${link}`);
  }
}

function rejectPlaceholders(report, reportPath) {
  const placeholderPattern = /`<[^`>]+>`/;
  if (placeholderPattern.test(report)) {
    errors.push(`${reportPath}: unresolved template placeholder remains`);
  }
}

function requireOrderedHeadings(report, headings, reportPath) {
  const headingPositions = new Map(
    [...report.matchAll(/^## .+$/gm)].map((match) => [match[0], match.index]),
  );
  let position = -1;
  for (const heading of headings) {
    const next = headingPositions.get(heading) ?? -1;
    if (next <= position) {
      errors.push(`${reportPath}: missing or out-of-order heading ${heading}`);
    }
    position = next;
  }
}

function requireMatch(content, regex, file) {
  if (!regex.test(content)) errors.push(`${file}: missing ${regex}`);
}

function requireIncludes(content, value, file) {
  if (!content.includes(value)) errors.push(`${file}: missing ${value}`);
}

function firstNumber(...values) {
  return values.find((value) => Number.isFinite(value));
}

function includesFormattedNumber(content, value) {
  return (
    content.includes(String(value)) ||
    content.includes(value.toLocaleString("en-US"))
  );
}

function percentage(counter) {
  if (!counter || !Number.isFinite(counter.covered) || !Number.isFinite(counter.total) || counter.total <= 0) {
    return undefined;
  }
  return (counter.covered / counter.total) * 100;
}

function normalizeStatus(value) {
  if (typeof value !== "string") return "";
  return value
    .replace(/^[^\p{L}]+/u, "")
    .trim()
    .toLowerCase()
    .replaceAll("-", " ")
    .replaceAll("_", " ");
}

async function validateRuntimeExecution(phase, state, report, statePath) {
  if (!Object.hasOwn(state, "runtimeExecution")) {
    if (requireRuntime) {
      errors.push(`${statePath}: runtimeExecution final assembly proof is required (--require-runtime)`);
    }
    return;
  }
  try {
    await verifyMigrationBoundary({ root, phase, state, report, runtimeDirectory });
  } catch (error) {
    errors.push(`${statePath}: runtimeExecution final assembly proof failed (${error.message})`);
  }
}

function validateArtifactRecord(state, report, diffs, statePath, reportPath) {
  if (record) return;
  const evidence = state?.validation?.artifactValidation;
  if (!evidence || evidence.exitCode !== 0 || !evidence.validatedAt) {
    errors.push(`${statePath}: successful validation.artifactValidation record is missing`);
    return;
  }
  const reportHash = sha256(report);
  if (evidence.reportSha256 !== reportHash) {
    errors.push(`${statePath}: artifact-validation report digest does not match ${reportPath}`);
  }
  if (diffs && evidence.fileDiffSha256 !== sha256(diffs)) {
    errors.push(`${statePath}: artifact-validation file-diff digest does not match`);
  }
}

async function requiredText(relativePath) {
  try {
    return await readFile(path.join(root, relativePath), "utf8");
  } catch {
    errors.push(`${relativePath}: missing`);
    return null;
  }
}

async function requiredJson(relativePath) {
  try {
    const content = await readFile(path.join(root, relativePath), "utf8");
    return JSON.parse(content);
  } catch (error) {
    errors.push(`${relativePath}: missing or invalid JSON (${error.message})`);
    return null;
  }
}

async function optionalJson(relativePath) {
  try {
    const content = await readFile(path.join(root, relativePath), "utf8");
    return JSON.parse(content);
  } catch {
    return null;
  }
}

async function optionalText(relativePath) {
  try {
    return await readFile(path.join(root, relativePath), "utf8");
  } catch {
    return "";
  }
}

async function recordArtifactValidation() {
  const timestamp = new Date().toISOString();
  if (mode === "assessment" || mode === "both") {
    await updateValidationRecord(
      ".github/migrate-oracle-app/assessment/assessment-data.json",
      ".github/migrate-oracle-app/assessment/assessment-report.md",
      null,
      timestamp,
    );
  }
  if (mode === "conversion" || mode === "both") {
    await updateValidationRecord(
      ".github/migrate-oracle-app/conversions/conversion-state.json",
      ".github/migrate-oracle-app/conversions/conversion-report.md",
      ".github/migrate-oracle-app/conversions/file-diffs.md",
      timestamp,
    );
  }
}

async function updateValidationRecord(statePath, reportPath, diffPath, timestamp) {
  const absoluteState = path.join(root, statePath);
  const state = JSON.parse(await readFile(absoluteState, "utf8"));
  const report = await readFile(path.join(root, reportPath), "utf8");
  const diffs = diffPath ? await optionalText(diffPath) : "";
  state.validation ??= {};
  state.validation.artifactValidation = {
    command: `node <skill-directory>/scripts/validate-migration-artifacts.mjs <codebase> ${mode} --record${requireRuntime ? " --require-runtime" : ""}${runtimeDirectory ? " --runtime-directory <private-runtime-directory>" : ""}`,
    exitCode: 0,
    validatedAt: timestamp,
    reportSha256: sha256(report),
    fileDiffSha256: diffs ? sha256(diffs) : null,
  };
  const temporary = `${absoluteState}.tmp`;
  await writeFile(temporary, `${JSON.stringify(state, null, 2)}\n`, "utf8");
  await rename(temporary, absoluteState);
}

function sha256(content) {
  return createHash("sha256").update(content).digest("hex");
}
