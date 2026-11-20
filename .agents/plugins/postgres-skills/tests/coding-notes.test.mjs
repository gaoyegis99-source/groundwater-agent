import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { chmod, mkdir, mkdtemp, readFile, rm, stat, symlink, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  ingestCodingNotes, inventoryCodingNotes, omittedCodingNotes, renderCodingNotes,
  validateCodingNotes, validateCodingNotesReport, verifyCodingNotes,
} from "../skills/migrate-oracle-app/scripts/coding-notes.mjs";
import {
  checkpointMigration, finalizeMigrationArtifacts, getManifest, openMigrationRuntime,
  putManifest, withPrimaryExecution, fingerprintInputs, digestJson,
} from "../skills/migrate-oracle-app/scripts/migration-runtime.mjs";

const guidance = `- CN-01: Map the routine to app.lookup_customer_v2(bigint).
- CN-02: Use app.customer_identifier_seq without changing allocation.
- CN-03: Preserve signatures and parameter binding.
- CN-04: Preserve logging; do not add dependencies.
- CN-05: Validate target return contracts before claiming compatibility.
`;

test("the portable skill-local notes CLI resolves from an unrelated application cwd", async (t) => {
  const { root, input, directory } = await fixture(t);
  const script = fileURLToPath(new URL("../skills/migrate-oracle-app/scripts/coding-notes.mjs", import.meta.url));
  const notes = JSON.parse(execFileSync(process.execPath, [script, "ingest", root, input], {
    cwd: root, encoding: "utf8",
  }));
  assert.equal(notes.status, "processed");
  assert.equal(notes.items.length, 5);
  const statePath = path.join(directory, "assessment-data.json");
  await writeFile(statePath, JSON.stringify({ codingNotes: notes }));
  const markdown = execFileSync(process.execPath, [script, "render", statePath], {
    cwd: root, encoding: "utf8",
  });
  assert.equal(markdown.trim(), renderCodingNotes(notes));
  await verifyCodingNotes(root, notes);
});

async function fixture(t) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "coding-notes-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const root = path.join(directory, "app");
  await mkdir(root);
  execFileSync("git", ["init", "--quiet"], { cwd: root });
  const input = path.join(directory, "notes.md");
  await writeFile(input, guidance);
  return { root, input, directory };
}

function evaluate(item, disposition = "pending") {
  Object.assign(item, {
    evaluated: true, disposition,
    evidence: [{
      kind: disposition === "applied" ? "diff" : "limitation",
      reference: disposition === "applied" ? "src/CustomerRepository.java:12" : "target-contract-gate",
      detail: disposition === "applied" ? "Routine call changed with bound arguments." : "No target connection was supplied; return shape remains unverified.",
    }],
    nextAction: ["pending", "conflicting"].includes(disposition) ? "Verify the bounded return contract with the selected target." : null,
  });
}

function report(notes) {
  return `<a id="coding-notes-processing"></a>\n## Coding Notes Processing\n\n${renderCodingNotes(notes)}\n\n[Back to Top](#top)\n`;
}

test("file ingestion preserves all supplied IDs and exposes no raw contents or paths", async (t) => {
  const { root, input } = await fixture(t);
  const before = await readFile(input);
  const notes = await ingestCodingNotes(root, input);
  assert.equal(notes.status, "processed");
  assert.deepEqual(notes.items.map((item) => item.id), ["CN-01", "CN-02", "CN-03", "CN-04", "CN-05"]);
  assert.ok(notes.items.every((item) => !item.evaluated && item.disposition === "pending"));
  assert.equal(notes.provenance.resolution, "explicit-file");
  assert.doesNotMatch(JSON.stringify(notes), /lookup_customer|customer_identifier|\/Users\/|notes\.md/);
  await verifyCodingNotes(root, notes);
  assert.deepEqual(await readFile(input), before);
  const locator = path.join(root, ".git", "migrate-oracle-app", "coding-notes", `${notes.inputRef}.json`);
  assert.equal((await stat(locator)).mode & 0o777, 0o600);
  assert.doesNotMatch(await readFile(locator, "utf8"), /lookup_customer/);
});

test("directory resolution reads only the canonical path and never searches unrelated candidates", async (t) => {
  const { root, directory } = await fixture(t);
  const project = path.join(directory, "project");
  await mkdir(path.join(project, "unrelated"), { recursive: true });
  await writeFile(path.join(project, "unrelated", "coding_notes.md"), guidance);
  const missing = await ingestCodingNotes(root, project);
  assert.equal(missing.status, "missing");
  assert.equal(missing.reason, "canonical-file-missing");
  const canonical = path.join(project, "results", "application_guidance", "coding_notes.md");
  await mkdir(path.dirname(canonical), { recursive: true });
  await writeFile(canonical, guidance);
  await assert.rejects(verifyCodingNotes(root, missing), /changed/);
  const notes = await ingestCodingNotes(root, project);
  assert.equal(notes.status, "processed");
  assert.equal(notes.provenance.inputKind, "directory");
  assert.equal(notes.provenance.resolution, "results/application_guidance/coding_notes.md");
  assert.equal(notes.items.length, 5);
  await verifyCodingNotes(root, notes);
});

test("omitted notes do not touch the filesystem and cannot claim ingestion", async () => {
  const notes = await ingestCodingNotes("/does-not-exist");
  assert.deepEqual(notes, omittedCodingNotes());
  await verifyCodingNotes("/does-not-exist", notes);
  assert.match(renderCodingNotes(notes), /not-supplied/);
  assert.doesNotMatch(renderCodingNotes(notes), /^- \[x\]/m);
  assert.throws(() => validateCodingNotes({ ...notes, sha256: "a".repeat(64) }), /cannot claim ingestion/);
});

test("missing and unreadable inputs are explicit, empty and never checked", async (t) => {
  const { root, directory, input } = await fixture(t);
  const missing = await ingestCodingNotes(root, path.join(directory, "absent.md"));
  assert.equal(missing.status, "missing");
  await verifyCodingNotes(root, missing);
  await writeFile(input, Buffer.from([0xff, 0xfe]));
  const unreadable = await ingestCodingNotes(root, input);
  assert.equal(unreadable.status, "unreadable");
  assert.deepEqual(unreadable.items, []);
  await verifyCodingNotes(root, unreadable);
  assert.doesNotMatch(renderCodingNotes(unreadable), /^- \[x\]/m);
});

test("permission-denied notes remain unreadable rather than fabricating a read", { skip: process.getuid?.() === 0 }, async (t) => {
  const { root, input } = await fixture(t);
  await chmod(input, 0o000);
  try {
    assert.equal((await ingestCodingNotes(root, input)).status, "unreadable");
  } finally {
    await chmod(input, 0o600);
  }
});

test("symlink inputs and canonical-directory escapes are rejected without reading targets", async (t) => {
  const { root, input, directory } = await fixture(t);
  const link = path.join(directory, "link.md");
  await symlink(input, link);
  assert.equal((await ingestCodingNotes(root, link)).status, "unreadable");
  const project = path.join(directory, "project");
  await mkdir(project);
  await symlink(directory, path.join(project, "results"));
  assert.equal((await ingestCodingNotes(root, project)).status, "unreadable");
});

test("oversized files and duplicate IDs cannot become partially processed guidance", async (t) => {
  const { root, input } = await fixture(t);
  await writeFile(input, "a".repeat(1_000_001));
  assert.equal((await ingestCodingNotes(root, input)).status, "unreadable");
  await writeFile(input, "- CN-01: first\n- CN-01: conflicting second\n");
  const notes = await ingestCodingNotes(root, input);
  assert.equal(notes.status, "ambiguous");
  assert.equal(notes.reason, "duplicate-note-ids");
  assert.deepEqual(notes.items, []);
  await verifyCodingNotes(root, notes);
  await writeFile(input, "- [private-host.example.com]: Unsafe identity.\n");
  const privateId = await ingestCodingNotes(root, input);
  assert.equal(privateId.status, "ambiguous");
  assert.equal(privateId.reason, "unsafe-note-id");
  assert.doesNotMatch(JSON.stringify(privateId), /private-host/);
});

test("all prose, headings, lists and fenced guidance get deterministic content-based IDs", () => {
  const first = inventoryCodingNotes("# Guidance\n\nPreserve binding.\n\n- Use logging.\n\n```sql\nselect 1;\n\nselect 2;\n```\n");
  assert.equal(first.length, 4);
  assert.deepEqual(first.map((item) => [item.startLine, item.endLine]), [[1, 1], [3, 3], [5, 5], [7, 11]]);
  const reordered = inventoryCodingNotes("New prose.\n\nPreserve binding.\n\n# Guidance\n\n- Use logging.\n");
  assert.equal(first[0].id, reordered[2].id);
  assert.equal(first[1].id, reordered[1].id);
  assert.equal(first[2].id, reordered[3].id);
  const repeats = inventoryCodingNotes("Same.\n\nSame.\n");
  assert.notEqual(repeats[0].id, repeats[1].id);
  assert.throws(() => inventoryCodingNotes("- note\n".repeat(2001)), /budget/);
});

test("explicit Markdown IDs survive wrappers, and empty files remain genuinely empty", async (t) => {
  assert.deepEqual(inventoryCodingNotes("- [x] **CN-01**: Keep.\n## [NOTE_A]: Keep.\n").map((item) => item.id), ["CN-01", "NOTE_A"]);
  const table = inventoryCodingNotes("| ID | Guidance |\n|---|---|\n| CN-01 | Keep binding. |\n| CN-02 | Keep logging. |\n");
  assert.deepEqual(table.slice(2).map((item) => item.id), ["CN-01", "CN-02"]);
  assert.equal(inventoryCodingNotes("- [alpha]: Keep binding.\n")[0].id, "alpha");
  assert.deepEqual(inventoryCodingNotes("- [123]: Keep binding.\n- CN.4: Keep logging.\n").map((item) => item.id), ["123", "CN.4"]);
  const { root, input } = await fixture(t);
  await writeFile(input, " \n\n");
  const empty = await ingestCodingNotes(root, input);
  assert.equal(empty.status, "processed");
  assert.deepEqual(empty.items, []);
  assert.match(renderCodingNotes(empty), /No nonempty notes/);
  await verifyCodingNotes(root, empty);
});

test("notes-dependent source and target caches reject stale lookup evidence and refresh after re-ingestion", async (t) => {
  const { root, input } = await fixture(t);
  await writeFile(path.join(root, "App.java"), "class App {}");
  const runtime = await openMigrationRuntime({ root, runId: "notes-cache", actorId: "primary", initialize: true });
  let notes = await ingestCodingNotes(root, input);
  await withPrimaryExecution(runtime, "cache", async () => {
    const checkpoint = async () => checkpointMigration(runtime, {
      phase: "assessment", operations: [{ op: "set", path: ["codingNotes"], value: notes }],
    });
    await checkpoint();
    for (const kind of ["source-findings", "target-objects"]) {
      const request = { kind, key: "notes-dependent", inputs: ["App.java"], contextDigest: "fresh-target" };
      const lookup = await getManifest(runtime, request);
      await putManifest(runtime, { ...request, expectedInputDigest: lookup.inputDigest, data: { findings: [] } });
      assert.equal((await getManifest(runtime, request)).status, "hit");
      notes.items.forEach((item) => evaluate(item));
      await checkpoint();
      assert.equal((await getManifest(runtime, request)).status, "hit", "evaluation alone must not invalidate discovery");
      await writeFile(input, `${await readFile(input, "utf8")}\nAdditional guidance for ${kind}.`);
      await assert.rejects(getManifest(runtime, request), /changed/);
      notes = await ingestCodingNotes(root, input);
      await checkpoint();
      const fresh = await getManifest(runtime, request);
      assert.equal(fresh.status, "miss");
      assert.notEqual(fresh.inputDigest, lookup.inputDigest);
      await assert.rejects(putManifest(runtime, {
        ...request, expectedInputDigest: lookup.inputDigest, data: { stale: true },
      }), /changed after lookup/);
      await putManifest(runtime, { ...request, expectedInputDigest: fresh.inputDigest, data: { refreshed: true } });
      assert.equal((await getManifest(runtime, request)).status, "hit");
    }
  });
});

test("combined runtime assembly refuses different notes inputs across phase snapshots", async (t) => {
  const { root, input } = await fixture(t);
  const runtime = await openMigrationRuntime({ root, runId: "notes-combined", actorId: "primary", initialize: true });
  const notes = await ingestCodingNotes(root, input);
  await withPrimaryExecution(runtime, "finalize", async () => {
    for (const phase of ["assessment", "conversion"]) {
      await checkpointMigration(runtime, {
        phase, operations: [{ op: "set", path: ["codingNotes"], value: phase === "assessment" ? notes : omittedCodingNotes() }],
      });
    }
    await assert.rejects(finalizeMigrationArtifacts(runtime, {
      phases: ["assessment", "conversion"], templates: {}, validateArtifacts: false,
    }), /same current coding notes input snapshot/);
  });
});

test("checked items mean evaluated, including explicit conflicts and pending target validation", async (t) => {
  const { root, input } = await fixture(t);
  const notes = await ingestCodingNotes(root, input);
  for (const [index, disposition] of ["applied", "no-change-needed", "not-applicable", "conflicting", "pending"].entries()) {
    evaluate(notes.items[index], disposition);
  }
  const markdown = report(notes);
  assert.equal((markdown.match(/^- \[x\]/gm) ?? []).length, 5);
  assert.match(markdown, /\*\*CN-05\*\* - pending/);
  assert.match(markdown, /\*\*CN-04\*\* - conflicting/);
  assert.match(markdown, /not implementation or runtime validation/);
  await validateCodingNotesReport(root, { codingNotes: notes, status: "PARTIAL" }, markdown, "conversion");
  await validateCodingNotesReport(root, { codingNotes: notes, status: "PARTIAL" }, markdown.replaceAll("\n", "\r\n"), "conversion");
  assert.throws(() => validateCodingNotes(notes, { phase: "conversion", status: "READY" }), /READY/);
  assert.throws(() => validateCodingNotes(notes, { phase: "conversion", status: "ready" }), /READY/);
  assert.throws(() => validateCodingNotes(notes, { phase: "assessment", status: "READY" }), /READY/);
  await assert.rejects(validateCodingNotesReport(root, { codingNotes: notes }, markdown.replace("pending;", "applied;"), "conversion"), /does not match/);
});

test("unread entries stay unchecked and fabricated evaluation/status evidence is rejected", async (t) => {
  const { root, input } = await fixture(t);
  const notes = await ingestCodingNotes(root, input);
  assert.equal((report(notes).match(/^- \[ \]/gm) ?? []).length, 5);
  const bad = structuredClone(notes);
  bad.items[0].evaluated = true;
  assert.throws(() => renderCodingNotes(bad), /concrete evidence/);
  bad.items[0].disposition = "applied";
  bad.items[0].evidence = [{ kind: "limitation", reference: "assessment", detail: "Only a recommendation exists; no code was changed." }];
  bad.items[0].nextAction = null;
  assert.throws(() => renderCodingNotes(bad), /source\/diff/);
  bad.items[0].evaluated = false;
  assert.throws(() => renderCodingNotes(bad), /Unread/);
  bad.items[0].disposition = "validated";
  assert.throws(() => renderCodingNotes(bad), /invalid/);
});

test("actual file inventory rejects fabricated ingestion, dropped notes, wrong digests and line spans", async (t) => {
  const { root, input } = await fixture(t);
  const notes = await ingestCodingNotes(root, input);
  for (const change of [
    (item) => item.items.pop(),
    (item) => { item.items[0].sha256 = "b".repeat(64); },
    (item) => { item.items[0].startLine = 99; item.items[0].endLine = 100; },
    (item) => { item.sha256 = "a".repeat(64); },
    (item) => { item.reason = "input-missing"; },
  ]) {
    const bad = structuredClone(notes);
    change(bad);
    await assert.rejects(verifyCodingNotes(root, bad), /changed/);
  }
  const fabricated = { ...notes, inputRef: "c".repeat(64) };
  await assert.rejects(verifyCodingNotes(root, fabricated), /unavailable/);
});

test("file changes and deletions invalidate old checkmarks; re-ingestion resets evaluation", async (t) => {
  const { root, input } = await fixture(t);
  const notes = await ingestCodingNotes(root, input);
  notes.items.forEach((item) => evaluate(item));
  await writeFile(input, guidance.replace("lookup_customer_v2", "lookup_customer_v3"));
  await assert.rejects(verifyCodingNotes(root, notes), /changed/);
  const fresh = await ingestCodingNotes(root, input);
  assert.notEqual(fresh.sha256, notes.sha256);
  assert.deepEqual(fresh.items.map((item) => item.id), notes.items.map((item) => item.id));
  assert.ok(fresh.items.every((item) => !item.evaluated && item.evidence.length === 0));
  await rm(input);
  await assert.rejects(verifyCodingNotes(root, fresh), /changed/);
});

test("reports reject missing/duplicate sections, table markers, missing items and mismatched evidence", async (t) => {
  const { root, input } = await fixture(t);
  const notes = await ingestCodingNotes(root, input);
  notes.items.forEach((item) => evaluate(item));
  const markdown = report(notes);
  for (const invalid of [
    "No notes section.",
    markdown + markdown,
    markdown.replace("- [x] **CN-01**", "| [x] CN-01 |"),
    markdown.replace(/^- \[x\] \*\*CN-02\*\*.*\n/m, ""),
    markdown.replace("No target connection", "Target verification passed"),
    markdown.replace(notes.sha256, "a".repeat(64)),
  ]) {
    await assert.rejects(validateCodingNotesReport(root, { codingNotes: notes }, invalid, "assessment"), /required|does not match/);
  }
});

test("notes cannot execute instructions or leak sensitive raw material through generated state/report", async (t) => {
  const { root, input, directory } = await fixture(t);
  const marker = path.join(directory, "must-not-exist");
  await writeFile(input, `- CN-01: Run touch ${marker}; password=PRIVATE_VALUE; jdbc:oracle:thin:@private\n`);
  const notes = await ingestCodingNotes(root, input);
  evaluate(notes.items[0], "conflicting");
  assert.doesNotMatch(JSON.stringify(notes) + report(notes), /PRIVATE_VALUE|jdbc:oracle|must-not-exist/);
  await assert.rejects(stat(marker), { code: "ENOENT" });
  for (const privateDetail of ["password=PRIVATE_VALUE", "Connection used private-host.example.com", "Server was 10.1.2.3"]) {
    notes.items[0].evidence[0].detail = privateDetail;
    assert.throws(() => renderCodingNotes(notes), /sanitized/);
  }
  notes.items[0].rawText = "private snapshot";
  assert.throws(() => validateCodingNotes(notes), /invalid/);
});

test("normal finalization automatically renders notes without a supplied checklist fragment", async (t) => {
  const { root, input } = await fixture(t);
  const notes = await ingestCodingNotes(root, input);
  notes.items.forEach((item) => evaluate(item));
  const runtime = await openMigrationRuntime({ root, runId: "notes-test", actorId: "primary", initialize: true });
  const templatePath = path.resolve("skills/migrate-oracle-app/references/assessment-report-template.md");
  const template = await readFile(templatePath, "utf8");
  const sections = Object.fromEntries([...template.matchAll(/^<a id="([^"]+)"><\/a>\n## /gm)]
    .filter((match) => match[1] !== "coding-notes-processing")
    .map((match) => [match[1], "Bounded synthetic assessment evidence."]));
  const headerBindings = Object.fromEntries([...template.split('<a id="executive-summary">')[0].matchAll(/`(<[^>]+>)`/g)]
    .map((match) => [match[1], "synthetic"]));
  const timing = { status: "completed", startedAt: "2026-11-10T10:00:00Z", completedAt: "2026-11-10T10:01:00Z", wallClockElapsedMs: 60000 };
  await withPrimaryExecution(runtime, "notes-finalization", async () => {
    await checkpointMigration(runtime, {
      phase: "assessment", sections, headerBindings,
      operations: [
        { op: "set", path: ["codingNotes"], value: notes },
        { op: "set", path: ["status"], value: "ACTION NEEDED" },
        { op: "set", path: ["runMetrics"], value: { timeZone: "UTC", assessment: timing, conversion: { status: "not-run" }, total: timing } },
      ],
    });
    const receipt = await finalizeMigrationArtifacts(runtime, {
      phases: ["assessment"], templates: { assessment: templatePath }, validateArtifacts: false,
    });
    const markdown = await readFile(path.join(root, receipt.artifacts[1].path), "utf8");
    const state = JSON.parse(await readFile(path.join(root, receipt.artifacts[0].path), "utf8"));
    await validateCodingNotesReport(root, state, markdown, "assessment");
    assert.equal((markdown.match(/^- \[x\]/gm) ?? []).length, 5);
    await writeFile(input, `${guidance}\nNew guidance.`);
    await assert.rejects(finalizeMigrationArtifacts(runtime, {
      phases: ["assessment"], templates: { assessment: templatePath }, validateArtifacts: false,
    }), /changed/);
  });
});

test("all six report templates and instruction surfaces wire automatic notes reporting", async () => {
  const references = path.resolve("skills/migrate-oracle-app/references");
  for (const name of ["assessment-report", "conversion-report", "no-conversion-assessment", "no-conversion-report", "scope-review-assessment", "scope-review-report"]) {
    const template = await readFile(path.join(references, `${name}-template.md`), "utf8");
    assert.match(template, /\[Coding Notes Processing\]\(#coding-notes-processing\)/);
    assert.match(template, /<a id="coding-notes-processing"><\/a>\n## Coding Notes Processing/);
  }
  const skill = await readFile(path.resolve("skills/migrate-oracle-app/SKILL.md"), "utf8");
  assert.match(skill, /coding-notes\.md/);
  assert.match(skill, /Every reporting exit includes/);
  const contract = await readFile(path.join(references, "coding-notes.md"), "utf8");
  assert.match(contract, /<skill-directory>\/scripts\/coding-notes\.mjs/);
  assert.match(contract, /absolute script path from the loaded skill directory/);
  assert.doesNotMatch(contract, /<plugin-root>[/\\]scripts/);
  const manifest = JSON.parse(await readFile(path.resolve("package.json"), "utf8"));
  assert.match(manifest.scripts.test, /tests\/coding-notes\.test\.mjs/);
});

test("ordinary repeated Markdown labels, numeric table cells and versions get generated IDs", async (t) => {
  const { root, input } = await fixture(t);
  const samples = [
    "- **Note**: Preserve binding.\n- **Note**: Preserve logging.\n",
    "- `ROWNUM` in OrderDao must change.\n- `ROWNUM` in ReportDao must change.\n",
    "- Read-only DAOs stay read-only.\n- Read-only views stay read-only.\n",
    "- 3 tables need inspection.\n- 3 packages need inspection.\n",
    "| 1 | First mapping |\n\n| 1 | Second mapping |\n",
    "- 19.3.0.0 driver must be replaced.\n- 19.3.0.0 driver also affects packaging.\n",
  ];
  for (const sample of samples) {
    await writeFile(input, sample);
    const notes = await ingestCodingNotes(root, input);
    assert.equal(notes.status, "processed", sample);
    assert.equal(notes.items.length, 2, sample);
    assert.ok(notes.items.every((item) => item.id.startsWith("NOTE-")), sample);
    assert.notEqual(notes.items[0].id, notes.items[1].id);
    await verifyCodingNotes(root, notes);
  }
  assert.deepEqual(inventoryCodingNotes("- **CN-01**: first\n- `CN-02`) second\n- [alpha] - third\n- CN03: fourth\n")
    .map((item) => item.id), ["CN-01", "CN-02", "alpha", "CN03"]);
  for (const sample of ["- CN-01: first\n- CN-01: second\n", "- [alpha]: first\n- [alpha]: second\n"]) {
    await writeFile(input, sample);
    assert.equal((await ingestCodingNotes(root, input)).status, "ambiguous");
  }
});

test("ready normalization rejects case and emoji variants with pending evidence", async (t) => {
  const { root, input } = await fixture(t);
  const notes = await ingestCodingNotes(root, input);
  notes.items.forEach((item) => evaluate(item));
  for (const status of ["READY", "ready", " Ready ", "✅ READY", "✅ Ready", "🔧 ready"]) {
    for (const phase of ["assessment", "conversion"]) {
      assert.throws(() => validateCodingNotes(notes, { phase, status }), /READY/, status);
    }
  }
});

test("private keyed path references are stable locally, distinct across worktrees and never exported", async (t) => {
  const first = await fixture(t);
  const second = await fixture(t);
  const one = await ingestCodingNotes(first.root, first.input);
  const again = await ingestCodingNotes(first.root, first.input);
  const other = await ingestCodingNotes(second.root, first.input);
  assert.equal(one.inputRef, again.inputRef);
  assert.equal(one.provenance.resolvedRef, again.provenance.resolvedRef);
  assert.notEqual(one.inputRef, other.inputRef);
  assert.notEqual(one.inputRef, createHash("sha256").update(first.input).digest("hex"));
  assert.notEqual(one.provenance.resolvedRef, createHash("sha256").update(first.input).digest("hex"));
  const secretPath = path.join(first.root, ".git/migrate-oracle-app/coding-notes/reference-key");
  const key = await readFile(secretPath);
  assert.equal(key.length, 32);
  assert.equal((await stat(secretPath)).mode & 0o777, 0o600);
  assert.equal((await stat(path.dirname(secretPath))).mode & 0o777, 0o700);
  assert.ok(!JSON.stringify(one).includes(first.input));
  assert.ok(!JSON.stringify(one).includes(key.toString("hex")));
  assert.ok(!renderCodingNotes(one).includes(key.toString("hex")));
  await verifyCodingNotes(first.root, one);
  await assert.rejects(verifyCodingNotes(second.root, one), /Re-ingest.*re-evaluate/);
  await rm(path.join(first.root, `.git/migrate-oracle-app/coding-notes/${one.inputRef}.json`));
  await assert.rejects(verifyCodingNotes(first.root, one), /copied reports.*new clone or CI/);
  await rm(secretPath);
  await assert.rejects(verifyCodingNotes(first.root, one), /Re-ingest.*re-evaluate/);
  assert.throws(() => validateCodingNotes({ ...one, schemaVersion: 1 }), /format changed.*re-ingest/i);
});

test("notes-sensitive lookup requires checkpoint even when an old notes-agnostic cache exists", async (t) => {
  const { root } = await fixture(t);
  await writeFile(path.join(root, "App.java"), "class App {}");
  const runtime = await openMigrationRuntime({ root, runId: "missing-notes", actorId: "primary", initialize: true });
  for (const kind of ["source-findings", "target-objects"]) {
    const request = { kind, key: "unbound", inputs: ["App.java"], contextDigest: "fresh-target" };
    const data = { staleNotesAgnostic: true };
    await mkdir(path.join(runtime.directory, "cache"), { recursive: true });
    await writeFile(path.join(runtime.directory, "cache", `${digestJson([kind, request.key])}.json`), JSON.stringify({
      schemaVersion: 1, kind, key: request.key, rootDigest: digestJson(runtime.root),
      inputDigest: (await fingerprintInputs(root, request.inputs)).digest,
      contextDigest: request.contextDigest, dataDigest: digestJson(data), data,
    }));
    await assert.rejects(getManifest(runtime, request), /Checkpoint codingNotes/);
  }
  const java = await getManifest(runtime, { kind: "java-release", key: "release", inputs: ["App.java"] });
  assert.equal(java.status, "miss", "Java cache does not depend on notes");
  await withPrimaryExecution(runtime, "notes", async () => {
    await checkpointMigration(runtime, { phase: "assessment", operations: [
      { op: "set", path: ["codingNotes"], value: omittedCodingNotes() },
    ] });
    const request = { kind: "source-findings", key: "explicit-omission", inputs: ["App.java"] };
    const lookup = await getManifest(runtime, request);
    await putManifest(runtime, { ...request, expectedInputDigest: lookup.inputDigest, data: { findings: [] } });
    assert.equal((await getManifest(runtime, request)).status, "hit");
  });
  const next = await openMigrationRuntime({ root, runId: "next-run", actorId: "primary", initialize: true });
  await assert.rejects(getManifest(next, { kind: "source-findings", key: "explicit-omission", inputs: ["App.java"] }), /Checkpoint codingNotes/);
});

test("an unclosed fence retains later guidance in one complete conservative block", () => {
  const items = inventoryCodingNotes("```sql\nselect 1;\n\n- CN-01: later guidance\n- CN-02: more guidance\n");
  assert.equal(items.length, 1);
  assert.equal(items[0].startLine, 1);
  assert.equal(items[0].endLine, 6);
});
