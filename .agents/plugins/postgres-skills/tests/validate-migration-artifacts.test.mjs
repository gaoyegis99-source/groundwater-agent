import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { ingestCodingNotes, omittedCodingNotes, renderCodingNotes } from "../skills/migrate-oracle-app/scripts/coding-notes.mjs";
import { detectMigrationScope } from "../skills/migrate-oracle-app/scripts/detect-migration-scope.mjs";
import { indexOracleDdlFolder } from "../skills/migrate-oracle-app/scripts/index-oracle-ddl.mjs";
import { renderRunMetrics } from "../skills/migrate-oracle-app/scripts/format-run-metrics.mjs";
import { planPostgresToolDiscovery } from "../skills/migrate-oracle-app/scripts/postgres-provider-policy.mjs";
import {
  checkpointMigration,
  digestJson,
  finalizeMigrationArtifacts,
  openMigrationRuntime,
  withPrimaryExecution,
} from "../skills/migrate-oracle-app/scripts/migration-runtime.mjs";

const validator = path.resolve("skills/migrate-oracle-app/scripts/validate-migration-artifacts.mjs");

for (const [label, create] of [
  ["no-conversion", createNoConversionFixture],
  ["scope-review", createScopeReviewFixture],
]) {
  test(`coding notes are validated on both ${label} reporting exits`, async () => {
    const root = await create();
    try {
      const input = path.join(root, ".git", "notes-project");
      const file = path.join(input, "results", "application_guidance", "coding_notes.md");
      await mkdir(path.dirname(file), { recursive: true });
      await writeFile(file, "- CN-01: Keep signatures.\n- CN-02: Verify target contracts.\n");
      const notes = await ingestCodingNotes(root, input);
      notes.items[0].evaluated = true;
      notes.items[0].disposition = "not-applicable";
      notes.items[0].nextAction = null;
      notes.items[0].evidence = [{
        kind: "scope", reference: "scopeDecision.status",
        detail: `${label} stopped semantic conversion before application edits.`,
      }];
      await setCodingNotesFixture(root, notes, ["assessment", "conversion"]);
      const recorded = spawnSync(process.execPath, [validator, root, "both", "--record"], { encoding: "utf8" });
      assert.equal(recorded.status, 0, recorded.stderr);
      const verified = spawnSync(process.execPath, [validator, root, "both"], { encoding: "utf8" });
      assert.equal(verified.status, 0, verified.stderr);
      await writeFile(file, "- CN-01: Changed guidance.\n");
      const changed = spawnSync(process.execPath, [validator, root, "both"], { encoding: "utf8" });
      assert.notEqual(changed.status, 0);
      assert.match(changed.stderr, /Coding notes input changed/);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
}

test("full conversion validator rejects missing notes state and fabricated checklist evidence", async () => {
  const root = await createFixture();
  try {
    const statePath = conversionStatePath(root);
    const state = JSON.parse(await readFile(statePath, "utf8"));
    delete state.codingNotes;
    await writeFile(statePath, JSON.stringify(state));
    const absent = spawnSync(process.execPath, [validator, root, "conversion", "--record"], { encoding: "utf8" });
    assert.notEqual(absent.status, 0);
    assert.match(absent.stderr, /codingNotes ingestion evidence is required/);
    state.codingNotes = omittedCodingNotes();
    await writeFile(statePath, JSON.stringify(state));
    const reportPath = path.join(root, ".github", "migrate-oracle-app", "conversions", "conversion-report.md");
    const report = await readFile(reportPath, "utf8");
    await writeFile(reportPath, report.replace("Input status: **not-supplied**", "- [x] CN-01 - applied\n\nInput status: **processed**"));
    const fabricated = spawnSync(process.execPath, [validator, root, "conversion", "--record"], { encoding: "utf8" });
    assert.notEqual(fabricated.status, 0);
    assert.match(fabricated.stderr, /checklist does not match/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("combined artifact validation rejects different current notes inputs across reports", async () => {
  const root = await createNoConversionFixture();
  try {
    const input = path.join(root, ".git", "notes.md");
    await writeFile(input, "- CN-01: Keep signatures.\n");
    const notes = await ingestCodingNotes(root, input);
    await setCodingNotesFixture(root, notes, ["assessment"]);
    const result = spawnSync(process.execPath, [validator, root, "both", "--record"], { encoding: "utf8" });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /same current coding notes input snapshot/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validation records unavailable supplied notes honestly without claiming successful ingestion", async () => {
  const root = await createNoConversionFixture();
  try {
    const input = path.join(root, ".git", "missing-notes.md");
    for (const kind of ["missing", "unreadable"]) {
      if (kind === "unreadable") await writeFile(input, Buffer.from([0xff]));
      const notes = await ingestCodingNotes(root, input);
      assert.equal(notes.status, kind);
      await setCodingNotesFixture(root, notes, ["assessment", "conversion"]);
      const result = spawnSync(process.execPath, [validator, root, "both", "--record"], { encoding: "utf8" });
      assert.equal(result.status, 0, result.stderr);
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

async function setCodingNotesFixture(root, notes, phases) {
  for (const phase of phases) {
    const directory = path.join(root, ".github", "migrate-oracle-app", phase === "assessment" ? "assessment" : "conversions");
    const statePath = path.join(directory, phase === "assessment" ? "assessment-data.json" : "conversion-state.json");
    const reportPath = path.join(directory, phase === "assessment" ? "assessment-report.md" : "conversion-report.md");
    const state = JSON.parse(await readFile(statePath, "utf8"));
    const report = await readFile(reportPath, "utf8");
    await writeFile(reportPath, report.replace(renderCodingNotes(state.codingNotes), renderCodingNotes(notes)));
    state.codingNotes = notes;
    await writeFile(statePath, JSON.stringify(state));
  }
}

test("artifact validator accepts a no-database Angular early exit", async () => {
  const root = await createNoConversionFixture();
  try {
    const recorded = execFileSync(
      process.execPath,
      [validator, root, "assess-and-convert", "--record"],
      { encoding: "utf8" },
    );
    assert.match(recorded, /passed and was recorded/);
    const verified = execFileSync(
      process.execPath,
      [validator, root, "both"],
      { encoding: "utf8" },
    );
    assert.match(verified, /validation passed/);
    await assert.rejects(
      readFile(
        path.join(
          root,
          ".github",
          "migrate-oracle-app",
          "assessment",
          "test-discovery-baseline.json",
        ),
      ),
    );
    await assert.rejects(
      readFile(
        path.join(
          root,
          ".github",
          "migrate-oracle-app",
          "conversions",
          "file-diffs.md",
        ),
      ),
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects build or source work after no-conversion decision", async () => {
  const root = await createNoConversionFixture();
  try {
    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.scopeDecision.executionSuppression.testsAttempted = true;
    await writeFile(statePath, JSON.stringify(state));
    await writeFile(path.join(root, "src", "app.js"), "changed");

    const result = spawnSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /attempted dependency\/build\/test\/source\/database work/);
    assert.match(result.stderr, /changed application files/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator accepts a scope-review early stop", async () => {
  const root = await createScopeReviewFixture();
  try {
    const output = execFileSync(
      process.execPath,
      [validator, root, "assess-and-convert", "--record"],
      { encoding: "utf8" },
    );
    assert.match(output, /passed and was recorded/);
    assert.match(
      execFileSync(process.execPath, [validator, root, "both"], {
        encoding: "utf8",
      }),
      /validation passed/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator detects ignored build output after no-conversion decision", async () => {
  const root = await createNoConversionFixture();
  try {
    const targetFile = path.join(root, "target", "classes", "App.class");
    await mkdir(path.dirname(targetFile), { recursive: true });
    await writeFile(targetFile, "compiled");

    const result = spawnSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /changed application files: !! target\//);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator detects changes inside a pre-existing ignored build directory", async () => {
  const root = await createNoConversionFixture({ preexistingTarget: true });
  try {
    const targetFile = path.join(root, "target", "classes", "App.class");
    await writeFile(targetFile, "changed-compiled-output");

    const result = spawnSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(
      result.stderr,
      /changed build\/dependency output target/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator accepts a complete conversion template and evidence set", async () => {
  const root = await createFixture();
  try {
    const recorded = execFileSync(process.execPath, [validator, root, "conversion", "--record"], {
      encoding: "utf8",
    });
    assert.match(recorded, /passed and was recorded/);
    const output = execFileSync(process.execPath, [validator, root, "conversion"], {
      encoding: "utf8",
    });
    assert.match(output, /validation passed/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("--require-runtime refuses legacy artifacts without final assembly proof", async () => {
  const root = await createFixture();
  try {
    const result = runRuntimeValidator(root, { record: true });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /runtimeExecution final assembly proof is required/);
    const state = JSON.parse(await readFile(conversionStatePath(root), "utf8"));
    assert.equal(state.validation.artifactValidation, undefined);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("runtime proof is verified when present even without --require-runtime", async () => {
  const root = await createFixture();
  try {
    const statePath = conversionStatePath(root);
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.runtimeExecution = {
      schemaVersion: 1,
      primaryActorId: "primary",
      runId: "fabricated-run",
      checkpointSequence: 1,
      journalDigest: "a".repeat(64),
      boundary: "conversion-final",
    };
    await writeFile(statePath, JSON.stringify(state));
    const result = runRuntimeValidator(root, { record: true, requireRuntime: false });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /runtimeExecution final assembly proof failed/);
    assert.match(result.stderr, /registered primary actor/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("isolated journal-backed assembly proof passes record and independent verification", async () => {
  const root = await createFixture();
  try {
    const runtime = await finalizeRuntimeFixture(root);
    const statePath = conversionStatePath(root);
    const originalText = await readFile(statePath, "utf8");
    const original = JSON.parse(originalText);
    const recorded = runRuntimeValidator(root, { record: true });
    assert.equal(recorded.status, 0, recorded.stderr);
    assert.match(recorded.stdout, /passed and was recorded/);
    const recordedText = await readFile(statePath, "utf8");
    const recordedState = JSON.parse(recordedText);
    assert.notEqual(sha256(recordedText), sha256(originalText));
    assert.deepEqual(recordedState.runtimeExecution, original.runtimeExecution);
    assert.match(recordedState.validation.artifactValidation.command, /--require-runtime/);
    assert.match(recordedState.validation.artifactValidation.command, /--runtime-directory/);
    delete recordedState.validation.artifactValidation;
    delete original.validation.artifactValidation;
    assert.deepEqual(recordedState, original);
    const verified = runRuntimeValidator(root);
    assert.equal(verified.status, 0, verified.stderr);
    assert.match(verified.stdout, /validation passed/);
    const journal = await readFile(path.join(runtime.runDirectory, "checkpoints.jsonl"), "utf8");
    assert.equal(journal.trimEnd().split("\n").length, original.runtimeExecution.checkpointSequence + 1);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("runtime validation rejects altered phase state, proof, or rendered report", async () => {
  const root = await createFixture();
  try {
    await finalizeRuntimeFixture(root);
    const statePath = conversionStatePath(root);
    const reportPath = path.join(root, ".github", "migrate-oracle-app", "conversions", "conversion-report.md");
    const stateText = await readFile(statePath, "utf8");
    const reportText = await readFile(reportPath, "utf8");
    for (const mutate of [
      (state) => { state.validation.fabricatedGate = { status: "passed" }; },
      (state) => { state.runtimeExecution.primaryActorId = "test-child"; },
      (state) => { state.runtimeExecution.runId = "a-fabricated-run"; },
      (state) => { state.runtimeExecution.checkpointSequence += 1; },
      (state) => { state.runtimeExecution.journalDigest = "f".repeat(64); },
      (state) => { state.runtimeExecution.boundary = "assessment-final"; },
      (state) => { state.runtimeExecution.boundary = "combined-final"; },
    ]) {
      const state = JSON.parse(stateText);
      mutate(state);
      await writeFile(statePath, JSON.stringify(state));
      const result = runRuntimeValidator(root, { record: true });
      assert.notEqual(result.status, 0, JSON.stringify(state.runtimeExecution));
      assert.match(result.stderr, /runtimeExecution final assembly proof failed/);
    }
    await writeFile(statePath, stateText);
    await writeFile(reportPath, `${reportText}\nAltered rendered report.\n`);
    const changedReport = runRuntimeValidator(root, { record: true });
    assert.notEqual(changedReport.status, 0);
    assert.match(changedReport.stderr, /report.*assembly receipt/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("runtime proof must match actual owner, complete journal, and both artifact receipts", async () => {
  const root = await createFixture();
  try {
    const runtime = await finalizeRuntimeFixture(root);
    const ownerPath = path.join(runtime.runDirectory, "owner.json");
    const ownerText = await readFile(ownerPath, "utf8");
    const owner = JSON.parse(ownerText);
    owner.actorId = "test-child";
    await writeFile(ownerPath, JSON.stringify(owner));
    const wrongOwner = runRuntimeValidator(root, { record: true });
    assert.notEqual(wrongOwner.status, 0);
    assert.match(wrongOwner.stderr, /registered primary actor/);
    await writeFile(ownerPath, ownerText);

    const journalPath = path.join(runtime.runDirectory, "checkpoints.jsonl");
    const journalText = await readFile(journalPath, "utf8");
    const records = journalText.trimEnd().split("\n").map((line) => JSON.parse(line));
    for (const changedJournal of [
      journalText.trimEnd(),
      `${records.slice(0, -1).map((entry) => JSON.stringify(entry)).join("\n")}\n`,
      journalText.replace('"primary"', '"test-child"'),
    ]) {
      await writeFile(journalPath, changedJournal);
      const result = runRuntimeValidator(root, { record: true });
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /runtimeExecution final assembly proof failed/);
    }
    for (const artifactKind of ["report", "state"]) {
      const mutated = structuredClone(records);
      const boundary = mutated.at(-1);
      const artifact = boundary.artifacts.find((entry) => entry.path.endsWith(
        artifactKind === "report" ? "conversion-report.md" : "conversion-state.json",
      ));
      artifact.digest = "f".repeat(64);
      const { digest: _digest, ...payload } = boundary;
      boundary.digest = digestJson(payload);
      await writeFile(journalPath, `${mutated.map((entry) => JSON.stringify(entry)).join("\n")}\n`);
      const result = runRuntimeValidator(root, { record: true });
      assert.notEqual(result.status, 0, `${artifactKind} receipt was not verified`);
      assert.match(result.stderr, /runtimeExecution final assembly proof failed/);
    }
    await writeFile(journalPath, journalText);
    assert.equal(runRuntimeValidator(root, { record: true }).status, 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("later checkpoints invalidate stale final assembly proof", async () => {
  const root = await createFixture();
  try {
    const runtime = await finalizeRuntimeFixture(root);
    await withPrimaryExecution(runtime, "later-checkpoint", () => checkpointMigration(runtime, {
      phase: "conversion",
      operations: [{ op: "set", path: ["validation", "laterGate"], value: { status: "passed" } }],
    }));
    const result = runRuntimeValidator(root, { record: true });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /boundary is missing or stale/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("combined no-conversion proof derives private runtime metadata from Git", async () => {
  const root = await createNoConversionFixture();
  try {
    await finalizeRuntimeFixture(root, { phases: ["assessment", "conversion"], runtimeDirectory: null });
    const arguments_ = [validator, root, "both", "--require-runtime"];
    const recorded = spawnSync(process.execPath, [...arguments_, "--record"], { encoding: "utf8" });
    assert.equal(recorded.status, 0, recorded.stderr);
    const verified = spawnSync(process.execPath, arguments_, { encoding: "utf8" });
    assert.equal(verified.status, 0, verified.stderr);
    const state = JSON.parse(await readFile(conversionStatePath(root), "utf8"));
    assert.equal(state.runtimeExecution.boundary, "combined-final");
    const assessment = JSON.parse(await readFile(path.join(
      root, ".github", "migrate-oracle-app", "assessment", "assessment-data.json",
    ), "utf8"));
    assert.deepEqual(assessment.runtimeExecution, state.runtimeExecution);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("runtime directory CLI requires an explicit value", () => {
  const result = spawnSync(process.execPath, [
    validator, process.cwd(), "conversion", "--runtime-directory",
  ], { encoding: "utf8" });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /--runtime-directory requires/);
});

test("artifact validator rejects unreadable Run Metrics in either report", async () => {
  const root = await createNoConversionFixture();
  try {
    const replacements = [
      [
        "| Phase | Start | Completed | Wall Clock Duration |",
        "| Phase | Started | Completed | Wall-clock duration |",
      ],
      ["3 minutes, 0 seconds", "180 seconds"],
      ["3 minutes, 0 seconds", "3m 0s"],
      ["4:10pm, June 10, 2026", "2026-06-10T23:10:00.000Z"],
      ["| Total Runs |", "| Entire run |"],
    ];
    for (const [phase, folder, name] of [
      ["assessment", "assessment", "assessment-report.md"],
      ["conversion", "conversions", "conversion-report.md"],
    ]) {
      const reportPath = path.join(root, ".github", "migrate-oracle-app", folder, name);
      const original = await readFile(reportPath, "utf8");
      for (const [before, after] of replacements) {
        assert.ok(original.includes(before));
        await writeFile(reportPath, original.replace(before, after));
        const result = spawnSync(process.execPath, [validator, root, phase], {
          encoding: "utf8",
        });
        assert.notEqual(result.status, 0);
        assert.match(result.stderr, /Run Metrics must match recorded timings/);
      }
      await writeFile(reportPath, original);
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects contradictory Run Metrics evidence", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(root, ".github", "migrate-oracle-app", "conversions", "conversion-state.json");
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.runMetrics.conversion.wallClockElapsedMs += 1;
    await writeFile(statePath, JSON.stringify(state));
    const result = spawnSync(process.execPath, [validator, root, "conversion"], {
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /wallClockElapsedMs does not match its timestamps/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator requires the same final timing snapshot in combined reports", async () => {
  const root = await createNoConversionFixture();
  try {
    const conversion = path.join(root, ".github", "migrate-oracle-app", "conversions");
    const statePath = path.join(conversion, "conversion-state.json");
    const reportPath = path.join(conversion, "conversion-report.md");
    const state = JSON.parse(await readFile(statePath, "utf8"));
    const originalSection = renderRunMetrics(state.runMetrics);
    state.runMetrics.timeZone = "UTC";
    await writeFile(statePath, JSON.stringify(state));
    const report = await readFile(reportPath, "utf8");
    await writeFile(reportPath, report.replace(originalSection, renderRunMetrics(state.runMetrics)));
    const result = spawnSync(process.execPath, [validator, root, "both"], {
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /same final timing snapshot/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects missing Run Metrics evidence", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(root, ".github", "migrate-oracle-app", "conversions", "conversion-state.json");
    const state = JSON.parse(await readFile(statePath, "utf8"));
    delete state.runMetrics;
    await writeFile(statePath, JSON.stringify(state));
    const result = spawnSync(process.execPath, [validator, root, "conversion"], {
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /runMetrics evidence is required/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator accepts VS Code PostgreSQL extension provider evidence", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.postgresProvider = postgresProviderEvidence("vscode-copilot");
    state.targetMetadataAcquisition.toolCallCounts = {
      postgres_mcp_db_context: 0,
      pgsql_db_context: 0,
      pgsql_query: state.targetMetadataAcquisition.queryCalls,
    };
    await writeFile(statePath, JSON.stringify(state));

    execFileSync(
      process.execPath,
      [validator, root, "conversion", "--record"],
      { encoding: "utf8" },
    );
    const output = execFileSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.match(output, /validation passed/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects PostgreSQL provider fallback or mixing", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.postgresProvider.fallbackAttempted = true;
    state.postgresProvider.mixedProviderOperationCount = 1;
    await writeFile(statePath, JSON.stringify(state));

    const result = spawnSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(
      result.stderr,
      /postgresProvider selection\/fallback evidence is invalid/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects premature provider absence after wrong-family or truncated discovery", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(root, ".github", "migrate-oracle-app", "conversions", "conversion-state.json");
    const original = await readFile(statePath, "utf8");
    const extension = postgresProviderEvidence("vscode-copilot");
    const initialQuery = planPostgresToolDiscovery({ harness: "vscode-copilot" }).nextQueries[0];
    const attempts = [
      {
        query: "PostgreSQL MCP VS Code extension pgsql list profiles connect capabilities query",
        status: "completed",
        returnedTools: Object.values(postgresProviderEvidence().selectedTools).slice(0, 5),
      },
      {
        query: initialQuery,
        status: "completed",
        returnedTools: Object.values(extension.selectedTools).slice(0, 5),
      },
    ];
    for (const attempt of attempts) {
      const state = JSON.parse(original);
      state.postgresProvider = {
        ...extension,
        available: false,
        selectedTools: {},
        reason: "The extension provider was not exposed.",
        discovery: {
          strategy: "exact-operation-names",
          status: "unavailable",
          initialTools: [],
          searches: [attempt],
        },
      };
      await writeFile(statePath, JSON.stringify(state));
      const result = spawnSync(process.execPath, [validator, root, "conversion"], {
        encoding: "utf8",
      });
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /exact-name discovery is incomplete/);
      assert.match(result.stderr, /pgsql_modify/);
      assert.match(result.stderr, /pgsql_disconnect/);
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator accepts native extension discovery recovered from wrong-provider results", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(root, ".github", "migrate-oracle-app", "conversions", "conversion-state.json");
    const state = JSON.parse(await readFile(statePath, "utf8"));
    const evidence = postgresProviderEvidence("vscode-copilot");
    const tools = Object.values(evidence.selectedTools);
    const harness = evidence.harness;
    const initial = planPostgresToolDiscovery({ harness });
    const searches = [
      {
        query: "PostgreSQL MCP VS Code extension pgsql list connection profiles connect capabilities",
        status: "completed",
        returnedTools: Object.values(state.postgresProvider.selectedTools).slice(0, 5),
      },
      {
        query: initial.nextQueries[0],
        status: "completed",
        returnedTools: [tools[0], tools[1], tools[2], tools[6]],
      },
    ];
    const partial = planPostgresToolDiscovery({ harness, searches });
    for (const query of partial.nextQueries) {
      searches.push({
        query,
        status: "completed",
        returnedTools: [tools.find((tool) => tool.endsWith(query))],
      });
    }
    evidence.discovery = {
      strategy: "exact-operation-names",
      status: "available",
      initialTools: [],
      searches,
    };
    state.postgresProvider = evidence;
    state.targetMetadataAcquisition.toolCallCounts = {
      postgres_mcp_db_context: 0,
      pgsql_db_context: 0,
      pgsql_query: state.targetMetadataAcquisition.queryCalls,
    };
    await writeFile(statePath, JSON.stringify(state));
    const recorded = execFileSync(process.execPath, [validator, root, "conversion", "--record"], {
      encoding: "utf8",
    });
    assert.match(recorded, /passed and was recorded/);
    assert.match(
      execFileSync(process.execPath, [validator, root, "conversion"], { encoding: "utf8" }),
      /validation passed/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator requires actual provider discovery evidence rather than guessed tools", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(root, ".github", "migrate-oracle-app", "conversions", "conversion-state.json");
    const state = JSON.parse(await readFile(statePath, "utf8"));
    delete state.postgresProvider.discovery;
    await writeFile(statePath, JSON.stringify(state));
    const missing = spawnSync(process.execPath, [validator, root, "conversion"], {
      encoding: "utf8",
    });
    assert.notEqual(missing.status, 0);
    assert.match(missing.stderr, /exact-name discovery evidence is required/);

    state.postgresProvider.discovery = {
      strategy: "exact-operation-names",
      status: "available",
      initialTools: Object.values(postgresProviderEvidence("vscode-copilot").selectedTools),
      searches: [],
    };
    await writeFile(statePath, JSON.stringify(state));
    const guessed = spawnSync(process.execPath, [validator, root, "conversion"], {
      encoding: "utf8",
    });
    assert.notEqual(guessed.status, 0);
    assert.match(guessed.stderr, /exact-name discovery is incomplete/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects target metadata from the other provider", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.postgresProvider = postgresProviderEvidence("vscode-copilot");
    await writeFile(statePath, JSON.stringify(state));

    const result = spawnSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(
      result.stderr,
      /target metadata query counts or batch limits are invalid/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator accepts a fresh supplied Oracle DDL index", async () => {
  const root = await createFixture();
  const oracleDdlFolder = await mkdtemp(
    path.join(process.cwd(), ".migration-oracle-ddl-"),
  );
  try {
    const schemaRoot = path.join(oracleDdlFolder, "exports", "APP");
    for (const objectType of [
      "FUNCTION",
      "INDEX",
      "PACKAGE",
      "PROCEDURE",
      "SCHEMA",
      "SEQUENCE",
      "TABLE",
      "TRIGGER",
      "VIEW",
    ]) {
      await mkdir(path.join(schemaRoot, objectType), { recursive: true });
    }
    await writeFile(
      path.join(schemaRoot, "PROCEDURE", "p_known.sql"),
      "CREATE OR REPLACE PROCEDURE APP.P_KNOWN AS BEGIN NULL; END;",
    );
    const index = await indexOracleDdlFolder(oracleDdlFolder, {
      schema: "APP",
    });
    const indexText = `${JSON.stringify(index, null, 2)}\n`;
    const indexRelativePath =
      ".github/migrate-oracle-app/assessment/oracle-ddl-index.json";
    await writeFile(path.join(root, indexRelativePath), indexText);

    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.oracleDdlSource = {
      supplied: true,
      folderPath: oracleDdlFolder,
      schema: index.schema,
      resolvedSchemaPath: index.resolvedSchemaPath,
      relativeSchemaPath: index.relativeSchemaPath,
      layout: index.layout,
      migrationName: index.migrationName,
      objectTypeDirectories: index.objectTypeDirectories,
      discoveredSchemaPaths: index.discoveredSchemaPaths,
      limits: index.limits,
      totalBytes: index.totalBytes,
      status: "valid",
      folderDigest: index.folderDigest,
      indexPath: indexRelativePath,
      indexSha256: sha256(indexText),
      candidateFiles: index.candidateFiles,
      indexedObjects: index.indexedObjects,
      files: index.files,
      errors: index.errors,
      warnings: index.warnings,
      missingTargetObjects: [],
      missingObjectResolutions: [],
    };
    await writeFile(statePath, JSON.stringify(state));

    execFileSync(
      process.execPath,
      [validator, root, "conversion", "--record"],
      { encoding: "utf8" },
    );
    const output = execFileSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.match(output, /validation passed/);
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(oracleDdlFolder, { recursive: true, force: true });
  }
});

test("artifact validator rejects inventory rows in the executive scorecard", async () => {
  const root = await createFixture();
  try {
    const reportPath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-report.md",
    );
    const report = await readFile(reportPath, "utf8");
    await writeFile(
      reportPath,
      report.replace(
        "| Application conversion | Selected application database paths converted to PostgreSQL | ✅ Complete |",
        "| Baseline codebase | 368 tracked files | ✅ Ready |",
      ),
    );
    const result = spawnSync(process.execPath, [validator, root, "conversion"], {
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /scorecard row 1 must be Application conversion/);
    assert.match(result.stderr, /must not contain file-count or change-inventory rows/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects next steps that drift from canonical customer actions", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.executiveSummary.nextSteps[0].owner = "Database team";
    await writeFile(statePath, JSON.stringify(state));
    const result = spawnSync(process.execPath, [validator, root, "conversion"], {
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0);
    assert.match(
      result.stderr,
      /next-step row 1 owner does not match canonical executive evidence/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects every PostgreSQL db-context tool call", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.targetMetadataAcquisition.dbContextCalls = 1;
    state.targetMetadataAcquisition.toolCallCounts.postgres_mcp_db_context = 1;
    state.targetMetadataAcquisition.policyViolations = [
      "postgres_mcp_db_context timed out",
    ];
    await writeFile(statePath, JSON.stringify(state));
    const result = spawnSync(process.execPath, [validator, root, "conversion"], {
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0);
    assert.match(
      result.stderr,
      /prohibited target metadata call recorded for postgres_mcp_db_context/,
    );
    assert.match(
      result.stderr,
      /targetMetadataAcquisition\.dbContextCalls must be zero/,
    );
    assert.match(result.stderr, /target metadata policy violations must be empty/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator accepts disclosed signature-only fail-closed reconstruction", async () => {
  const root = await createFixture();
  try {
    await configureSignatureFallbackFixture(root, true);

    execFileSync(
      process.execPath,
      [validator, root, "conversion", "--record"],
      { encoding: "utf8" },
    );
    const output = execFileSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.match(output, /validation passed/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

for (const harness of ["copilot-cli", "vscode-copilot"]) {
  test(`artifact validator accepts reconstructed persistent CREATE through ${harness}`, async () => {
    const root = await createFixture();
    try {
      await configurePersistentDdlFixture(root, "created", { harness });
      execFileSync(process.execPath, [validator, root, "conversion", "--record"], {
        encoding: "utf8",
      });
      assert.match(
        execFileSync(process.execPath, [validator, root, "conversion"], {
          encoding: "utf8",
        }),
        /validation passed/,
      );
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
}

test("artifact validator rejects leaving validated reconstructed DDL undeployed", async () => {
  const root = await createFixture();
  try {
    await configurePersistentDdlFixture(root, "not-executed");
    const result = spawnSync(process.execPath, [validator, root, "conversion"], {
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0);
    assert.match(
      result.stderr,
      /validated missing object must be created when create-persistent-ddl is enabled/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator accepts explicit persistent CREATE safety limitations", async () => {
  const root = await createFixture();
  try {
    await configurePersistentDdlFixture(root, "not-executed", { writable: false });
    assert.match(
      execFileSync(process.execPath, [validator, root, "conversion", "--record"], {
        encoding: "utf8",
      }),
      /validation passed/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator accepts verified existing objects without replacing them", async () => {
  const root = await createFixture();
  try {
    await configurePersistentDdlFixture(root, "already-present", { writable: false });
    assert.match(
      execFileSync(process.execPath, [validator, root, "conversion", "--record"], {
        encoding: "utf8",
      }),
      /validation passed/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator preserves explicit failed persistent CREATE results", async () => {
  const root = await createFixture();
  try {
    await configurePersistentDdlFixture(root, "failed");
    assert.match(
      execFileSync(process.execPath, [validator, root, "conversion", "--record"], {
        encoding: "utf8",
      }),
      /validation passed/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects unverifiable persistent CREATE evidence", async () => {
  const cases = [
    {
      mutate: (state, resolution) => {
        resolution.persistentCreate.execution.ddlSha256 = "c".repeat(64);
      },
      message: /exact deployed DDL hash/,
    },
    {
      mutate: (state, resolution) => {
        resolution.persistentCreate.execution.targetDigest = "d".repeat(64);
      },
      message: /exact deployed DDL hash/,
    },
    {
      mutate: (state, resolution) => {
        resolution.persistentCreate.execution.tool = state.postgresProvider.selectedTools.query;
      },
      message: /selected modify-tool evidence/,
    },
    {
      mutate: (state, resolution) => {
        resolution.persistentCreate.verification = null;
      },
      message: /immediate exact-object target verification/,
    },
    {
      mutate: (state, resolution) => {
        resolution.persistentCreate.verification.objectIdentity = "other.p_unknown(bigint)";
      },
      message: /immediate exact-object target verification/,
    },
    {
      mutate: (state, resolution) => {
        resolution.persistentCreate.verification.contractMatches = false;
      },
      message: /immediate exact-object target verification/,
    },
    {
      mutate: (state, resolution) => {
        resolution.persistentCreate.verification.expectedDdlSha256 = "e".repeat(64);
      },
      message: /immediate exact-object target verification/,
    },
    {
      mutate: (state, resolution) => {
        resolution.persistentCreate.verification.verifiedAt = "2024-12-31T23:59:00.000Z";
      },
      message: /immediate exact-object target verification/,
    },
    {
      mutate: (state, resolution) => {
        resolution.persistentCreate.authorization.source = "previous-request";
      },
      message: /current-request authorization/,
    },
    {
      mutate: (state, resolution) => {
        resolution.persistentCreate.authorization.refreshedTargetDigest = "f".repeat(64);
      },
      message: /fresh exact target context/,
    },
    {
      mutate: (state, resolution) => {
        resolution.persistentCreate.authorization.profileAccessMode = "ro";
      },
      message: /execution lacks write safety/,
    },
    {
      mutate: (state, resolution) => {
        resolution.persistentCreate.authorization.safetyChecks.createOnly = false;
      },
      message: /execution lacks write safety/,
    },
    {
      mutate: (state, resolution) => {
        resolution.persistentCreateAttempted = false;
      },
      message: /requested\/attempted\/verified state is inconsistent/,
    },
    {
      mutate: (state) => {
        state.options["create-persistent-ddl"] = false;
      },
      message: /disabled option must not perform a persistent write/,
    },
    {
      mutate: (state) => {
        delete state.options["create-persistent-ddl"];
      },
      message: /options.create-persistent-ddl must be an explicit boolean/,
    },
    {
      mutate: (state) => {
        state.optionResults["create-persistent-ddl"].status = "not-applicable";
      },
      message: /option result does not match verified target creation/,
    },
  ];
  for (const { mutate, message } of cases) {
    const root = await createFixture();
    try {
      await configurePersistentDdlFixture(root, "created");
      const statePath = path.join(
        root,
        ".github",
        "migrate-oracle-app",
        "conversions",
        "conversion-state.json",
      );
      const state = JSON.parse(await readFile(statePath, "utf8"));
      mutate(state, state.oracleDdlSource.missingObjectResolutions[0]);
      await writeFile(statePath, JSON.stringify(state));
      const result = spawnSync(process.execPath, [validator, root, "conversion"], {
        encoding: "utf8",
      });
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, message);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  }
});

test("artifact validator rejects incomplete persistent DDL as runtime-validation-needed", async () => {
  const root = await createFixture();
  try {
    await configurePersistentDdlFixture(root, "not-executed", { writable: false });
    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.status = "RUNTIME_VALIDATION_NEEDED";
    await writeFile(statePath, JSON.stringify(state));
    const result = spawnSync(process.execPath, [validator, root, "conversion"], {
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /incomplete persistent DDL must record a reason, next action, and PARTIAL/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator accepts requested persistent DDL with no missing objects", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.options["create-persistent-ddl"] = true;
    state.optionResults["create-persistent-ddl"] = {
      requested: true,
      status: "not-applicable",
      optionSatisfied: true,
      applicableMissingObjects: 0,
      reason: "The exact code-derived target manifest contains no missing objects.",
      nextAction: null,
    };
    await writeFile(statePath, JSON.stringify(state));
    assert.match(
      execFileSync(process.execPath, [validator, root, "conversion", "--record"], {
        encoding: "utf8",
      }),
      /validation passed/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects a silent no-op for a missing Oracle body", async () => {
  const root = await createFixture();
  try {
    await configureSignatureFallbackFixture(root, false);
    const result = spawnSync(process.execPath, [validator, root, "conversion"], {
      encoding: "utf8",
    });

    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /missing-body implementation is not explicitly fail-closed/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects missing object matrix and regressed test totals", async () => {
  const root = await createFixture({
    finalTests: 9,
    baselineTests: 10,
    includeMatrix: false,
    includeReconciliation: false,
  });
  try {
    const result = spawnSync(process.execPath, [validator, root, "conversion"], {
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /below baseline/);
    assert.match(result.stderr, /database-object test matrix is missing/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects a missing Vintage engine and undiscovered JUnit 4 class", async () => {
  const root = await createFixture({
    includeVintageEngine: false,
    omitVintageClassFromReports: true,
  });
  try {
    const result = spawnSync(process.execPath, [validator, root, "conversion"], {
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /approved baseline lacks adapter org\.junit\.vintage:junit-vintage-engine/);
    assert.match(result.stderr, /eligible containers without fresh execution reports: com\.example\.LegacyTest/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects exposed failures without recorded repairs", async () => {
  const root = await createFixture({ exposedFailures: 3 });
  try {
    const result = spawnSync(process.execPath, [validator, root, "conversion"], {
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /exposed failures were not reconciled to recorded repairs/);
    assert.match(result.stderr, /failure classes without repair attempts/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects safely convertible deferred findings", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.conversionScope.categories[0] = {
      category: "runtime-datasource",
      discovered: 1,
      converted: 0,
      retained: 0,
      deferred: 1,
      safeConvertibleRemaining: 1,
      evidence: "Deferred despite safe placeholder conversion.",
    };
    state.conversionScope.findings[0].status = "deferred";
    state.conversionScope.findings[0].safeToConvert = true;
    state.conversionScope.findings[0].reason = "Credentials unavailable.";
    await writeFile(statePath, JSON.stringify(state));

    const result = spawnSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /safely convertible finding was deferred/);
    assert.match(result.stderr, /does not reconcile/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

for (const lane of ["unit", "integration"]) {
  test(`artifact validator independently enforces two ${lane} waves`, async () => {
    const root = await createFixture();
    try {
      const statePath = path.join(
        root,
        ".github",
        "migrate-oracle-app",
        "conversions",
        "conversion-state.json",
      );
      const state = JSON.parse(await readFile(statePath, "utf8"));
      state.coverage[`${lane}Waves`] =
        state.coverage[`${lane}Waves`].slice(0, 1);
      await writeFile(statePath, JSON.stringify(state));

      const result = spawnSync(
        process.execPath,
        [validator, root, "conversion"],
        { encoding: "utf8" },
      );
      assert.notEqual(result.status, 0);
      assert.match(
        result.stderr,
        new RegExp(`fewer than two ${lane} waves`),
      );
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
}

test("artifact validator rejects serial execution when independent child-agent work exists", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.orchestration.assignments = [];
    state.orchestration.parallelBatches = [];
    state.orchestration.metrics.delegatedTasks = 0;
    state.orchestration.metrics.parallelBatches = 0;
    await writeFile(statePath, JSON.stringify(state));

    const result = spawnSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /no child-agent parallel batch executed/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects overlapping child-agent write ownership", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    for (const assignment of state.orchestration.assignments) {
      assignment.mode = "write-enabled";
      assignment.ownedPaths = ["src/main"];
      assignment.changedPaths = [`src/main/${assignment.id}.java`];
    }
    await writeFile(statePath, JSON.stringify(state));

    const result = spawnSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /write-enabled assignments .* overlap/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects repeated compilation and packaging during Maven waves", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.performanceExecution.testCompile.count = 10;
    state.performanceExecution.packaging.cleanPackageCount = 2;
    await writeFile(statePath, JSON.stringify(state));

    const result = spawnSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /one test-compile per verified input\/output generation/);
    assert.match(result.stderr, /final clean-package\/archive evidence is invalid/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator allows recompilation only with recorded generation invalidation", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(root, ".github", "migrate-oracle-app", "conversions", "conversion-state.json");
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.performanceExecution.testCompile.count = 2;
    state.performanceExecution.testCompile.invalidations = [{
      reason: "Generated test sources changed.",
      beforeDigest: "previous-compiled-input",
      afterDigest: state.performanceExecution.testCompile.compiledInputDigest,
    }];
    state.performanceExecution.fullSuite.generationCount = 2;
    state.performanceExecution.fullSuite.runnerExecutions = 2;
    await writeFile(statePath, JSON.stringify(state));
    assert.match(
      execFileSync(process.execPath, [validator, root, "conversion", "--record"], { encoding: "utf8" }),
      /passed and was recorded/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects per-process Docker recreation without configuration changes", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(root, ".github", "migrate-oracle-app", "conversions", "conversion-state.json");
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.performanceExecution.dockerEnvironment.containerStartCount = 4;
    await writeFile(statePath, JSON.stringify(state));
    const result = spawnSync(process.execPath, [validator, root, "conversion"], { encoding: "utf8" });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /one verified run-scoped shared environment/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator requires additional coverage value for an optional third wave", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(root, ".github", "migrate-oracle-app", "conversions", "conversion-state.json");
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.coverage.unitWaves.push({
      wave: 3,
      plannedBehaviors: ["Rollback branch"],
      testsAddedOrRepaired: ["RollbackTest"],
      result: "passed",
    });
    state.performanceExecution.waveRunner.uniqueReportDirectories = 5;
    state.performanceExecution.waveRunner.uniqueExecutionDataFiles = 5;
    await writeFile(statePath, JSON.stringify(state));
    const result = spawnSync(process.execPath, [validator, root, "conversion"], { encoding: "utf8" });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /optional waves require a specific uncovered behavior/);

    state.coverage.unitWaves[2].continuationEvidence = {
      uncoveredBehavior: "Rollback branch",
      expectedNewCoveredLines: 1,
      expectedNewCoveredBranches: 1,
      reason: "High-risk rollback behavior remains untested after the mandatory waves.",
    };
    await writeFile(statePath, JSON.stringify(state));
    assert.match(
      execFileSync(process.execPath, [validator, root, "conversion", "--record"], { encoding: "utf8" }),
      /passed and was recorded/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator accepts documented low-yield deferral but rejects high-risk deferral", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(root, ".github", "migrate-oracle-app", "conversions", "conversion-state.json");
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.coverage.stopDecision = {
      kind: "low-marginal-value",
      reason: "Only low-priority already-characterized wrapper variants remain.",
    };
    state.coverage.rankedBacklogFinal = [{
      classification: "deferred-low-yield",
      safelyTestable: true,
      priority: "low",
      highRisk: false,
      evidence: "Measured lane counters plateaued; the remaining wrapper has no distinct database behavior.",
      expectedNewCoveredLines: 1,
      expectedNewCoveredBranches: 0,
      estimatedAdditionalRuntimeMs: 60000,
      nextAction: "Revisit when the wrapper gains a distinct contract or behavior.",
    }];
    await writeFile(statePath, JSON.stringify(state));
    assert.match(
      execFileSync(process.execPath, [validator, root, "conversion", "--record"], { encoding: "utf8" }),
      /passed and was recorded/,
    );
    state.coverage.rankedBacklogFinal[0].highRisk = true;
    await writeFile(statePath, JSON.stringify(state));
    const result = spawnSync(process.execPath, [validator, root, "conversion"], { encoding: "utf8" });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /documented low-yield gaps/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects a PostgreSQL driver found only inside the WAR", async () => {
  const root = await createFixture();
  try {
    const conversion = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
    );
    await writeFile(
      path.join(root, "server.xml"),
      `<server>
        <library id="postgresql-lib">
          <fileset dir="\${server.config.dir}/resources/lib" includes="postgresql.jar"/>
        </library>
        <dataSource><jdbcDriver libraryRef="postgresql-lib"/><properties.postgresql/></dataSource>
      </server>`,
    );
    const archivePath = await createDriverArchive(root, {
      externalDriver: false,
      warDriver: true,
    });
    const plan = {
      archives: [
        {
          id: "deployment-zip",
          path: path.relative(root, archivePath).replaceAll("\\", "/"),
          descriptors: [
            {
              sourcePath: "server.xml",
              entry: "server.xml",
              runtimeDriverPatterns: [
                "server/resources/lib/postgresql.jar",
              ],
            },
          ],
        },
      ],
    };
    await writeFile(
      path.join(conversion, "driver-packaging-plan.json"),
      JSON.stringify(plan),
    );
    const statePath = path.join(conversion, "conversion-state.json");
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.validation.runtimeDriverPackaging = {
      required: true,
      status: "passed",
      planPath:
        ".github/migrate-oracle-app/conversions/driver-packaging-plan.json",
      descriptorSourcePaths: ["server.xml"],
      archivesInspected: 1,
      warOnlyDriverDetected: false,
      reason: null,
    };
    await writeFile(statePath, JSON.stringify(state));
    const reportPath = path.join(conversion, "conversion-report.md");
    const report = await readFile(reportPath, "utf8");
    await writeFile(
      reportPath,
      report.replace(
        "| None | None | Not applicable | None | None | ➖ Not applicable |",
        "| target/application.zip | server.xml → server.xml | server/resources/lib/postgresql.jar | None | WEB-INF/lib-provided/postgresql.jar | ✅ Passed |",
      ),
    );

    const result = spawnSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(
      result.stderr,
      /deployment archive does not satisfy runtime PostgreSQL driver paths/,
    );
    assert.match(result.stderr, /only inside nested WAR/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects Docker Desktop startup while the option is off", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    state.validation.dockerEngineAvailability = {
      optionEnabled: false,
      engineRunningAtPreflight: false,
      desktopDetected: true,
      startAttempted: true,
      desktopStateChanged: true,
      startupCommand: "docker desktop start",
      engineResponsive: true,
      status: "started",
      reason: "Started without option.",
    };
    await writeFile(statePath, JSON.stringify(state));

    const result = spawnSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(
      result.stderr,
      /Docker Desktop changed state while its option was off/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects unused artifact removal while option is off", async () => {
  const root = await createFixture();
  try {
    const statePath = path.join(
      root,
      ".github",
      "migrate-oracle-app",
      "conversions",
      "conversion-state.json",
    );
    const state = JSON.parse(await readFile(statePath, "utf8"));
    const candidate =
      state.validation.unusedArtifactDisposition.candidates[0];
    candidate.action = "removed";
    candidate.removed = true;
    state.validation.unusedArtifactDisposition.deletedPaths = [candidate.path];
    await writeFile(statePath, JSON.stringify(state));

    const result = spawnSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(
      result.stderr,
      /unused artifact was removed while remove-unused-artifacts was off/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator detects an unreported deleted JAR from Git", async () => {
  const root = await createFixture();
  try {
    const jarPath = path.join(root, "lib", "DealOutlookUI.jar");
    await mkdir(path.dirname(jarPath), { recursive: true });
    await writeFile(jarPath, "placeholder");
    execFileSync("git", ["init"], { cwd: root, stdio: "ignore" });
    execFileSync("git", ["config", "user.email", "test@example.invalid"], {
      cwd: root,
    });
    execFileSync("git", ["config", "user.name", "Artifact Test"], {
      cwd: root,
    });
    execFileSync("git", ["add", "."], { cwd: root });
    execFileSync("git", ["commit", "-m", "baseline"], {
      cwd: root,
      stdio: "ignore",
    });
    await rm(jarPath);

    const result = spawnSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(
      result.stderr,
      /repository artifact was deleted while remove-unused-artifacts was off/,
    );
    assert.match(
      result.stderr,
      /deleted repository artifact lacks reconciled removal evidence/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

for (const adapter of ["dotnet", "python", "cpp", "c"]) {
  test(`artifact validator accepts complete ${adapter} discovery evidence`, async () => {
    const root = await createLanguageFixture(adapter);
    try {
      const recorded = execFileSync(
        process.execPath,
        [validator, root, "conversion", "--record"],
        { encoding: "utf8" },
      );
      assert.match(recorded, /passed and was recorded/);
      const output = execFileSync(
        process.execPath,
        [validator, root, "conversion"],
        { encoding: "utf8" },
      );
      assert.match(output, /validation passed/);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
}

test("artifact validator accepts a completed requested target-write gate", async () => {
  const root = await createFixture();
  try {
    await setTargetWriteFixture(root, "passed", "READY");
    const output = execFileSync(
      process.execPath,
      [validator, root, "conversion", "--record"],
      { encoding: "utf8" },
    );
    assert.match(output, /passed and was recorded/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects deferred requested writes as runtime-validation-needed", async () => {
  const root = await createFixture();
  try {
    await setTargetWriteFixture(
      root,
      "not-executed",
      "RUNTIME_VALIDATION_NEEDED",
    );
    const result = spawnSync(
      process.execPath,
      [validator, root, "conversion"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(
      result.stderr,
      /requested but unexecuted target writes must make conversion PARTIAL/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator maps assess-and-convert to both instead of passing without checks", async () => {
  const root = await createFixture();
  try {
    const result = spawnSync(
      process.execPath,
      [validator, root, "assess-and-convert"],
      { encoding: "utf8" },
    );
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /assessment-report\.md/);
    assert.doesNotMatch(result.stdout, /validation passed/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("artifact validator rejects unknown modes", async () => {
  const root = await createFixture();
  try {
    const result = spawnSync(
      process.execPath,
      [validator, root, "assess-and-convert-typo"],
      { encoding: "utf8" },
    );
    assert.equal(result.status, 2);
    assert.match(result.stderr, /unsupported validation mode/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

function runMetricsEvidence(conversionRan = true) {
  const assessment = {
    status: "completed",
    startedAt: "2026-06-10T23:10:00.000Z",
    completedAt: "2026-06-10T23:13:00.000Z",
    wallClockElapsedMs: 180000,
  };
  return {
    timeZone: "America/Los_Angeles",
    assessment,
    conversion: conversionRan
      ? {
          status: "completed",
          startedAt: "2026-06-10T23:13:00.000Z",
          completedAt: "2026-06-11T01:07:30.000Z",
          wallClockElapsedMs: 6870000,
        }
      : { status: "not-run" },
    total: conversionRan
      ? {
          status: "completed",
          startedAt: "2026-06-10T23:10:00.000Z",
          completedAt: "2026-06-11T01:07:30.000Z",
          wallClockElapsedMs: 7050000,
        }
      : { ...assessment },
  };
}

function conversionStatePath(root) {
  return path.join(root, ".github", "migrate-oracle-app", "conversions", "conversion-state.json");
}

function runRuntimeValidator(root, { record = false, requireRuntime = true } = {}) {
  return spawnSync(process.execPath, [
    validator, root, "conversion",
    "--runtime-directory", path.join(root, ".private-runtime"),
    ...(requireRuntime ? ["--require-runtime"] : []),
    ...(record ? ["--record"] : []),
  ], { encoding: "utf8" });
}

async function finalizeRuntimeFixture(
  root,
  { phases = ["conversion"], runtimeDirectory = path.join(root, ".private-runtime") } = {},
) {
  const runtime = await openMigrationRuntime({
    root, runId: "artifact-test-run", actorId: "primary",
    runtimeDirectory: runtimeDirectory ?? undefined, initialize: true,
  });
  const templates = {};
  const expectedArtifacts = {};
  await withPrimaryExecution(runtime, "test-final-assembly", async () => {
    for (const phase of phases) {
      const folder = phase === "assessment" ? "assessment" : "conversions";
      const stateName = phase === "assessment" ? "assessment-data.json" : "conversion-state.json";
      const reportName = phase === "assessment" ? "assessment-report.md" : "conversion-report.md";
      const relativeState = path.join(".github", "migrate-oracle-app", folder, stateName);
      const relativeReport = path.join(".github", "migrate-oracle-app", folder, reportName);
      const stateText = await readFile(path.join(root, relativeState), "utf8");
      const report = await readFile(path.join(root, relativeReport), "utf8");
      const state = JSON.parse(stateText);
      const anchors = [...report.matchAll(/^<a id="([^"]+)"><\/a>\r?\n## ([^\r\n]+)\r?$/gm)];
      assert.ok(anchors.length > 0);
      const sections = Object.fromEntries(anchors.map((anchor, index) => {
        let body = report.slice(
          anchor.index + anchor[0].length, anchors[index + 1]?.index ?? report.length,
        ).replace(/^\[Back to Top\]\(#top\)[ \t]*(?:\r?\n|$)/gm, "").trim();
        if (anchor[2].includes("Appendix")) {
          body = body.replace(renderRunMetrics(state.runMetrics), "").trim();
        }
        return [anchor[1], body];
      }));
      await checkpointMigration(runtime, {
        phase,
        operations: Object.entries(state).map(([key, value]) => ({ op: "set", path: [key], value })),
        sections,
      });
      templates[phase] = path.join(runtime.runDirectory, "templates", `${phase}.md`);
      await mkdir(path.dirname(templates[phase]), { recursive: true });
      await writeFile(templates[phase], report);
      expectedArtifacts[relativeState] = sha256(stateText);
      expectedArtifacts[relativeReport] = sha256(report);
    }
    await finalizeMigrationArtifacts(runtime, { phases, templates, expectedArtifacts });
  });
  return runtime;
}

async function createNoConversionFixture({ preexistingTarget = false } = {}) {
  const root = await mkdtemp(path.join(process.cwd(), ".no-conversion-test-"));
  await mkdir(path.join(root, "src"), { recursive: true });
  await mkdir(path.join(root, "lib"), { recursive: true });
  await writeFile(
    path.join(root, "package.json"),
    JSON.stringify({ dependencies: { "@angular/core": "18.0.0" } }),
  );
  await writeFile(path.join(root, ".gitignore"), "target/\nnode_modules/\n");
  await writeFile(
    path.join(root, "src", "app.js"),
    "import { Component } from '@angular/core'; export class AppComponent {}",
  );
  await writeFile(path.join(root, "lib", "ojdbc8.jar"), "placeholder");
  if (preexistingTarget) {
    const targetFile = path.join(root, "target", "classes", "App.class");
    await mkdir(path.dirname(targetFile), { recursive: true });
    await writeFile(targetFile, "baseline-compiled-output");
  }
  execFileSync("git", ["init"], { cwd: root, stdio: "ignore" });
  execFileSync("git", ["config", "user.email", "test@example.invalid"], {
    cwd: root,
  });
  execFileSync("git", ["config", "user.name", "Scope Test"], { cwd: root });
  execFileSync("git", ["add", "."], { cwd: root });
  execFileSync("git", ["commit", "-m", "baseline"], {
    cwd: root,
    stdio: "ignore",
  });

  const assessment = path.join(
    root,
    ".github",
    "migrate-oracle-app",
    "assessment",
  );
  const conversion = path.join(
    root,
    ".github",
    "migrate-oracle-app",
    "conversions",
  );
  await mkdir(assessment, { recursive: true });
  await mkdir(conversion, { recursive: true });
  const scopeDecision = await detectMigrationScope(root);
  await writeFile(
    path.join(assessment, "assessment-report.md"),
    noConversionAssessmentReport(),
  );
  await writeFile(
    path.join(assessment, "assessment-data.json"),
    JSON.stringify({
      schemaVersion: 1,
      status: "NO_CONVERSION_REQUIRED",
      codingNotes: omittedCodingNotes(),
      runMetrics: runMetricsEvidence(false),
      scopeDecision,
      fileInventory: {
        noModifiedTrackedFilesReason:
          "No application conversion required; only skill-owned reports were generated.",
      },
      validation: {},
    }),
  );
  await writeFile(
    path.join(conversion, "conversion-report.md"),
    noConversionReport(),
  );
  await writeFile(
    path.join(conversion, "conversion-state.json"),
    JSON.stringify({
      schemaVersion: 1,
      status: "NO_CONVERSION_REQUIRED",
      codingNotes: omittedCodingNotes(),
      runMetrics: runMetricsEvidence(false),
      scopeDecision,
      fileInventory: {
        noModifiedTrackedFilesReason:
          "No application conversion required; no application files changed.",
      },
      validation: {},
    }),
  );
  return root;
}

async function createScopeReviewFixture() {
  const root = await mkdtemp(path.join(process.cwd(), ".scope-review-test-"));
  await mkdir(path.join(root, "forms"), { recursive: true });
  await writeFile(
    path.join(root, "package.json"),
    JSON.stringify({ dependencies: { react: "19.0.0" } }),
  );
  await writeFile(path.join(root, "forms", "login.fmb"), "binary-placeholder");
  execFileSync("git", ["init"], { cwd: root, stdio: "ignore" });
  execFileSync("git", ["config", "user.email", "test@example.invalid"], {
    cwd: root,
  });
  execFileSync("git", ["config", "user.name", "Scope Review Test"], {
    cwd: root,
  });
  execFileSync("git", ["add", "."], { cwd: root });
  execFileSync("git", ["commit", "-m", "baseline"], {
    cwd: root,
    stdio: "ignore",
  });
  const scopeDecision = await detectMigrationScope(root);
  const assessment = path.join(
    root,
    ".github",
    "migrate-oracle-app",
    "assessment",
  );
  const conversion = path.join(
    root,
    ".github",
    "migrate-oracle-app",
    "conversions",
  );
  await mkdir(assessment, { recursive: true });
  await mkdir(conversion, { recursive: true });
  await writeFile(
    path.join(assessment, "assessment-report.md"),
    scopeReviewMarkdown(
      "Oracle to PostgreSQL Application Code Assessment",
      "Assessment Outcome",
    ),
  );
  await writeFile(
    path.join(assessment, "assessment-data.json"),
    JSON.stringify({
      schemaVersion: 1,
      status: "SCOPE_REVIEW_REQUIRED",
      codingNotes: omittedCodingNotes(),
      runMetrics: runMetricsEvidence(false),
      scopeDecision,
      fileInventory: {
        noModifiedTrackedFilesReason:
          "Scope review required; no application files changed.",
      },
      validation: {},
    }),
  );
  await writeFile(
    path.join(conversion, "conversion-report.md"),
    scopeReviewMarkdown(
      "Oracle to PostgreSQL Application Code Conversion",
      "Conversion Outcome",
    ),
  );
  await writeFile(
    path.join(conversion, "conversion-state.json"),
    JSON.stringify({
      schemaVersion: 1,
      status: "SCOPE_REVIEW_REQUIRED",
      codingNotes: omittedCodingNotes(),
      runMetrics: runMetricsEvidence(false),
      scopeDecision,
      fileInventory: {
        noModifiedTrackedFilesReason:
          "Scope review required; no application files changed.",
      },
      validation: {},
    }),
  );
  return root;
}

function scopeReviewMarkdown(title, outcomeHeading) {
  const sections = [
    ["executive-summary", title.includes("Assessment") ? "Executive Summary" : "Executive Conversion Summary"],
    ["scope-review-required", "Scope Review Required"],
    ["validation-results", "Validation Results"],
    ["outcome", outcomeHeading],
    ["coding-notes-processing", "Coding Notes Processing"],
    [
      "appendix",
      title.includes("Assessment") ? "Appendix / Run Evidence" : "Appendix / Execution Evidence",
    ],
  ];
  return `<a id="top"></a>
# ${title}

## Table of Contents
${sections.map(([anchor, heading]) => `- [${heading}](#${anchor})`).join("\n")}

${sections
  .map(
    ([anchor, heading]) => heading === "Coding Notes Processing"
      ? `<a id="${anchor}"></a>\n## ${heading}\n\n${renderCodingNotes(omittedCodingNotes())}\n\n[Back to Top](#top)`
      : `<a id="${anchor}"></a>
## ${heading}

Scope review required.

| Technology | Evidence |
|---|---|
| React | package.json |
| Node.js | package.json |

| Path | Reason |
|---|---|
| forms/login.fmb | Unknown non-inert file extension requires manual scope review. |

| Gate | Result |
|---|---|
| Application conversion | Not applicable until scope is resolved |
| Build / tests / coverage | Not run by design |

${heading.startsWith("Appendix") ? `${renderRunMetrics(runMetricsEvidence(false))}\n\n` : ""}[Back to Top](#top)`,
  )
  .join("\n\n")}
`;
}

function noConversionAssessmentReport() {
  return noConversionMarkdown(
    "Oracle to PostgreSQL Application Code Assessment",
    [
      ["executive-summary", "Executive Summary"],
      ["application-technology", "Application Technology"],
      ["database-dependency-scan", "Database Dependency Scan"],
      ["assessment-outcome", "Assessment Outcome"],
      ["coding-notes-processing", "Coding Notes Processing"],
      ["appendix-run-evidence", "Appendix / Run Evidence"],
    ],
  );
}

function noConversionReport() {
  return noConversionMarkdown(
    "Oracle to PostgreSQL Application Code Conversion",
    [
      ["executive-conversion-summary", "Executive Conversion Summary"],
      ["scope-decision", "Scope Decision"],
      ["validation-results", "Validation Results"],
      ["conversion-outcome", "Conversion Outcome"],
      ["coding-notes-processing", "Coding Notes Processing"],
      ["appendix-execution-evidence", "Appendix / Execution Evidence"],
    ],
  );
}

function noConversionMarkdown(title, sections) {
  const body = sections
    .map(
      ([anchor, heading]) => heading === "Coding Notes Processing"
        ? `<a id="${anchor}"></a>\n## ${heading}\n\n${renderCodingNotes(omittedCodingNotes())}\n\n[Back to Top](#top)`
        : `<a id="${anchor}"></a>
## ${heading}

No application conversion required.

| Technology | Evidence |
|---|---|
| Angular | package.json; src/app.js |
| Node.js | package.json |

| Category | Result |
|---|---|
| PostgreSQL DDL | Not applicable |
| Database integration tests | Not applicable |
| Dependency installation | Not run by design |
| Build / unit tests / coverage | Not run by design |
| Source changes | None |

| Passive artifact | Kind | Action |
|---|---|---|
| lib/ojdbc8.jar | jar | Retained |

${heading.startsWith("Appendix") ? `${renderRunMetrics(runMetricsEvidence(false))}\n\n` : ""}[Back to Top](#top)`,
    )
    .join("\n\n");
  return `<a id="top"></a>
# ${title}

## Table of Contents

${sections.map(([anchor, heading]) => `- [${heading}](#${anchor})`).join("\n")}

${body}
`;
}

async function createFixture({
  baselineTests = 10,
  finalTests = 12,
  includeMatrix = true,
  includeReconciliation = true,
  includeVintageEngine = true,
  omitVintageClassFromReports = false,
  exposedFailures = 0,
} = {}) {
  const root = await mkdtemp(path.join(process.cwd(), ".migration-artifact-test-"));
  const assessment = path.join(root, ".github", "migrate-oracle-app", "assessment");
  const conversion = path.join(root, ".github", "migrate-oracle-app", "conversions");
  const testSources = path.join(root, "src", "test", "java", "com", "example");
  await mkdir(assessment, { recursive: true });
  await mkdir(conversion, { recursive: true });
  await mkdir(testSources, { recursive: true });
  await writeFile(
    path.join(testSources, "LegacyTest.java"),
    javaTestSource("org.junit.Test", 4, "LegacyTest"),
  );
  await writeFile(
    path.join(testSources, "CurrentTest.java"),
    javaTestSource("org.junit.jupiter.api.Test", 6, "CurrentTest"),
  );
  await writeFile(
    path.join(testSources, "DisabledLegacyTest.java"),
    `/* package com.example;
import org.junit.Test;
public class DisabledLegacyTest {
  @Test public void disabled() {}
} */`,
  );

  await writeFile(
    path.join(assessment, "assessment-data.json"),
    JSON.stringify({
      runMetrics: runMetricsEvidence(),
      codingNotes: omittedCodingNotes(),
      baselineEnablement: { enabledBaseline: { testsRun: baselineTests } },
      postgresProvider: postgresProviderEvidence(),
      targetMetadataAcquisition: targetMetadataAcquisitionEvidence(),
      oracleDdlSource: oracleDdlSourceEvidence(),
    }),
  );
  const baseline = discoveryBaseline(includeVintageEngine);
  await writeFile(
    path.join(assessment, "test-discovery-baseline.json"),
    JSON.stringify(baseline),
  );
  await writeFile(
    path.join(conversion, "conversion-report.md"),
    conversionReport(finalTests),
  );
  await writeFile(path.join(conversion, "file-diffs.md"), diffReport());
  const vintageTests = Math.min(5, finalTests);
  const jupiterTests = Math.max(0, finalTests - vintageTests);
  const reportFiles = [
    "target/surefire-reports/TEST-com.example.LegacyTest.xml",
    "target/surefire-reports/TEST-com.example.CurrentTest.xml",
  ];
  await mkdir(path.join(root, "target", "surefire-reports"), { recursive: true });
  await writeFile(
    path.join(root, reportFiles[0]),
    testReport(
      "com.example.LegacyTest",
      omitVintageClassFromReports ? 0 : vintageTests,
    ),
  );
  await writeFile(
    path.join(root, reportFiles[1]),
    testReport("com.example.CurrentTest", jupiterTests),
  );
  const coverageLanes = [
    "junit-vintage",
    "junit-jupiter",
    "integration",
    "combined",
  ].map((id) => ({
    id,
    status: "passed",
    executionDataPath: `target/coverage-reports/${id}.exec`,
    reportPath: `target/coverage-reports/${id}/jacoco.xml`,
    generatedAt: "2025-01-01T00:05:00.000Z",
  }));
  for (const lane of coverageLanes) {
    await mkdir(path.dirname(path.join(root, lane.reportPath)), {
      recursive: true,
    });
    await writeFile(path.join(root, lane.executionDataPath), lane.id);
    await writeFile(path.join(root, lane.reportPath), "<report />");
  }
  await writeFile(
    path.join(conversion, "conversion-state.json"),
    JSON.stringify({
      runMetrics: runMetricsEvidence(),
      codingNotes: omittedCodingNotes(),
      postgresProvider: postgresProviderEvidence(),
      targetMetadataAcquisition: targetMetadataAcquisitionEvidence(),
      oracleDdlSource: oracleDdlSourceEvidence(),
      assessmentBaseline: {
        unitTests: baselineTests,
        noTestsReason: null,
      },
      options: {
        "establish-baseline": true,
        "fix-broken-tests": true,
        "create-persistent-ddl": false,
        "create-unit-tests": true,
        "create-integration-tests": true,
        "start-docker-desktop": false,
        "remove-unused-artifacts": false,
        "test-target-writes": false,
      },
      optionResults: {
        "create-persistent-ddl": {
          requested: false,
          status: "not-requested",
          optionSatisfied: true,
          applicableMissingObjects: 0,
          reason: "Option was off.",
          nextAction: null,
        },
        "test-target-writes": {
          requested: false,
          status: "not-requested",
          optionSatisfied: true,
          applicableMutationScenarios: 0,
          reason: "Option was off.",
          nextAction: null,
        },
      },
      executiveSummary: executiveSummaryEvidence(finalTests),
      orchestration: orchestrationEvidence(),
      performanceExecution: performanceEvidence(),
      validation: {
        unitTests: { tests: finalTests },
        ...(includeReconciliation
          ? {
              testReconciliation: {
                baseline: baselineTests,
                final: finalTests,
                missing: [],
                explanation: "All baseline tests were retained.",
              },
            }
          : {}),
        dockerPostgreSqlRuntime: { status: "passed" },
        dockerEngineAvailability: {
          optionEnabled: false,
          engineRunningAtPreflight: true,
          desktopDetected: null,
          startAttempted: false,
          desktopStateChanged: false,
          startupCommand: null,
          engineResponsive: true,
          status: "already-running",
          reason: "Docker engine was already running.",
        },
        unusedArtifactDisposition: {
          optionEnabled: false,
          candidates: [
            {
              path: "lib/DealOutlookUI.jar",
              kind: "jar",
              classification: "unused",
              beforeDigest: "artifact-digest",
              unusedEvidence: [
                "No active source references.",
                "Not required by runtime package.",
              ],
              activeReferences: 0,
              requiredByBuild: false,
              requiredByPackage: false,
              action: "retained",
              removed: false,
              rollback: null,
              postRemovalValidation: null,
            },
          ],
          deletedPaths: [],
        },
        runtimeDriverPackaging: {
          required: false,
          status: "not-applicable",
          planPath: null,
          descriptorSourcePaths: [],
          archivesInspected: 0,
          warOnlyDriverDetected: false,
          reason: "No PostgreSQL runtime descriptor was detected.",
        },
        targetWriteTests: {
          requested: false,
          status: "not-requested",
          optionSatisfied: true,
          applicableMutationScenarios: 0,
          writeAttempted: false,
          mcpModificationScenarios: 0,
          markerRowsInserted: 0,
          markerRowsDeleted: 0,
          remainingMarkerRows: 0,
          customerRowsUpdated: 0,
          customerRowsDeleted: 0,
          sequenceValuesConsumed: [],
          mcpReads: 0,
          mcpWrites: 0,
          reason: "Option was off.",
          nextAction: null,
        },
        testDiscovery: {
          status: "passed",
          baselinePath:
            ".github/migrate-oracle-app/assessment/test-discovery-baseline.json",
          baselineSha256: sha256(JSON.stringify(baseline)),
          fullSuite: {
            command: "mvn test",
            startedAt: "2025-01-01T00:00:00.000Z",
            completedAt: "2025-01-01T00:01:00.000Z",
            reportFiles,
            tests: finalTests,
            failures: 0,
            errors: 0,
            skipped: 0,
          },
          frameworks: [
            {
              id: "junit-vintage",
              engineAvailable: includeVintageEngine,
              engineVersion: "5.12.2",
              sourceFiles: 1,
              testMethods: 4,
              eligibleClasses: 1,
              executedClasses: omitVintageClassFromReports ? 0 : 1,
              executedTests: omitVintageClassFromReports ? 0 : vintageTests,
              representativeClassesExecuted: omitVintageClassFromReports
                ? []
                : ["com.example.LegacyTest"],
              missingClasses: omitVintageClassFromReports
                ? ["com.example.LegacyTest"]
                : [],
            },
            {
              id: "junit-jupiter",
              engineAvailable: true,
              engineVersion: "5.12.2",
              sourceFiles: 1,
              testMethods: 6,
              eligibleClasses: 1,
              executedClasses: 1,
              executedTests: jupiterTests,
              representativeClassesExecuted: ["com.example.CurrentTest"],
              missingClasses: [],
            },
          ],
          runnerPolicy: runnerPolicy(),
          failureRepair: {
            fixBrokenTestsEnabled: true,
            exposedFailures,
            exposedErrors: 0,
            exposedFailureClasses:
              exposedFailures > 0 ? ["com.example.BrokenTest"] : [],
            repairedTests: [],
            repairAttempts: [],
            unresolvedFailureClasses:
              exposedFailures > 0 ? ["com.example.BrokenTest"] : [],
            remainingFailures: 0,
            remainingErrors: 0,
            status: "passed",
          },
          coverageEligibility: {
            discoveryReconciled: true,
            reconciledAt: "2025-01-01T00:02:00.000Z",
            coverageGeneratedAfterReconciliation: true,
            jacocoFresh: true,
          },
        },
      },
      coverage: {
        lanes: coverageLanes,
        wavePolicy: {
          unitMinimum: 2,
          integrationMinimum: 2,
          recommended: 2,
          maximum: 10,
          aspirationalCombinedLinePercentage: 80,
          readinessGate: false,
        },
        baseline: {
          combined: {
            line: { covered: 2, total: 10 },
            branch: { covered: 0, total: 2 },
          },
        },
        final: {
          combined: {
            line: { covered: 3, total: 10 },
            branch: { covered: 1, total: 2 },
          },
        },
        unitWaves: waveEvidence("unit"),
        integrationWaves: waveEvidence("integration"),
        stopReason: "Remaining items require unavailable external systems.",
        rankedBacklogFinal: [
          {
            area: "External runtime",
            classification: "external-system",
            safelyTestable: false,
            nextAction: "Run in the configured environment.",
          },
        ],
      },
      fixtures: {
        ledger: [{ table: "app.t", remaining: 0 }],
        databaseObjectTestMatrix: includeMatrix
          ? [
              { kind: "function", active: 0, exercised: 0 },
              { kind: "procedure", active: 0, exercised: 0 },
              { kind: "sequence", active: 0, exercised: 0 },
              { kind: "table", active: 1, exercised: 1 },
              { kind: "view", active: 0, exercised: 0 },
              { kind: "trigger", active: 0, exercised: 0 },
              { kind: "native-query-path", active: 1, exercised: 1 },
            ]
          : [],
      },
      conversionScope: {
        manifestDigest: "scope-digest",
        categories: [
          {
            category: "runtime-datasource",
            discovered: 1,
            converted: 1,
            retained: 0,
            deferred: 0,
            safeConvertibleRemaining: 0,
            evidence: "Selected datasource converted.",
          },
        ],
        findings: [
          {
            id: "runtime-datasource:app",
            path: "app.config",
            category: "runtime-datasource",
            status: "converted",
            safeToConvert: true,
            reason: "PostgreSQL binding configured.",
          },
        ],
      },
    }),
  );
  return root;
}

async function createLanguageFixture(adapter) {
  const root = await createFixture({ baselineTests: 2, finalTests: 2 });
  await rm(path.join(root, "src"), { recursive: true, force: true });
  await rm(path.join(root, "tests"), { recursive: true, force: true });
  await rm(path.join(root, "target"), { recursive: true, force: true });

  const spec = languageSpec(adapter);
  for (const source of spec.sources) {
    const absolute = path.join(root, source.path);
    await mkdir(path.dirname(absolute), { recursive: true });
    await writeFile(absolute, source.content);
  }
  for (const report of spec.reports) {
    const absolute = path.join(root, report.path);
    await mkdir(path.dirname(absolute), { recursive: true });
    await writeFile(absolute, report.content);
  }

  const assessment = path.join(
    root,
    ".github",
    "migrate-oracle-app",
    "assessment",
  );
  const conversion = path.join(
    root,
    ".github",
    "migrate-oracle-app",
    "conversions",
  );
  await writeFile(
    path.join(assessment, "test-discovery-baseline.json"),
    JSON.stringify(spec.baseline),
  );

  const coverageLanes = [
    spec.framework.id,
    "integration",
    "combined",
  ].map((id) => ({
    id,
    status: "passed",
    executionDataPath: `artifacts/coverage/${id}.data`,
    reportPath: `artifacts/coverage/${id}/coverage.xml`,
    generatedAt: "2025-01-01T00:05:00.000Z",
  }));
  for (const lane of coverageLanes) {
    await mkdir(path.dirname(path.join(root, lane.reportPath)), {
      recursive: true,
    });
    await writeFile(path.join(root, lane.executionDataPath), lane.id);
    await writeFile(path.join(root, lane.reportPath), "<coverage />");
  }

  const statePath = path.join(conversion, "conversion-state.json");
  const state = JSON.parse(await readFile(statePath, "utf8"));
  state.assessmentBaseline.unitTests = 2;
  state.validation.unitTests.tests = 2;
  state.validation.testReconciliation = {
    baseline: 2,
    final: 2,
    missing: [],
    explanation: "All reviewed tests executed.",
  };
  state.validation.testDiscovery = {
    status: "passed",
    baselinePath:
      ".github/migrate-oracle-app/assessment/test-discovery-baseline.json",
    baselineSha256: sha256(JSON.stringify(spec.baseline)),
    fullSuite: {
      command: spec.command,
      startedAt: "2025-01-01T00:00:00.000Z",
      completedAt: "2025-01-01T00:01:00.000Z",
      reportFiles: spec.reports.map((report) => ({
        path: report.path,
        format: report.format,
      })),
      tests: 2,
      failures: 0,
      errors: 0,
      skipped: 0,
    },
    frameworks: [
      {
        id: spec.framework.id,
        adapterAvailable: true,
        adapterVersion: "1.0",
        sourceFiles: 1,
        testCases: 2,
        eligibleContainers: spec.framework.containers.length,
        executedContainers: spec.framework.containers.length,
        executedTests: 2,
        representativeContainersExecuted: [
          spec.framework.containers[0],
        ],
        missingContainers: [],
      },
    ],
    runnerPolicy: runnerPolicy(),
    failureRepair: {
      fixBrokenTestsEnabled: true,
      exposedFailures: 0,
      exposedErrors: 0,
      exposedFailureClasses: [],
      repairedTests: [],
      repairAttempts: [],
      unresolvedFailureClasses: [],
      remainingFailures: 0,
      remainingErrors: 0,
      status: "passed",
    },
    coverageEligibility: {
      discoveryReconciled: true,
      reconciledAt: "2025-01-01T00:02:00.000Z",
      coverageGeneratedAfterReconciliation: true,
      coverageArtifactsFresh: true,
    },
  };
  state.coverage.lanes = coverageLanes;
  await writeFile(statePath, JSON.stringify(state));
  await writeFile(
    path.join(conversion, "conversion-report.md"),
    conversionReport(
      2,
      `| ${spec.framework.language} | ${spec.framework.id} | 1 | 2 | 1.0 | ${spec.framework.containers.length} | ${spec.framework.containers.length} | 2 | 0 | ✅ Passed |`,
      spec.framework.id,
    ),
  );
  return root;
}

async function setTargetWriteFixture(root, targetStatus, conversionStatus) {
  const conversion = path.join(
    root,
    ".github",
    "migrate-oracle-app",
    "conversions",
  );
  const statePath = path.join(conversion, "conversion-state.json");
  const reportPath = path.join(conversion, "conversion-report.md");
  const state = JSON.parse(await readFile(statePath, "utf8"));
  state.status = conversionStatus;
  state.options["test-target-writes"] = true;
  if (targetStatus === "passed") {
    state.optionResults["test-target-writes"] = {
      requested: true,
      status: "completed",
      optionSatisfied: true,
      applicableMutationScenarios: 1,
      reason: null,
      nextAction: null,
    };
    state.validation.targetWriteTests = {
      requested: true,
      status: "passed",
      optionSatisfied: true,
      applicableMutationScenarios: 1,
      writeAttempted: true,
      contextRefreshedAt: "2025-01-01T00:03:00.000Z",
      savedProfile: true,
      profileAccessMode: "rw",
      modifyToolAvailable: true,
      defaultTransactionReadOnly: false,
      inspectionTransactionReadOnly: true,
      serverInRecovery: false,
      safetyChecks: {
        identity: true,
        role: true,
        privileges: true,
        contracts: true,
        locks: true,
      },
      dockerPrerequisitesPassed: true,
      mcpModificationScenarios: 1,
      markerRowsInserted: 1,
      markerRowsDeleted: 1,
      remainingMarkerRows: 0,
      customerRowsUpdated: 0,
      customerRowsDeleted: 0,
      sequenceValuesConsumed: [7001],
      mcpReads: 12,
      mcpWrites: 3,
      reason: null,
      nextAction: null,
    };
  } else {
    state.optionResults["test-target-writes"] = {
      requested: true,
      status: "not-executed",
      optionSatisfied: false,
      applicableMutationScenarios: 1,
      reason: "Selected saved profile is transaction read-only.",
      nextAction: "Provide a write-enabled saved profile for the same target.",
    };
    state.validation.targetWriteTests = {
      requested: true,
      status: "not-executed",
      optionSatisfied: false,
      applicableMutationScenarios: 1,
      writeAttempted: false,
      mcpModificationScenarios: 0,
      markerRowsInserted: 0,
      markerRowsDeleted: 0,
      remainingMarkerRows: 0,
      customerRowsUpdated: 0,
      customerRowsDeleted: 0,
      sequenceValuesConsumed: [],
      mcpReads: 12,
      mcpWrites: 0,
      reason: "Selected saved profile is transaction read-only.",
      nextAction: "Provide a write-enabled saved profile for the same target.",
    };
  }
  await writeFile(statePath, JSON.stringify(state));
  const report = await readFile(reportPath, "utf8");
  const resultRow =
    targetStatus === "passed"
      ? "| Yes | 1 | Saved rw profile; transaction_read_only=off | Yes | 1 | 1 / 1 / 0 | 1 | 3 | Yes | ✅ Passed |"
      : "| Yes | 1 | transaction_read_only=on | No | 0 | 0 / 0 / 0 | 0 | 0 | No | ⚠️ Not executed |";
  await writeFile(
    reportPath,
    report.replace(
      "| No | 0 | Not requested | No | 0 | 0 / 0 / 0 | 0 | 0 | Yes | ➖ Not requested |",
      resultRow,
    ),
  );
}

function languageSpec(adapter) {
  const base = {
    schemaVersion: 2,
    capturedAt: "2025-01-01T00:00:00.000Z",
    runnerPolicy: runnerPolicy(),
    intentionalExclusions: [],
    review: {
      status: "approved",
      reviewedAt: "2025-01-01T00:02:00.000Z",
      reason: "Complete passing reconciled suite.",
    },
  };
  if (adapter === "dotnet") {
    const framework = {
      id: "dotnet-xunit",
      sourceFrameworkId: "dotnet-xunit",
      language: "C#",
      sourceFramework: "xUnit",
      requiredAdapter: "xunit.runner.visualstudio",
      adapterVersion: "1.0",
      adapterAvailable: true,
      reportFormat: "trx",
      sourceFiles: 1,
      testCases: 2,
      eligibleContainers: ["Example.Tests.CurrentTests"],
      representativeContainers: ["Example.Tests.CurrentTests"],
      minimumExecutedContainers: 1,
      minimumExecutedTests: 2,
    };
    return {
      framework: { ...framework, containers: framework.eligibleContainers },
      command: "dotnet test",
      sources: [
        {
          path: "tests/Example.Tests/CurrentTests.cs",
          content: `using Xunit;
namespace Example.Tests;
public class CurrentTests {
  [Fact] public void Works() {}
  [Theory] public void HandlesNull() {}
}`,
        },
      ],
      reports: [
        {
          path: "TestResults/complete.trx",
          format: "trx",
          content: trxReport("Example.Tests.CurrentTests", [
            "Works",
            "HandlesNull",
          ]),
        },
      ],
      baseline: {
        ...base,
        buildSystems: ["dotnet"],
        frameworks: [framework],
        adapterEvidence: {
          commands: ["dotnet list package", "dotnet test --list-tests"],
          availableAdapters: ["xunit.runner.visualstudio:1.0"],
        },
      },
    };
  }
  if (adapter === "python") {
    const framework = {
      id: "python-pytest",
      sourceFrameworkId: "python-pytest",
      language: "Python",
      sourceFramework: "pytest",
      requiredAdapter: "pytest",
      adapterVersion: "1.0",
      adapterAvailable: true,
      reportFormat: "junit-xml",
      sourceFiles: 1,
      testCases: 2,
      eligibleContainers: ["tests.test_current"],
      representativeContainers: ["tests.test_current"],
      minimumExecutedContainers: 1,
      minimumExecutedTests: 2,
    };
    return {
      framework: { ...framework, containers: framework.eligibleContainers },
      command: "pytest --junitxml=artifacts/pytest.xml",
      sources: [
        {
          path: "tests/test_current.py",
          content: `def test_works():\n    assert True\n\ndef test_handles_null():\n    assert True\n`,
        },
      ],
      reports: [
        {
          path: "artifacts/pytest.xml",
          format: "junit-xml",
          content: testReport("tests.test_current", 2),
        },
      ],
      baseline: {
        ...base,
        buildSystems: ["python"],
        frameworks: [framework],
        adapterEvidence: {
          commands: ["pytest --collect-only"],
          availableAdapters: ["pytest:1.0"],
        },
      },
    };
  }
  if (adapter === "cpp") {
    const framework = {
      id: "cpp-gtest",
      sourceFrameworkId: "cpp-gtest",
      language: "C++",
      sourceFramework: "GoogleTest",
      requiredAdapter: "GoogleTest+CTest",
      adapterVersion: "1.0",
      adapterAvailable: true,
      reportFormat: "junit-xml",
      sourceFiles: 1,
      testCases: 2,
      eligibleContainers: ["Widget.Works", "Widget.HandlesNull"],
      representativeContainers: ["Widget.Works"],
      minimumExecutedContainers: 2,
      minimumExecutedTests: 2,
    };
    return {
      framework: { ...framework, containers: framework.eligibleContainers },
      command: "ctest --output-junit artifacts/ctest.xml",
      sources: [
        {
          path: "tests/current_test.cpp",
          content: `TEST(Widget, Works) {}\nTEST(Widget, HandlesNull) {}\n`,
        },
      ],
      reports: [
        {
          path: "artifacts/ctest.xml",
          format: "junit-xml",
          content: namedTestReport("Widget", ["Works", "HandlesNull"]),
        },
      ],
      baseline: {
        ...base,
        buildSystems: ["cmake"],
        frameworks: [framework],
        adapterEvidence: {
          commands: ["ctest --show-only=json-v1"],
          availableAdapters: ["GoogleTest+CTest:1.0"],
        },
      },
    };
  }
  const framework = {
    id: "c-cmocka",
    sourceFrameworkId: "c-cmocka",
    language: "C",
    sourceFramework: "CMocka",
    requiredAdapter: "CMocka+CTest",
    adapterVersion: "1.0",
    adapterAvailable: true,
    reportFormat: "junit-xml",
    sourceFiles: 1,
    testCases: 2,
    eligibleContainers: ["test_works", "test_handles_null"],
    representativeContainers: ["test_works"],
    minimumExecutedContainers: 2,
    minimumExecutedTests: 2,
  };
  return {
    framework: { ...framework, containers: framework.eligibleContainers },
    command: "ctest --output-junit artifacts/ctest.xml",
    sources: [
      {
        path: "tests/current_test.c",
        content: `cmocka_unit_test(test_works),\ncmocka_unit_test(test_handles_null),\n`,
      },
    ],
    reports: [
      {
        path: "artifacts/ctest.xml",
        format: "junit-xml",
        content: namedTestReport("cmocka", [
          "test_works",
          "test_handles_null",
        ]),
      },
    ],
    baseline: {
      ...base,
      buildSystems: ["cmake"],
      frameworks: [framework],
      adapterEvidence: {
        commands: ["ctest --show-only=json-v1"],
        availableAdapters: ["CMocka+CTest:1.0"],
      },
    },
  };
}

function discoveryBaseline(includeVintageEngine) {
  const vintageCoordinate = "org.junit.vintage:junit-vintage-engine";
  return {
    schemaVersion: 1,
    capturedAt: "2025-01-01T00:00:00.000Z",
    buildSystem: "maven",
    frameworks: [
      {
        id: "junit-vintage",
        sourceFrameworkId: "junit4",
        sourceFramework: "JUnit 4",
        requiredEngine: vintageCoordinate,
        engineVersion: includeVintageEngine ? "5.12.2" : null,
        engineAvailable: includeVintageEngine,
        sourceFiles: 1,
        testMethods: 4,
        eligibleClasses: ["com.example.LegacyTest"],
        representativeClasses: ["com.example.LegacyTest"],
        minimumExecutedClasses: 1,
        minimumExecutedTests: 4,
      },
      {
        id: "junit-jupiter",
        sourceFrameworkId: "junit5",
        sourceFramework: "JUnit Jupiter",
        requiredEngine: "org.junit.jupiter:junit-jupiter-engine",
        engineVersion: "5.12.2",
        engineAvailable: true,
        sourceFiles: 1,
        testMethods: 6,
        eligibleClasses: ["com.example.CurrentTest"],
        representativeClasses: ["com.example.CurrentTest"],
        minimumExecutedClasses: 1,
        minimumExecutedTests: 6,
      },
    ],
    dependencyEvidence: {
      command: "mvn -DincludeScope=test dependency:tree",
      effectiveEngines: [
        ...(includeVintageEngine ? [`${vintageCoordinate}:5.12.2`] : []),
        "org.junit.jupiter:junit-jupiter-engine:5.12.2",
      ],
    },
    runnerPolicy: runnerPolicy(),
    intentionalExclusions: [],
    review: {
      status: "approved",
      reviewedAt: "2025-01-01T00:02:00.000Z",
      reason: "Complete passing reconciled suite.",
    },
  };
}

function runnerPolicy() {
  return {
    failIfNoTests: true,
    failIfNoSpecifiedTests: true,
    skipTests: false,
    testFailureIgnore: false,
    newExclusions: [],
    unexplainedDisabledTests: [],
  };
}

function orchestrationEvidence() {
  return {
    agentFacilityAvailable: true,
    eligibleIndependentUnits: [
      "source-oracle-inventory",
      "test-build-inventory",
    ],
    parallelRequired: true,
    fallbackReason: null,
    sharedPrimaryOnlyPaths: [".github/migrate-oracle-app/**"],
    assignments: [
      {
        id: "agent-source",
        type: "explore",
        objective: "Inventory active Oracle source findings.",
        mode: "read-only",
        unitIds: ["source-oracle-inventory"],
        ownedPaths: [],
        prohibitedPaths: [".github/migrate-oracle-app/**"],
        databaseMutationAllowed: false,
        startedAt: "2025-01-01T00:00:00.000Z",
        completedAt: "2025-01-01T00:00:10.000Z",
        status: "completed",
        resultSummary: "Returned source findings.",
        changedPaths: [],
        commandsExecuted: 0,
        testRuns: 0,
      },
      {
        id: "agent-tests",
        type: "explore",
        objective: "Inventory test and build evidence.",
        mode: "read-only",
        unitIds: ["test-build-inventory"],
        ownedPaths: [],
        prohibitedPaths: [".github/migrate-oracle-app/**"],
        databaseMutationAllowed: false,
        startedAt: "2025-01-01T00:00:01.000Z",
        completedAt: "2025-01-01T00:00:11.000Z",
        status: "completed",
        resultSummary: "Returned test/build findings.",
        changedPaths: [],
        commandsExecuted: 0,
        testRuns: 0,
      },
    ],
    parallelBatches: [
      {
        id: "batch-discovery",
        assignmentIds: ["agent-source", "agent-tests"],
        startedAt: "2025-01-01T00:00:00.000Z",
        completedAt: "2025-01-01T00:00:11.000Z",
      },
    ],
    metrics: {
      delegatedTasks: 2,
      parallelBatches: 1,
      delegatedCommands: 0,
      delegatedTestRuns: 0,
      ownershipViolations: 0,
    },
  };
}

function performanceEvidence() {
  return {
    buildSystem: "maven",
    strategy: "precompiled-waves",
    runnerScript: "<skill-directory>/scripts/run-maven-waves.mjs",
    dependencyWarmup: {
      phaseCount: 1,
      commands: [
        "mvn dependency:go-offline",
        "mvn dependency:get -Dartifact=org.jacoco:org.jacoco.cli:0.8.13:jar:nodeps",
      ],
      status: "passed",
    },
    testCompile: {
      count: 1,
      command: "mvn -o test-compile -DskipTests",
      compiledInputDigest: "compiled-input-digest",
      invalidations: [],
    },
    waveRunner: {
      directPluginGoals: true,
      offline: true,
      lifecycleCompileInvocations: 0,
      uniqueReportDirectories: 4,
      uniqueExecutionDataFiles: 4,
      measurementStartedAt: "2025-01-01T00:00:00.000Z",
    },
    dockerEnvironment: {
      scope: "migration-run",
      containerStartCount: 1,
      invalidations: [],
      containerIdDigest: "docker-container-digest",
      sharedAcrossTestProcesses: true,
      ownershipVerified: true,
      readinessVerified: true,
      endpointBindingsVerified: true,
      fixtureIsolationVerified: true,
      cleanupVerified: true,
    },
    fullSuite: {
      generationCount: 1,
      runnerExecutions: 1,
      directPrecompiledExecution: true,
      status: "passed",
    },
    coverage: {
      mergeTool: "jacoco-cli",
      execPerWave: true,
      nativeTestXmlPerWave: true,
      htmlGenerationCount: 1,
      combinedXml:
        ".github/migrate-oracle-app/conversions/coverage/waves/combined.xml",
    },
    packaging: {
      prePackageCheck: "passed",
      cleanPackageCount: 1,
      testsSkippedBecauseFullSuitePassed: true,
      archivesInspected: 2,
      status: "passed",
    },
    agentScheduling: {
      backgroundNotifications: true,
      blockingWaitSecondsWhileIndependentWorkReady: 0,
    },
    targetMetadata: {
      batchedReadOnlyChecks: true,
      parallelBatches: 1,
      checks: ["identity", "privileges", "locks", "recovery"],
      databaseMutationsParallelized: false,
    },
  };
}

function waveEvidence(lane) {
  return Array.from({ length: 2 }, (_, index) => ({
    wave: index + 1,
    kind: index === 0 ? "generation" : "validation",
    plannedBehaviors: [`${lane}-behavior-${index + 1}`],
    testsAddedOrRepaired: index === 0 ? [`${lane}-test-${index + 1}`] : [],
    validatedTests: index === 1 ? [`${lane}-test-1`] : [],
    result: "passed",
  }));
}

function waveReportRows() {
  return ["unit", "integration"]
    .flatMap((lane) =>
      Array.from(
        { length: 2 },
        (_, index) =>
          `| ${lane} | ${index + 1} | Behavior ${index + 1} | Test ${index + 1} | 1 | 0 | ✅ Passed |`,
      ),
    )
    .join("\n");
}

function sha256(content) {
  return createHash("sha256").update(content).digest("hex");
}

async function createDriverArchive(
  root,
  { externalDriver, warDriver },
) {
  const content = path.join(root, "target", "driver-archive-content");
  const warContent = path.join(root, "target", "driver-war-content");
  await mkdir(content, { recursive: true });
  await mkdir(warContent, { recursive: true });
  await writeFile(
    path.join(content, "server.xml"),
    `<server><library id="postgresql-lib"><fileset dir="\${server.config.dir}/resources/lib" includes="postgresql.jar"/></library><properties.postgresql/></server>`,
  );
  if (externalDriver) {
    const external = path.join(
      content,
      "server",
      "resources",
      "lib",
      "postgresql.jar",
    );
    await mkdir(path.dirname(external), { recursive: true });
    await writeFile(external, "driver");
  }
  await writeFile(path.join(warContent, "web.xml"), "<web-app/>");
  if (warDriver) {
    const nested = path.join(
      warContent,
      "WEB-INF",
      "lib-provided",
      "postgresql.jar",
    );
    await mkdir(path.dirname(nested), { recursive: true });
    await writeFile(nested, "driver");
  }
  const war = path.join(content, "server", "apps", "application.war");
  await mkdir(path.dirname(war), { recursive: true });
  execFileSync("jar", ["cf", war, "-C", warContent, "."], {
    stdio: "ignore",
  });
  const archive = path.join(root, "target", "application.zip");
  await mkdir(path.dirname(archive), { recursive: true });
  execFileSync("jar", ["cf", archive, "-C", content, "."], {
    stdio: "ignore",
  });
  return archive;
}

function testReport(className, tests) {
  const cases = Array.from(
    { length: tests },
    (_, index) =>
      `<testcase classname="${className}" name="test${index + 1}" time="0" />`,
  ).join("\n");
  return `<testsuite name="${className}" tests="${tests}" failures="0" errors="0" skipped="0">
${cases}
</testsuite>`;
}

function namedTestReport(className, names) {
  return `<testsuite name="${className}" tests="${names.length}" failures="0" errors="0" skipped="0">
${names.map((name) => `<testcase classname="${className}" name="${name}" time="0" />`).join("\n")}
</testsuite>`;
}

function trxReport(className, names) {
  return `<TestRun>
  <Results>
    ${names.map((name, index) => `<UnitTestResult testId="${index}" testName="${className}.${name}" outcome="Passed" />`).join("\n")}
  </Results>
  <TestDefinitions>
    ${names.map((name, index) => `<UnitTest id="${index}" name="${className}.${name}"><TestMethod className="${className}" name="${name}" /></UnitTest>`).join("\n")}
  </TestDefinitions>
</TestRun>`;
}

function javaTestSource(testImport, methods, className) {
  const testMethods = Array.from(
    { length: methods },
    (_, index) => `  @Test
  public void test${index + 1}() {}`,
  ).join("\n\n");
  return `package com.example;

import ${testImport};

public class ${className} {
${testMethods}
}
`;
}

function conversionReport(
  finalTests,
  discoveryRows = `| Java | junit-vintage | 1 | 4 | 5.12.2 | 1 | 1 | 5 | 0 | ✅ Passed |
| Java | junit-jupiter | 1 | 6 | 5.12.2 | 1 | 1 | 7 | 0 | ✅ Passed |`,
  adapterIds = "junit-vintage and junit-jupiter",
) {
  const headings = [
    ["executive-conversion-summary", "Executive Conversion Summary"],
    ["conversion-transformation", "Conversion Transformation"],
    ["validation-results", "Validation Results"],
    ["test-coverage-results", "Test & Coverage Results"],
    ["database-test-environment", "Database Test Environment"],
    ["remaining-work-exceptions", "Remaining Work & Exceptions"],
    ["conversion-outcome", "Conversion Outcome"],
    ["coding-notes-processing", "Coding Notes Processing"],
    ["appendix-execution-evidence", "Appendix / Execution Evidence"],
  ];
  const sections = headings
    .map(
      ([anchor, heading]) =>
        `<a id="${anchor}"></a>\n## ${heading}\n\n${sectionBody(heading, finalTests, discoveryRows, adapterIds)}\n\n[Back to Top](#top)`,
    )
    .join("\n\n");
  return `<a id="top"></a>
# Oracle to PostgreSQL Application Code Conversion

## Table of Contents

${headings.map(([anchor, heading]) => `- [${heading}](#${anchor})`).join("\n")}

${sections}
`;
}

function executiveSummaryEvidence(finalTests) {
  return {
    conversionResult:
      "The selected application database boundary was converted from Oracle to PostgreSQL across runtime configuration, SQL, packaging, and database-facing tests.",
    customerActionSummary:
      "The converted application compiles, packages, and passes its unit and PostgreSQL integration tests. Overall status remains ⚠️ Action needed because runtime deployment smoke validation remains customer-owned.",
    scorecard: [
      {
        area: "Application conversion",
        result: "Selected application database paths converted to PostgreSQL",
        status: "complete",
      },
      {
        area: "Build & package",
        result: "Application compiled and deployable archives were produced",
        status: "passed",
      },
      {
        area: "Unit tests",
        result: `${finalTests} tests passed with zero failures`,
        status: "passed",
      },
      {
        area: "PostgreSQL integration tests",
        result: "Two real PostgreSQL integration waves passed",
        status: "passed",
      },
      {
        area: "Target validation",
        result: "Required objects and authorized write checks passed",
        status: "passed",
      },
      {
        area: "Production readiness",
        result: "Runtime deployment smoke validation remains customer-owned",
        status: "action-needed",
      },
    ],
    nextSteps: [
      {
        priority: "Required",
        area: "Runtime deployment",
        actionNeeded:
          "Deploy the converted application and run an authenticated smoke test",
        owner: "Application / platform team",
        customerAction: true,
        selectionReason: "required-runtime-validation",
        sourceEvidence: "Remaining Work & Exceptions / Required Before Production",
      },
      {
        priority: "Recommended",
        area: "End-to-end validation",
        actionNeeded:
          "Exercise representative business workflows against PostgreSQL",
        owner: "Application team",
        customerAction: true,
        selectionReason: "production-confidence",
        sourceEvidence: "Remaining Work & Exceptions / Recommended",
      },
    ],
    recommendedProductionReadinessPath: [
      "deploy to the target application runtime",
      "run smoke and end-to-end tests",
      "validate retained external integrations",
    ],
  };
}

function targetMetadataAcquisitionEvidence() {
  return {
    strategy: "code-derived-bounded-catalog-queries",
    dbContextToolsProhibited: [
      "postgres_mcp_db_context",
      "pgsql_db_context",
    ],
    dbContextCalls: 0,
    schemaWideCatalogQueries: 0,
    broadInformationSchemaQueries: 0,
    catalogSelectStarQueries: 0,
    manifestObjectCount: 1,
    identityBatchLimit: 20,
    detailBatchLimit: 10,
    queryCalls: 2,
    toolCallCounts: {
      postgres_mcp_db_context: 0,
      pgsql_db_context: 0,
      postgres_mcp_query: 2,
    },
    queries: [
      {
        id: "target-scalar-001",
        purpose: "connection-identity-and-server-state",
        boundedBy: "scalar",
        objectCount: 0,
        oidCount: 0,
        maxExpectedRows: 1,
        usesSelectStar: false,
        usesInformationSchema: false,
        queryDigest: "a".repeat(64),
        startedAt: "2025-01-01T00:00:00.000Z",
        completedAt: "2025-01-01T00:00:00.001Z",
        elapsedMs: 1,
        status: "passed",
      },
      {
        id: "target-identity-001",
        purpose: "manifest-object-identity",
        boundedBy: "exact-name",
        objectCount: 1,
        oidCount: 0,
        maxExpectedRows: 25,
        usesSelectStar: false,
        usesInformationSchema: false,
        queryDigest: "b".repeat(64),
        startedAt: "2025-01-01T00:00:00.002Z",
        completedAt: "2025-01-01T00:00:00.003Z",
        elapsedMs: 1,
        status: "passed",
      },
    ],
    timeouts: [],
    policyViolations: [],
  };
}

function postgresProviderEvidence(harness = "copilot-cli") {
  const initial = planPostgresToolDiscovery({ harness });
  const tools = initial.nextQueries[0].split(" ").map(
    (operation) => `${initial.route.serverId}-${operation}`,
  );
  const plan = planPostgresToolDiscovery({ harness, initialTools: tools });
  const { error, ...route } = plan.route;
  return {
    ...route,
    profileStore: route.provider,
    discovery: {
      strategy: "exact-operation-names",
      status: plan.status,
      initialTools: tools,
      searches: [],
    },
    mixedProviderOperationCount: 0,
    reason: error,
  };
}

function oracleDdlSourceEvidence() {
  return {
    supplied: false,
    folderPath: null,
    status: "not-supplied",
    folderDigest: null,
    indexPath: null,
    indexSha256: null,
    candidateFiles: 0,
    indexedObjects: 0,
    files: [],
    errors: [],
    warnings: [],
    missingTargetObjects: [],
    missingObjectResolutions: [],
  };
}

async function configureSignatureFallbackFixture(root, failClosed) {
  const conversion = path.join(
    root,
    ".github",
    "migrate-oracle-app",
    "conversions",
  );
  const ddlRoot = path.join(root, "database-ddl", "01-p-unknown");
  await mkdir(ddlRoot, { recursive: true });
  const deploy = failClosed
    ? `CREATE PROCEDURE app.p_unknown(p_id bigint)
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'Original Oracle body unavailable for app.p_unknown';
END;
$$;
`
    : `CREATE PROCEDURE app.p_unknown(p_id bigint)
LANGUAGE plpgsql
AS $$
BEGIN
  NULL;
END;
$$;
`;
  const undo = "DROP PROCEDURE IF EXISTS app.p_unknown(bigint);\n";
  const deployRelative = "database-ddl/01-p-unknown/deploy.sql";
  const undoRelative = "database-ddl/01-p-unknown/undo.sql";
  await writeFile(path.join(root, deployRelative), deploy);
  await writeFile(path.join(root, undoRelative), undo);

  const reportPath = path.join(conversion, "conversion-report.md");
  const report = await readFile(reportPath, "utf8");
  const customerAction = "Validate reconstructed body with database owner";
  await writeFile(
    reportPath,
    report.replace(
      "| None | Not supplied | None | No missing objects | Not generated | Not applicable | Not applicable |",
      `| app.p_unknown(bigint) | signature-only-fail-closed | Not available | missing-fail-closed / low — original body unavailable | ${deployRelative}; ${undoRelative}; src/test/java/com/example/CurrentTest.java | One synthetic bigint; expected explicit error; zero rows remain | Option off; ${customerAction} |`,
    ),
  );

  const statePath = path.join(conversion, "conversion-state.json");
  const state = JSON.parse(await readFile(statePath, "utf8"));
  state.status = "PARTIAL";
  state.optionResults["create-persistent-ddl"].applicableMissingObjects = 1;
  state.oracleDdlSource.missingTargetObjects = [
    {
      targetObject: "app.p_unknown(bigint)",
      targetKind: "procedure",
      oracleSourceIdentity: "APP.P_UNKNOWN",
      oracleMemberName: null,
      applicationCallSites: ["src/test/java/com/example/CurrentTest.java"],
    },
  ];
  state.oracleDdlSource.missingObjectResolutions = [
    {
      targetObject: "app.p_unknown(bigint)",
      targetKind: "procedure",
      resolutionStatus: "converted",
      semanticsSource: "signature-only-fail-closed",
      oracleSourceIdentity: "APP.P_UNKNOWN",
      sourceRelativePath: null,
      sourceSha256: null,
      matchMethod: "code-derived-signature-without-source-body",
      signatureReconciled: true,
      behaviorEvidence: [
        "Application call site requires one bigint input and no result.",
      ],
      sourceBodyAvailable: false,
      bodyCompleteness: "missing-fail-closed",
      semanticConfidence: "low",
      businessSemanticsProven: false,
      generatedFromInference: true,
      assumptions: ["The observed bigint call signature is complete."],
      limitations: ["The original Oracle procedure body is unavailable."],
      generatedDeployPath: deployRelative,
      generatedDeploySha256: sha256(deploy),
      generatedUndoPath: undoRelative,
      generatedUndoSha256: sha256(undo),
      applicationCallSitesUpdated: [
        "src/test/java/com/example/CurrentTest.java",
      ],
      dockerValidated: true,
      syntheticValidation: {
        syntheticInputsCreated: 1,
        fixtureRowsCreated: 0,
        fixtureRowsRemaining: 0,
        productionPathInvoked: true,
        assertions: [
          "Invocation raises the explicit original-body-unavailable error.",
          "No database rows are changed.",
        ],
        status: "passed",
        limitation: "Original business behavior cannot be validated without the Oracle body.",
      },
      customerValidationRequired: true,
      customerValidationAction: customerAction,
      persistentCreateRequested: false,
      persistentCreateAttempted: false,
      persistentCreateVerified: false,
      persistentCreate: {
        status: "not-requested",
        authorization: null,
        execution: null,
        verification: null,
        reason: "Option was off.",
        nextAction: null,
      },
      unresolvedReason: "Original Oracle business body remains unavailable.",
    },
  ];
  await writeFile(statePath, JSON.stringify(state));
}

async function configurePersistentDdlFixture(
  root,
  status,
  { harness = "copilot-cli", writable = true } = {},
) {
  await configureSignatureFallbackFixture(root, true);
  const conversion = path.join(root, ".github", "migrate-oracle-app", "conversions");
  const statePath = path.join(conversion, "conversion-state.json");
  const state = JSON.parse(await readFile(statePath, "utf8"));
  state.postgresProvider = postgresProviderEvidence(harness);
  if (harness === "vscode-copilot") {
    const metadata = state.targetMetadataAcquisition;
    metadata.toolCallCounts = {
      postgres_mcp_db_context: 0,
      pgsql_db_context: 0,
      pgsql_query: metadata.queryCalls,
    };
    for (const query of metadata.queries) query.tool = "pgsql_query";
  }
  state.options["create-persistent-ddl"] = true;
  const satisfied = ["created", "already-present"].includes(status);
  const reason = satisfied
    ? null
    : status === "failed"
      ? "Target CREATE failed."
      : writable
        ? "Only deploy/undo scripts were generated."
        : "The saved profile is read-only.";
  const nextAction = satisfied
    ? null
    : status === "failed"
      ? "Resolve the reported target error and refresh object state before retrying."
      : "Select a write-enabled saved profile and rerun create-persistent-ddl.";
  state.optionResults["create-persistent-ddl"] = {
    requested: true,
    status: satisfied ? "completed" : status,
    optionSatisfied: satisfied,
    applicableMissingObjects: 1,
    reason,
    nextAction,
  };
  const resolution = state.oracleDdlSource.missingObjectResolutions[0];
  const attempted = ["created", "failed"].includes(status);
  const targetDigest = "a".repeat(64);
  resolution.persistentCreateRequested = true;
  resolution.persistentCreateAttempted = attempted;
  resolution.persistentCreateVerified = status === "created";
  resolution.persistentCreate = {
    status,
    authorization: {
      source: "current-request",
      sourceDigest: "b".repeat(64),
      contextRefreshedAt: "2025-01-01T00:00:00.000Z",
      authorizedTargetDigest: targetDigest,
      refreshedTargetDigest: targetDigest,
      database: "migration_test",
      schema: "app",
      savedProfile: true,
      profileAccessMode: writable ? "rw" : "ro",
      modifyToolAvailable: true,
      defaultTransactionReadOnly: false,
      serverInRecovery: false,
      safetyChecks: Object.fromEntries(
        [
          "identity",
          "role",
          "privileges",
          "absence",
          "dependencies",
          "locks",
          "createOnly",
          "dockerValidation",
        ].map((check) => [check, check !== "absence" || status !== "already-present"]),
      ),
    },
    execution: attempted
      ? {
          tool: state.postgresProvider.selectedTools.modify,
          targetDigest,
          ddlSha256: resolution.generatedDeploySha256,
          executedAt: "2025-01-01T00:00:01.000Z",
          succeeded: status === "created",
          error: status === "created" ? null : reason,
        }
      : null,
    verification: satisfied
      ? {
          tool: state.postgresProvider.selectedTools.query,
          targetDigest,
          expectedDdlSha256: resolution.generatedDeploySha256,
          verifiedAt: "2025-01-01T00:00:02.000Z",
          objectIdentity: resolution.targetObject,
          objectKind: resolution.targetKind,
          contractMatches: true,
        }
      : null,
    reason,
    nextAction,
  };
  const result = status === "created"
    ? "✅ Created and verified"
    : status === "already-present"
      ? "✅ Already present and verified"
      : status === "failed"
        ? `❌ Failed; ${reason}; ${nextAction}`
        : `⚠️ Not executed; ${reason}; ${nextAction}`;
  const reportPath = path.join(conversion, "conversion-report.md");
  const report = await readFile(reportPath, "utf8");
  await writeFile(
    reportPath,
    report.replace(
      "Option off; Validate reconstructed body with database owner",
      `${result}; Validate reconstructed body with database owner`,
    ),
  );
  await writeFile(statePath, JSON.stringify(state));
}

function sectionBody(heading, finalTests, discoveryRows, adapterIds) {
  if (heading === "Coding Notes Processing") return renderCodingNotes(omittedCodingNotes());
  if (heading === "Executive Conversion Summary") {
    const executive = executiveSummaryEvidence(finalTests);
    return `${executive.conversionResult}

The converted application compiles, packages, and passes its unit and PostgreSQL integration tests. Overall status remains **⚠️ Action needed** because runtime deployment smoke validation remains customer-owned.

### Conversion Scorecard

| Area | Result | Status |
|---|---|---|
| Application conversion | ${executive.scorecard[0].result} | ✅ Complete |
| Build & package | ${executive.scorecard[1].result} | ✅ Passed |
| Unit tests | ${executive.scorecard[2].result} | ✅ Passed |
| PostgreSQL integration tests | ${executive.scorecard[3].result} | ✅ Passed |
| Target validation | ${executive.scorecard[4].result} | ✅ Passed |
| Production readiness | ${executive.scorecard[5].result} | ⚠️ Action needed |

### Next Steps / Actions Needed

The automated conversion and validation are substantially complete. Complete these customer-owned actions before considering the application production-ready:

| Priority | Area | Action needed | Owner |
|---|---|---|---|
| Required | Runtime deployment | Deploy the converted application and run an authenticated smoke test | Application / platform team |
| Recommended | End-to-end validation | Exercise representative business workflows against PostgreSQL | Application team |

> **Recommended production-readiness path:** deploy to the target application runtime → run smoke and end-to-end tests → validate retained external integrations.

The detailed conversion changes, validation evidence, test results, database activity, and remaining exceptions are documented in the sections below.`;
  }
  if (heading === "Validation Results") {
    return `| Stage / gate | Scope / evidence | Result | Readiness impact |
|---|---|---|---|
| Unit tests | ${finalTests} tests | ✅ Passed | Buildable |
| Test runner/adapter availability | ${adapterIds} | ✅ Passed | All source frameworks executable |
| Test discovery reconciliation | Eligible containers reconciled | ✅ Passed | Complete suite |
| Broken-test repair | 0 remaining | ✅ Passed | No blocking failures |
| Report artifact validation | Validator command | ✅ Passed | Report evidence validated |

### Runtime Driver Packaging Reconciliation
| Archive | Source → packaged runtime descriptor | Required driver path / pattern | Matching deployment entries | WAR-only copies | Result |
|---|---|---|---|---|---|
| None | None | Not applicable | None | None | ➖ Not applicable |

### Target Write Test Reconciliation
| Requested | Applicable mutation scenarios | Writable context | Write attempted | MCP modification / driver scenarios | Marker rows inserted / deleted / remaining | Sequence values consumed | Recorded write operations | Option satisfied | Result |
|---|---:|---|---|---:|---|---:|---:|---|---|
| No | 0 | Not requested | No | 0 | 0 / 0 / 0 | 0 | 0 | Yes | ➖ Not requested |`;
  }
  if (heading === "Conversion Transformation") {
    return `### Conversion Scope Reconciliation
| Category | Discovered | Converted | Intentionally retained | Deferred | Safely convertible remaining | Evidence / exact reason |
|---|---:|---:|---:|---:|---:|---|
| runtime-datasource | 1 | 1 | 0 | 0 | 0 | Selected datasource converted |

### Oracle DDL Source Reconciliation
| Missing PostgreSQL object | Oracle source / reconstruction provenance | Source DDL file / digest | Body completeness / confidence | PostgreSQL DDL and application call updates | Synthetic Docker validation | Persistent target result / customer validation |
|---|---|---|---|---|---|---|
| None | Not supplied | None | No missing objects | Not generated | Not applicable | Not applicable |

### Unused Artifacts
| Path | Kind | Unused evidence | Build/package references | Action | Result |
|---|---|---|---|---|---|
| lib/DealOutlookUI.jar | jar | No active references | None | Retained — option off | ➖ Retained |`;
  }
  if (heading === "Test & Coverage Results") {
    return `### Test Discovery Reconciliation
| Language | Framework / adapter | Source files | Test cases | Adapter version | Eligible containers | Executed containers | Executed tests | Missing containers | Result |
|---|---|---:|---:|---|---:|---:|---:|---:|---|
${discoveryRows}

### Broken-Test Repair Reconciliation
| Failure class / suite | Exposed failures / errors | Classification / root cause | Repair attempts | Files repaired | Final result / next action |
|---|---:|---|---:|---|---|
| None | 0 / 0 | No exposed failures | 0 | None | ✅ Passed |

### Coverage by Lane
| Lane | Baseline line | Final line | Baseline branch | Final branch | Evidence |
|---|---:|---:|---:|---:|---|
| Unit | 2/10 (20.00%) | 3/10 (30.00%) | 0/2 | 1/2 | report |

### Coverage Convergence
| Lane | Wave | Planned behaviors | Tests added / repaired | Line Δ | Branch Δ | Result |
|---|---:|---|---|---:|---:|---|
${waveReportRows()}`;
  }
  if (heading === "Database Test Environment") {
    return `### Docker Engine Availability
| Option enabled | Engine running at preflight | Desktop detected | Start attempted | Desktop state changed | Engine responsive | Result / limitation |
|---|---|---|---|---|---|---|
| No | Yes | Not checked | No | No | Yes | ✅ Already running |

### Synthetic Test Data
| Environment | Table | Fixture loads | Rows created | Peak rows present | Rows reused | Rows cleaned / rolled back | Rows remaining | Status |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Docker | app.t | 1 | 1 | 1 | 0 | 1 | 0 | ✅ Cleaned |

### Database Objects Exercised with Synthetic Data
| Object kind | Active / code-derived in tested scope | Exercised | Evidence / gap |
|---|---:|---:|---|
| Function | 0 | 0 | None |
| Procedure | 0 | 0 | None |

| Object / signature | Kind | Production entry point | Synthetic fixtures | Invocation form | Assertions / side effects | Environment | Result |
|---|---|---|---|---|---|---|---|
| app.t | Table | Repo.call | 1 row | Query | Row returned and cleaned | Docker | ✅ Passed |

### Behavior Exercised
| Test / scenario | Production entry point | PostgreSQL objects | Fixture tables / rows | Behavior exercised | Assertions / observed outcome | Result |
|---|---|---|---|---|---|---|
| Query | Repo.call | app.t | 1 | Query | Passed | ✅ Passed |`;
  }
  if (heading === "Appendix / Execution Evidence") {
    return `${renderRunMetrics(runMetricsEvidence())}

### Parallel Execution & Agent Utilization
**Agent facility:** available · **Independent eligible units:** 2 · **Parallel batches:** 1 · **Fallback:** None.

| Batch | Agent / type | Objective / unit | Mode / owned paths | Started | Completed | Result |
|---|---|---|---|---|---|---|
| batch-discovery | agent-source / explore | Source inventory | read-only | 2025-01-01T00:00:00Z | 2025-01-01T00:00:10Z | Completed |
| batch-discovery | agent-tests / explore | Test/build inventory | read-only | 2025-01-01T00:00:01Z | 2025-01-01T00:00:11Z | Completed |

### Performance & Execution Efficiency
| Control | Evidence | Result |
|---|---|---|
| Dependency warmup | One phase | ✅ Passed |
| Test compilation | One test-compile | ✅ Passed |
| Precompiled waves | Four direct offline waves | ✅ Passed |
| Coverage generation | JaCoCo CLI; XML per wave; one HTML | ✅ Passed |
| Complete suite | One direct precompiled execution | ✅ Passed |
| Pre-package checks | Source classification checked | ✅ Passed |
| Final clean package | One clean package after tests | ✅ Passed |
| Archive inspection | WAR and ZIP inspected | ✅ Passed |
| Agent waits | Background notifications; zero blocking seconds | ✅ Passed |
| Target metadata | Parallel bounded reads; serialized writes | ✅ Passed |

### Changes Inventory
#### Modified tracked files
| Path | Git change | Conversion unit / category | Updated line ranges | Result / purpose |
|---|---|---|---|---|
| [a.java](file-diffs.md#file-00001) | modified | Code | 1 | Test |`;
  }
  return "Complete evidence.";
}

function diffReport() {
  return `<a id="top"></a>
# Sanitized Modified-File Diffs

## Table of Contents
- [a.java](#file-00001)

## FILE-00001
\`a.java\`

\`\`\`diff
-old
+new
\`\`\`

[Back to Top](#top) · [Back to Conversion Report](./conversion-report.md#modified-tracked-files)
`;
}
