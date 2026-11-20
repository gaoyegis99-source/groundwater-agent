import { createHash, randomUUID } from "node:crypto";
import {
  lstat, mkdir, open, readFile, readdir, realpath, rename, unlink,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runProcessCommand } from "./process-command.mjs";
import { renderRunMetrics } from "./format-run-metrics.mjs";
import { codingNotesInputDigest, renderCodingNotes, verifyCodingNotes } from "./coding-notes.mjs";

const PHASES = new Set(["assessment", "conversion"]);
const CACHE_KINDS = new Set(["source-findings", "target-objects", "java-release"]);
const OWNER = "primary";
const EXCLUDED = new Set([".git", "target", "node_modules", ".venv", "__pycache__"]);
const UNSAFE_KEYS = new Set(["__proto__", "prototype", "constructor"]);
const PRIVATE_KEYS = new Set([
  "password", "username", "hostname", "host", "connectionstring", "profileid",
  "rawsource", "sourcecode", "rawsql", "sqlbody",
]);

export function digestJson(value) {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

function requiredString(value, label) {
  if (typeof value !== "string" || !value.trim()) throw new TypeError(`${label} is required`);
  return value;
}

function assertJson(value, ancestry = "") {
  if (typeof value === "number" && !Number.isFinite(value)) {
    throw new TypeError(`${ancestry} must contain finite JSON numbers`);
  }
  if (typeof value === "string" &&
    /jdbc:(?:oracle|postgresql):|postgres(?:ql)?:\/\/|(?:password|pwd)\s*=/i.test(value)) {
    throw new TypeError(`${ancestry} must not persist credentials or connection strings`);
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertJson(item, `${ancestry}[${index}]`));
  } else if (value && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) {
      if (UNSAFE_KEYS.has(key) || PRIVATE_KEYS.has(key.toLowerCase())) {
        throw new TypeError(`${ancestry}.${key} is not permitted in sanitized runtime evidence`);
      }
      assertJson(item, `${ancestry}.${key}`);
    }
  } else if (value !== null && !["string", "number", "boolean"].includes(typeof value)) {
    throw new TypeError(`${ancestry} must be JSON data`);
  }
}

async function readJsonOrMissing(file) {
  let text;
  try {
    text = await readFile(file, "utf8");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    return null;
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    throw new Error(`Invalid runtime JSON: ${path.basename(file)}`);
  }
}

async function atomicJson(file, value) {
  await mkdir(path.dirname(file), { recursive: true });
  const temporary = `${file}.${randomUUID()}.tmp`;
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(value)}\n`, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  try {
    await rename(temporary, file);
  } catch (error) {
    await unlink(temporary);
    throw error;
  }
}

function contained(root, relative) {
  requiredString(relative, "relative path");
  const absolute = path.resolve(root, relative);
  const difference = path.relative(root, absolute);
  if (path.isAbsolute(relative) || difference === ".." ||
    difference.startsWith(`..${path.sep}`) || path.isAbsolute(difference)) {
    throw new Error("Runtime paths must remain within their explicit workspace");
  }
  return absolute;
}

async function rejectSymlinkAncestors(root, target, allowMissing = false) {
  const relative = path.relative(root, target);
  let current = root;
  for (const segment of relative.split(path.sep).filter(Boolean)) {
    current = path.join(current, segment);
    let metadata;
    try {
      metadata = await lstat(current);
    } catch (error) {
      if (error.code !== "ENOENT" || !allowMissing) throw error;
      return;
    }
    if (metadata.isSymbolicLink()) throw new Error("Runtime paths must not traverse symbolic links");
  }
}

export async function fingerprintInputs(root, inputs) {
  if (!Array.isArray(inputs) || inputs.length === 0) throw new TypeError("fingerprint inputs are required");
  const canonicalRoot = await realpath(root);
  const hash = createHash("sha256");
  let files = 0;
  let bytes = 0;
  async function visit(file) {
    const metadata = await lstat(file);
    if (metadata.isSymbolicLink()) throw new Error("Symbolic-link inputs cannot be cached");
    const relative = path.relative(canonicalRoot, file).replaceAll("\\", "/");
    hash.update(relative);
    if (metadata.isDirectory()) {
      hash.update("directory");
      for (const entry of (await readdir(file, { withFileTypes: true })).sort(
        (left, right) => left.name.localeCompare(right.name),
      )) {
        if (EXCLUDED.has(entry.name)) continue;
        const childRelative = path.relative(canonicalRoot, path.join(file, entry.name)).replaceAll("\\", "/");
        if (childRelative === ".github/migrate-oracle-app") continue;
        await visit(path.join(file, entry.name));
      }
    } else if (metadata.isFile()) {
      files += 1;
      bytes += metadata.size;
      if (files > 50000 || bytes > 100000000) throw new Error("Manifest fingerprint scan budget exceeded");
      hash.update("file");
      hash.update(await readFile(file));
    } else {
      throw new Error("Unsupported manifest input kind");
    }
  }
  for (const input of [...new Set(inputs)].sort()) {
    const file = contained(canonicalRoot, input);
    await rejectSymlinkAncestors(canonicalRoot, file);
    await visit(file);
  }
  return { digest: hash.digest("hex"), files, bytes };
}

export async function javaBuildInputs(root) {
  const result = [];
  let entriesRead = 0;
  async function visit(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      entriesRead += 1;
      if (entriesRead > 50000) throw new Error("Java manifest discovery budget exceeded");
      if (EXCLUDED.has(entry.name) || entry.name === ".github") continue;
      const file = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) throw new Error("Java build discovery does not follow symbolic links");
      if (entry.isDirectory()) await visit(file);
      else if (entry.isFile() &&
        (entry.name === "pom.xml" || entry.name === ".java-version" ||
          entry.name === ".sdkmanrc" || path.relative(root, file).split(path.sep).includes(".mvn"))) {
        result.push(path.relative(root, file));
      }
    }
  }
  await visit(root);
  if (!result.some((file) => path.basename(file) === "pom.xml")) {
    throw new Error("Java pre-generation gate requires Maven build evidence");
  }
  return result.sort();
}

export async function prepareJavaTestGeneration(runtime, config = {}) {
  requireLease(runtime);
  const { detectJavaTestRelease, validateJavaTestFiles } = await import("./java-test-preflight.mjs");
  const { fingerprintJavaBuildContext } = await import("./java-build-context.mjs");
  const context = await fingerprintJavaBuildContext({ ...config, root: runtime.root });
  const inputs = [...new Set([
    ...(config.inputs ?? await javaBuildInputs(runtime.root)),
    ...context.buildInputs,
  ])].sort();
  const contextDigest = digestJson([runtime.runId, context.contextDigest]);
  const outputDirectory = config.outputDirectory
    ? contained(runtime.runDirectory, path.isAbsolute(config.outputDirectory)
      ? path.relative(runtime.runDirectory, config.outputDirectory) : config.outputDirectory)
    : path.join(runtime.runDirectory, "java-preflight");
  const request = { kind: "java-release", key: "test-generation", inputs, contextDigest };
  const cached = await getManifest(runtime, request);
  let evidence = cached.data;
  if (cached.status !== "hit") {
    const before = await fingerprintInputs(runtime.root, inputs);
    evidence = await detectJavaTestRelease({
      ...config, root: runtime.root,
      outputDirectory,
    });
    const after = await fingerprintInputs(runtime.root, inputs);
    if (before.digest !== after.digest) throw new Error("Java build inputs changed during release preflight");
    const currentContext = await fingerprintJavaBuildContext({ ...config, root: runtime.root });
    if (currentContext.contextDigest !== context.contextDigest) {
      throw new Error("Java build environment changed during release preflight");
    }
    await putManifest(runtime, {
      ...request, expectedInputDigest: cached.inputDigest, data: evidence,
    });
  }
  const validation = await validateJavaTestFiles({
    root: runtime.root, files: config.files ?? [], evidence,
  });
  await appendRecord(runtime, {
    type: "java-generation-gate", inputDigest: cached.inputDigest,
    evidenceDigest: digestJson(evidence), cacheHit: cached.status === "hit",
    modules: evidence.modules.map(({ modulePath, mainRelease, testRelease }) => ({
      modulePath, mainRelease, testRelease,
    })),
    validatedFiles: validation.files,
  });
  return { status: "passed", cacheHit: cached.status === "hit",
    inputDigest: cached.inputDigest, contextDigest, evidence, validatedFiles: validation.files };
}

export async function openMigrationRuntime({
  root,
  runId,
  actorId,
  runtimeDirectory,
  initialize = false,
}) {
  requiredString(runId, "runId");
  requiredString(actorId, "actorId");
  const canonicalRoot = await realpath(root);
  let directory = runtimeDirectory;
  if (!directory) {
    const git = runProcessCommand(canonicalRoot, "git", [
      "--no-pager", "rev-parse", "--show-toplevel", "--absolute-git-dir",
    ]);
    if (git.status !== 0) throw new Error("Migration runtime requires an existing Git worktree");
    const [worktree, metadataDirectory] = git.stdout.trim().split(/\r?\n/);
    if (!worktree || !metadataDirectory || await realpath(path.normalize(worktree)) !== canonicalRoot) {
      throw new Error("Migration runtime codebase must equal the canonical Git worktree root");
    }
    directory = path.join(path.normalize(metadataDirectory), "migrate-oracle-app-runtime");
  }
  directory = path.resolve(directory);
  const runDirectory = path.join(directory, "runs", digestJson(runId));
  const ownerPath = path.join(runDirectory, "owner.json");
  if (initialize) {
    if (actorId !== OWNER) throw new Error("Only the primary may initialize migration execution ownership");
    await mkdir(runDirectory, { recursive: true });
    const owner = await readJsonOrMissing(ownerPath);
    if (!owner) {
      const handle = await open(ownerPath, "wx", 0o600);
      try {
        await handle.writeFile(JSON.stringify({
          schemaVersion: 1, runId, actorId: OWNER, rootDigest: digestJson(canonicalRoot),
        }));
        await handle.sync();
      } finally {
        await handle.close();
      }
    }
  }
  const owner = await readJsonOrMissing(ownerPath);
  if (!owner || owner.runId !== runId || owner.rootDigest !== digestJson(canonicalRoot) ||
    owner.actorId !== OWNER || actorId !== owner.actorId) {
    throw new Error("Migration execution is owned by the registered primary actor");
  }
  return { root: canonicalRoot, runId, actorId, directory, runDirectory };
}

export async function withPrimaryExecution(runtime, kind, work) {
  if (runtime.actorId !== OWNER) throw new Error("Only the primary may execute build/Docker/artifact work");
  requiredString(kind, "execution kind");
  const owner = await readJsonOrMissing(path.join(runtime.runDirectory, "owner.json"));
  if (owner?.actorId !== runtime.actorId || owner.runId !== runtime.runId ||
    owner.rootDigest !== digestJson(runtime.root)) throw new Error("Execution owner registration does not match");
  const lock = path.join(runtime.directory, "execution.lock");
  const lease = {
    schemaVersion: 1, runId: runtime.runId, actorId: runtime.actorId,
    kind, pid: process.pid, nonce: randomUUID(), startedAt: new Date().toISOString(),
  };
  let handle;
  try {
    handle = await open(lock, "wx", 0o600);
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    throw new Error("The workspace execution lease is already held; do not start another Maven/Docker setup");
  }
  try {
    await handle.writeFile(JSON.stringify(lease));
    await handle.sync();
    runtime.activeLease = lease.nonce;
    return await work({
      runId: lease.runId, actorId: lease.actorId, kind, startedAt: lease.startedAt,
      leaseDigest: digestJson(lease),
    });
  } finally {
    delete runtime.activeLease;
    await handle.close();
    const recorded = await readJsonOrMissing(lock);
    if (recorded?.nonce !== lease.nonce) throw new Error("Execution lease ownership changed; refusing to remove it");
    await unlink(lock);
  }
}

function requireLease(runtime) {
  if (runtime.actorId !== OWNER || !runtime.activeLease) {
    throw new Error("A live primary execution lease is required for runtime mutations");
  }
}

async function journalRecords(runtime) {
  const file = path.join(runtime.runDirectory, "checkpoints.jsonl");
  let text;
  try {
    text = await readFile(file, "utf8");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    return [];
  }
  if (text && !text.endsWith("\n")) throw new Error("Incomplete runtime checkpoint; explicit recovery is required");
  let previous = null;
  return text.split("\n").filter(Boolean).map((line, index) => {
    let record;
    try { record = JSON.parse(line); } catch (error) {
      if (!(error instanceof SyntaxError)) throw error;
      throw new Error("Corrupt migration checkpoint journal");
    }
    const { digest, ...payload } = record;
    assertJson(payload);
    if (record.sequence !== index + 1 || record.previousDigest !== previous ||
      digestJson(payload) !== digest || record.runId !== runtime.runId) {
      throw new Error("Migration checkpoint integrity verification failed");
    }
    previous = digest;
    return record;
  });
}

async function appendRecord(runtime, event) {
  requireLease(runtime);
  assertJson(event);
  const records = await journalRecords(runtime);
  const payload = {
    sequence: records.length + 1, previousDigest: records.at(-1)?.digest ?? null,
    runId: runtime.runId, actorId: runtime.actorId, timestamp: new Date().toISOString(), ...event,
  };
  const record = { ...payload, digest: digestJson(payload) };
  const handle = await open(path.join(runtime.runDirectory, "checkpoints.jsonl"), "a", 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(record)}\n`);
    await handle.sync();
  } finally {
    await handle.close();
  }
  return record;
}

export async function getManifest(runtime, { kind, key, inputs, contextDigest = null }) {
  if (!CACHE_KINDS.has(kind)) throw new Error("Only source, code-derived target, and Java manifests may be cached");
  requiredString(key, "cache key");
  if (kind === "target-objects") requiredString(contextDigest, "fresh target context digest");
  const notesDigest = await manifestNotesDigest(runtime, kind);
  const fingerprint = await fingerprintInputs(runtime.root, inputs);
  const inputDigest = notesDigest ? digestJson([fingerprint.digest, notesDigest]) : fingerprint.digest;
  const file = path.join(runtime.directory, "cache", `${digestJson([kind, key])}.json`);
  const entry = await readJsonOrMissing(file);
  if (!entry) return { status: "miss", reason: "not-cached", inputDigest };
  assertJson(entry.data);
  if (entry.kind !== kind || entry.key !== key || entry.rootDigest !== digestJson(runtime.root) ||
    digestJson(entry.data) !== entry.dataDigest) throw new Error("Manifest cache integrity verification failed");
  if (entry.inputDigest !== inputDigest || entry.contextDigest !== contextDigest) {
    return { status: "miss", reason: "inputs-or-context-changed", inputDigest };
  }
  return { status: "hit", inputDigest, dataDigest: entry.dataDigest, data: entry.data };
}

async function manifestNotesDigest(runtime, kind) {
  if (kind === "java-release") return null;
  const snapshot = await replayMigrationState(runtime);
  const notes = snapshot.conversion.state.codingNotes ?? snapshot.assessment.state.codingNotes;
  if (!notes) throw new Error("Checkpoint codingNotes (including not-supplied) before notes-sensitive manifest lookup");
  await verifyCodingNotes(runtime.root, notes);
  return codingNotesInputDigest(notes);
}

export async function putManifest(runtime, {
  kind, key, inputs, contextDigest = null, expectedInputDigest, data,
}) {
  requireLease(runtime);
  assertJson(data);
  requiredString(expectedInputDigest, "expectedInputDigest from manifest lookup");
  const current = await getManifest(runtime, { kind, key, inputs, contextDigest });
  if (current.inputDigest !== expectedInputDigest) {
    throw new Error("Manifest inputs changed after lookup; refresh analysis instead of caching stale findings");
  }
  if (current.status === "hit" && current.dataDigest === digestJson(data)) return current;
  const entry = {
    schemaVersion: 1, kind, key, rootDigest: digestJson(runtime.root),
    inputDigest: current.inputDigest, contextDigest, dataDigest: digestJson(data), data,
  };
  await atomicJson(path.join(runtime.directory, "cache", `${digestJson([kind, key])}.json`), entry);
  await appendRecord(runtime, {
    type: "manifest", kind, key, inputDigest: entry.inputDigest, dataDigest: entry.dataDigest,
  });
  return { status: "stored", inputDigest: entry.inputDigest, dataDigest: entry.dataDigest };
}

function applyOperation(state, operation) {
  if (!["set", "append", "delete"].includes(operation?.op) ||
    !Array.isArray(operation.path) || operation.path.length === 0 ||
    operation.path.some((key) => typeof key !== "string" || !key || UNSAFE_KEYS.has(key))) {
    throw new TypeError("Checkpoint operations require a safe path and set/append/delete operation");
  }
  let parent = state;
  for (const key of operation.path.slice(0, -1)) {
    parent[key] ??= {};
    if (!parent[key] || typeof parent[key] !== "object" || Array.isArray(parent[key])) {
      throw new Error("Checkpoint path does not refer to an object");
    }
    parent = parent[key];
  }
  const key = operation.path.at(-1);
  if (operation.op === "delete") delete parent[key];
  else if (operation.op === "set") parent[key] = structuredClone(operation.value);
  else {
    if (!Array.isArray(operation.values)) throw new TypeError("Append checkpoint requires values");
    parent[key] ??= [];
    if (!Array.isArray(parent[key])) throw new Error("Append checkpoint path must be an array");
    parent[key].push(...structuredClone(operation.values));
  }
}

export async function replayMigrationState(runtime, maximumSequence = Infinity) {
  const result = {
    assessment: { state: {}, sections: {}, headerBindings: {} },
    conversion: { state: {}, sections: {}, headerBindings: {} },
  };
  const records = await journalRecords(runtime);
  for (const record of records.filter((entry) => entry.sequence <= maximumSequence)) {
    if (record.type !== "checkpoint") continue;
    const phase = result[record.phase];
    for (const operation of record.operations) applyOperation(phase.state, operation);
    Object.assign(phase.sections, record.sections);
    Object.assign(phase.headerBindings, record.headerBindings);
  }
  return { ...result, sequence: records.length, journalDigest: records.at(-1)?.digest ?? null };
}

function normalizedState(value) {
  const copy = structuredClone(value);
  if (copy.validation) {
    delete copy.validation.artifactValidation;
    if (Object.keys(copy.validation).length === 0) delete copy.validation;
  }
  const canonical = (item) => Array.isArray(item) ? item.map(canonical)
    : item && typeof item === "object"
      ? Object.fromEntries(Object.keys(item).sort().map((key) => [key, canonical(item[key])]))
      : item;
  return digestJson(canonical(copy));
}

export async function verifyMigrationBoundary({
  root, phase, state, report, runtimeDirectory,
}) {
  if (!PHASES.has(phase)) throw new TypeError("Runtime boundary phase is invalid");
  const evidence = state?.runtimeExecution;
  if (
    evidence?.schemaVersion !== 1 ||
    evidence.primaryActorId !== OWNER ||
    !Number.isInteger(evidence.checkpointSequence) ||
    evidence.checkpointSequence < 1 ||
    typeof evidence.journalDigest !== "string"
  ) {
    throw new Error("Final artifact runtimeExecution proof is required");
  }
  const runtime = await openMigrationRuntime({
    root, runId: evidence.runId, actorId: evidence.primaryActorId, runtimeDirectory,
  });
  const records = await journalRecords(runtime);
  if (records[evidence.checkpointSequence - 1]?.digest !== evidence.journalDigest) {
    throw new Error("Final artifact checkpoint digest does not match its journal");
  }
  const boundary = records.find((record) =>
    record.sequence === evidence.checkpointSequence + 1 &&
    record.type === "artifact-boundary" && record.phases.includes(phase),
  );
  if (!boundary || records.some((record) =>
    record.sequence > boundary.sequence && record.type === "checkpoint" && record.phase === phase,
  )) {
    throw new Error("Final artifact boundary is missing or stale");
  }
  const boundaryKind = boundary.phases.length === 2 ? "combined-final" : `${phase}-final`;
  if (
    boundary.phases.length < 1 || boundary.phases.length > 2 ||
    new Set(boundary.phases).size !== boundary.phases.length ||
    boundary.phases.some((item) => !PHASES.has(item)) ||
    evidence.boundary !== boundaryKind
  ) {
    throw new Error("Final artifact boundary kind/phases do not match the assembly receipt");
  }
  const snapshot = await replayMigrationState(runtime, evidence.checkpointSequence);
  const expectedState = {
    ...snapshot[phase].state,
    runtimeExecution: {
      schemaVersion: 1, primaryActorId: runtime.actorId, runId: runtime.runId,
      checkpointSequence: evidence.checkpointSequence, journalDigest: evidence.journalDigest,
      boundary: boundaryKind,
    },
  };
  if (normalizedState(expectedState) !== normalizedState(state)) {
    throw new Error("Final machine state does not match the checkpointed evidence");
  }
  const folder = phase === "assessment" ? "assessment" : "conversions";
  const reportName = phase === "assessment" ? "assessment-report.md" : "conversion-report.md";
  const stateName = phase === "assessment" ? "assessment-data.json" : "conversion-state.json";
  const stateRelative = path.join(".github", "migrate-oracle-app", folder, stateName);
  const stateReceipt = boundary.artifacts.find((artifact) => artifact.path === stateRelative);
  const assembledStateDigest = createHash("sha256")
    .update(`${JSON.stringify(expectedState, null, 2)}\n`).digest("hex");
  if (!stateReceipt || stateReceipt.digest !== assembledStateDigest) {
    throw new Error("Final state assembly receipt does not match the checkpointed state");
  }
  const relative = path.join(".github", "migrate-oracle-app", folder, reportName);
  const receipt = boundary.artifacts.find((artifact) => artifact.path === relative);
  const reportDigest = createHash("sha256").update(report).digest("hex");
  if (!receipt || receipt.digest !== reportDigest) {
    throw new Error("Final report does not match the primary assembly receipt");
  }
  return { status: "verified", primaryActorId: OWNER, runId: runtime.runId,
    checkpointSequence: evidence.checkpointSequence, boundarySequence: boundary.sequence,
    journalDigest: evidence.journalDigest, reportDigest };
}

export async function checkpointMigration(runtime, {
  phase, operations = [], sections = {}, headerBindings = {},
}) {
  requireLease(runtime);
  if (!PHASES.has(phase)) throw new TypeError("Checkpoint phase must be assessment or conversion");
  assertJson({ operations, sections, headerBindings });
  const snapshot = await replayMigrationState(runtime);
  for (const operation of operations) applyOperation(snapshot[phase].state, operation);
  for (const [anchor, body] of Object.entries(sections)) {
    if (!/^[a-z0-9-]+$/.test(anchor) || typeof body !== "string") {
      throw new TypeError("Report sections require stable anchors and Markdown bodies");
    }
  }
  for (const [binding, value] of Object.entries(headerBindings)) {
    if (!/^<[^<>]+>$/.test(binding) || typeof value !== "string") {
      throw new TypeError("Header bindings require exact template placeholders and strings");
    }
  }
  return appendRecord(runtime, { type: "checkpoint", phase, operations, sections, headerBindings });
}

export function assembleMigrationReport(template, { sections, headerBindings, state }) {
  const boundaries = [...template.matchAll(/^<a id="([^"]+)"><\/a>\r?\n## ([^\r\n]+)\r?$/gm)];
  if (boundaries.length === 0) throw new Error("Report template has no canonical section anchors");
  const expected = new Set(boundaries.map((match) => match[1]));
  for (const key of Object.keys(sections)) {
    if (!expected.has(key)) throw new Error(`Unknown report section: ${key}`);
  }
  let prefix = template.slice(0, boundaries[0].index);
  for (const [placeholder, value] of Object.entries(headerBindings)) {
    prefix = prefix.replaceAll(placeholder, value);
  }
  if (/`<[^>\r\n]+>`/.test(prefix)) throw new Error("Report header template bindings remain unresolved");
  const bodies = boundaries.map((match) => {
    const [_, anchor, heading] = match;
    if (anchor === "coding-notes-processing") {
      const body = renderCodingNotes(state.codingNotes);
      if (sections[anchor] !== undefined && sections[anchor].trim() !== body) {
        throw new Error("Coding Notes Processing is generated centrally from machine evidence");
      }
      return `<a id="${anchor}"></a>\n## ${heading}\n\n${body}\n\n[Back to Top](#top)`;
    }
    if (typeof sections[anchor] !== "string" || !sections[anchor].trim()) {
      throw new Error(`Final report section is missing: ${anchor}`);
    }
    let body = sections[anchor].trim();
    if (/^## /m.test(body) || /^\[Back to Top\]/m.test(body)) {
      throw new Error("Section fragments must not duplicate top-level headings or navigation");
    }
    if (/Appendix/.test(heading)) {
      if (/^### Run Metrics$/m.test(body)) throw new Error("Run Metrics are generated centrally, not checkpointed as prose");
      body += `\n\n${renderRunMetrics(state.runMetrics)}`;
    }
    return `<a id="${anchor}"></a>\n## ${heading}\n\n${body}\n\n[Back to Top](#top)`;
  });
  return `${prefix.replace(/\r\n/g, "\n").trimEnd()}\n\n${bodies.join("\n\n")}\n`;
}

export async function finalizeMigrationArtifacts(runtime, {
  phases, templates, expectedArtifacts = {}, validateArtifacts = true,
  commandRunner = runProcessCommand,
}) {
  requireLease(runtime);
  if (!Array.isArray(phases) || phases.length === 0 || phases.some((phase) => !PHASES.has(phase))) {
    throw new TypeError("Finalization requires explicit assessment/conversion phases");
  }
  const snapshot = await replayMigrationState(runtime);
  const outputs = [];
  if (phases.length === 2 &&
    codingNotesInputDigest(snapshot.assessment.state.codingNotes) !==
      codingNotesInputDigest(snapshot.conversion.state.codingNotes)) {
    throw new Error("Combined reports require the same current coding notes input snapshot");
  }
  for (const phase of phases) {
    const model = snapshot[phase];
    await verifyCodingNotes(runtime.root, model.state.codingNotes, { phase, status: model.state.status });
    const template = await readFile(requiredString(templates[phase], `${phase} template`), "utf8");
    if (!/^<a id="coding-notes-processing"><\/a>\r?\n## Coding Notes Processing\r?$/m.test(template)) {
      throw new Error("Final report template requires Coding Notes Processing");
    }
    const report = assembleMigrationReport(template, model);
    const folder = phase === "assessment" ? "assessment" : "conversions";
    const stateName = phase === "assessment" ? "assessment-data.json" : "conversion-state.json";
    const reportName = phase === "assessment" ? "assessment-report.md" : "conversion-report.md";
    const state = structuredClone(model.state);
    state.runtimeExecution = {
      schemaVersion: 1, primaryActorId: runtime.actorId, runId: runtime.runId,
      checkpointSequence: snapshot.sequence, journalDigest: snapshot.journalDigest,
      boundary: phases.length === 2 ? "combined-final" : `${phase}-final`,
    };
    for (const [name, content] of [
      [stateName, `${JSON.stringify(state, null, 2)}\n`],
      [reportName, report],
    ]) {
      const relative = path.join(".github", "migrate-oracle-app", folder, name);
      const file = contained(runtime.root, relative);
      await rejectSymlinkAncestors(runtime.root, file, true);
      let existing = null;
      try { existing = await readFile(file); } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
      if (existing && expectedArtifacts[relative] !== createHash("sha256").update(existing).digest("hex")) {
        throw new Error(`Artifact ownership/digest approval is required before replacing ${name}`);
      }
      outputs.push({
        file, relative, content,
        previousDigest: existing ? createHash("sha256").update(existing).digest("hex") : null,
        digest: createHash("sha256").update(content).digest("hex"),
      });
    }
  }
  const staged = [];
  try {
    for (const output of outputs) {
      await mkdir(path.dirname(output.file), { recursive: true });
      const temporary = `${output.file}.${randomUUID()}.tmp`;
      const handle = await open(temporary, "wx", 0o600);
      try { await handle.writeFile(output.content); await handle.sync(); } finally { await handle.close(); }
      staged.push({ ...output, temporary });
    }
    for (const output of staged) {
      await rejectSymlinkAncestors(runtime.root, output.file, true);
      let current = null;
      try { current = await readFile(output.file); } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
      const currentDigest = current ? createHash("sha256").update(current).digest("hex") : null;
      if (currentDigest !== output.previousDigest) {
        throw new Error("Final artifact changed outside the primary during staging; refusing to overwrite it");
      }
    }
    for (const output of staged) await rename(output.temporary, output.file);
  } finally {
    for (const output of staged) {
      try { await unlink(output.temporary); } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
    }
  }
  const receipt = await appendRecord(runtime, {
    type: "artifact-boundary", phases,
    artifacts: outputs.map(({ relative, digest }) => ({ path: relative, digest })),
  });
  if (validateArtifacts) {
    const validator = path.join(path.dirname(fileURLToPath(import.meta.url)), "validate-migration-artifacts.mjs");
    const mode = phases.length === 2 ? "both" : phases[0];
    for (const record of [true, false]) {
      const result = await commandRunner(runtime.root, process.execPath, [
        validator, runtime.root, mode, "--require-runtime",
        "--runtime-directory", runtime.directory,
        ...(record ? ["--record"] : []),
      ]);
      if (result.status !== 0) {
        throw new Error(`Final artifact ${record ? "record" : "verify"} failed: ${result.stderr || result.stdout}`);
      }
    }
    for (const output of outputs) {
      output.digest = createHash("sha256").update(await readFile(output.file)).digest("hex");
    }
  }
  return { checkpointSequence: snapshot.sequence, boundarySequence: receipt.sequence,
    artifacts: outputs.map(({ relative, digest }) => ({ path: relative, digest })) };
}

async function main() {
  const [command, root, configFile] = process.argv.slice(2);
  if (!["init", "checkpoint", "replay", "cache-get", "cache-put", "java-preflight", "finalize"].includes(command) ||
    !root || !configFile) throw new Error("Usage: node migration-runtime.mjs <command> <codebase> <config.json>");
  const config = JSON.parse(await readFile(path.resolve(configFile), "utf8"));
  if (command === "finalize" && config.validateArtifacts === false) {
    throw new Error("CLI finalization must run artifact record and independent verification");
  }
  const runtime = await openMigrationRuntime({ ...config, root, initialize: command === "init" });
  const result = command === "init" ? { status: "initialized", runId: runtime.runId }
    : command === "replay" ? await replayMigrationState(runtime)
    : await withPrimaryExecution(runtime, command, async () => {
      if (command === "checkpoint") return checkpointMigration(runtime, config);
      if (command === "cache-get") return getManifest(runtime, config);
      if (command === "cache-put") return putManifest(runtime, config);
      if (command === "java-preflight") return prepareJavaTestGeneration(runtime, config);
      return finalizeMigrationArtifacts(runtime, config);
    });
  console.log(JSON.stringify(result, null, 2));
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) {
  await main();
}
