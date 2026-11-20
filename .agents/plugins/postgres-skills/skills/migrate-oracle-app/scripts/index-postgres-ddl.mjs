import { opendir } from "node:fs/promises";
import path from "node:path";
import {
  createBudget, integrationError, inspectPath, isMain, parseArguments,
  readLimitedText, requireDirectory, runCli, sha256, writeNewText,
} from "./migration-common.mjs";
import { normalizePgType, parsePostgresDdl, postgresObjectKey } from "./postgres-ddl-parser.mjs";

const EXTENSIONS = new Set([".sql", ".ddl", ".psql", ".pgsql", ".fnc", ".prc", ".vw", ".trg"]);
const NOTES = new Set([".md", ".txt"]);
const LIMITS = Object.freeze({
  maxFiles: 10_000,
  maxEntries: 30_000,
  maxDepth: 32,
  maxFileBytes: 8_000_000,
  maxTotalBytes: 64_000_000,
});

export async function indexPostgresDdlFolder(folder, options = {}) {
  const budget = options.budget ?? createBudget(options);
  const limits = { ...LIMITS };
  for (const key of Object.keys(limits)) {
    if (options[key] !== undefined) limits[key] = options[key];
    if (!Number.isSafeInteger(limits[key]) || limits[key] < 1) {
      throw integrationError("DDL traversal limits must be positive integers.");
    }
  }
  budget.check();
  const root = await requireDirectory(folder, "PostgreSQL DDL folder");
  const candidates = [];
  const limitations = [];
  let entriesVisited = 0;
  const walk = async (directory, depth) => {
    budget.check();
    if (depth > limits.maxDepth) throw integrationError("PostgreSQL DDL directory depth limit exceeded.");
    for await (const entry of await opendir(directory)) {
      budget.check();
      if (++entriesVisited > limits.maxEntries) throw integrationError("PostgreSQL DDL entry limit exceeded.");
      const absolute = path.join(directory, entry.name);
      const metadata = await inspectPath(absolute);
      const relativePath = path.relative(root, absolute).split(path.sep).join("/");
      if (metadata.isDirectory()) await walk(absolute, depth + 1);
      else if (!metadata.isFile()) throw integrationError("DDL snapshots may contain only regular files and directories.");
      else if (EXTENSIONS.has(path.extname(entry.name).toLowerCase())) {
        if (candidates.length >= limits.maxFiles) throw integrationError("PostgreSQL DDL file limit exceeded.");
        candidates.push({ absolute, relativePath });
      } else if (!NOTES.has(path.extname(entry.name).toLowerCase())) {
        throw integrationError("The DDL folder contains a non-DDL file; supply a dedicated schema-only export folder.");
      }
    }
  };
  await walk(root, 0);
  candidates.sort((left, right) => left.relativePath.localeCompare(right.relativePath));
  if (!candidates.length) throw integrationError("The PostgreSQL DDL folder contains no supported DDL files.");
  const files = [];
  const objects = [];
  const alterations = [];
  let totalBytes = 0;
  for (const candidate of candidates) {
    budget.check();
    const remaining = Math.min(limits.maxFileBytes, limits.maxTotalBytes - totalBytes);
    if (remaining < 1) throw integrationError("PostgreSQL DDL total byte limit exceeded.");
    const source = await readLimitedText(candidate.absolute, remaining, budget);
    totalBytes += source.bytes;
    const parsed = parsePostgresDdl(source.text, { budget });
    files.push({ relativePath: candidate.relativePath, sha256: source.sha256, bytes: source.bytes });
    for (const object of parsed.objects) {
      objects.push({ ...object, source: { relativePath: candidate.relativePath, sha256: source.sha256 } });
    }
    for (const alteration of parsed.alterations) {
      alterations.push({ ...alteration, relativePath: candidate.relativePath });
    }
    limitations.push(...parsed.limitations.map((entry) => ({ ...entry, relativePath: candidate.relativePath })));
  }
  const identities = new Set();
  for (const object of objects) {
    const key = postgresObjectKey(object);
    if (identities.has(key)) throw integrationError("The DDL snapshot contains duplicate or ambiguous object definitions.");
    identities.add(key);
  }
  for (const alteration of alterations) {
    const matches = objects.filter((object) => object.kind === "table" &&
      object.schema === alteration.schema && object.name === alteration.name);
    if (matches.length !== 1) {
      limitations.push({ relativePath: alteration.relativePath,
        reason: "An ALTER TABLE target was not uniquely declared in the snapshot." });
      continue;
    }
    for (const change of alteration.changes) {
      if (change.constraint) {
        matches[0].constraints.push(change.constraint);
        if (change.constraint.kind === "primary-key") {
          for (const column of matches[0].columns) {
            if (change.constraint.columns.includes(column.name)) column.nullable = false;
          }
        }
      } else if (change.column) {
        if (matches[0].columns.some((column) => column.name === change.column.name)) {
          throw integrationError("The DDL snapshot contains a duplicate column declaration.");
        }
        matches[0].columns.push(change.column);
      }
      if (change.limitation) {
        matches[0].limitations.push(change.limitation);
        limitations.push({ relativePath: alteration.relativePath, reason: change.limitation });
      }
    }
  }
  if (!objects.length) throw integrationError("No supported PostgreSQL object definitions were found.");
  objects.sort((left, right) => postgresObjectKey(left).localeCompare(postgresObjectKey(right)));
  budget.check();
  return {
    schemaVersion: 1,
    evidenceKind: "offline-postgresql-ddl-snapshot",
    status: limitations.length || objects.some((object) => object.limitations.length) ? "PARTIAL" : "INDEXED",
    liveTargetVerified: false,
    absenceMeans: "not-found-in-supplied-snapshot",
    snapshotSha256: sha256(JSON.stringify(files)),
    files, objects, limitations, totalBytes, limits,
  };
}

export function matchPostgresObjects(index, references) {
  return references.map((reference) => {
    let candidates = (index?.objects ?? []).filter((object) =>
      object.name === reference.name &&
      (reference.schema == null || object.schema == null || object.schema === reference.schema) &&
      (reference.kind === "relation" ? ["table", "view", "materialized-view"].includes(object.kind) :
        reference.kind === "routine" ? ["function", "procedure"].includes(object.kind) :
          reference.kind === object.kind));
    if (reference.identityArguments) {
      const identity = reference.identityArguments.map(normalizePgType);
      candidates = candidates.filter((object) =>
        JSON.stringify(object.identityArguments) === JSON.stringify(identity));
    }
    return {
      reference,
      status: !index ? "not-supplied" : candidates.length === 0 ? "not-found-in-supplied-snapshot" :
        candidates.length === 1 && candidates[0].schema != null ? "snapshot-candidate" : "ambiguous",
      candidates,
    };
  });
}

if (isMain(import.meta.url)) {
  await runCli(async (signal) => {
    const args = parseArguments(process.argv.slice(2), ["--folder", "--output"], ["--folder"]);
    const index = await indexPostgresDdlFolder(args["--folder"], { signal });
    const json = `${JSON.stringify(index, null, 2)}\n`;
    if (args["--output"]) {
      const output = path.resolve(args["--output"]);
      await writeNewText(path.dirname(output), path.basename(output), json);
    } else process.stdout.write(json);
  });
}
