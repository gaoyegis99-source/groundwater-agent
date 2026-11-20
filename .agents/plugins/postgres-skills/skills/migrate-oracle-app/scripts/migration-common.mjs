import { createHash } from "node:crypto";
import { lstat, mkdir, open, unlink } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

export const MIGRATION_SKILLS = Object.freeze({
  assessment: "oracle-to-postgresql-app-assessment",
  java: "migration-oracle-to-postgresql-app-java",
  dotnet: "migration-oracle-to-postgresql-app-dotnet",
});

export function integrationError(message, code = "MIGRATION_INVALID_INPUT") {
  return Object.assign(new Error(message), { code });
}

export function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

export function isWithin(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!path.isAbsolute(relative) &&
    relative !== ".." && !relative.startsWith(`..${path.sep}`));
}

export function requireText(value, label) {
  if (typeof value !== "string" || !value.trim() ||
    value.length > 16_384 || /[\u0000-\u001f\u007f]/u.test(value)) {
    throw integrationError(`${label} must be a nonempty, single-line string.`);
  }
  return value;
}

export function createBudget({ maxDurationMs = 30_000, signal } = {}) {
  if (!Number.isSafeInteger(maxDurationMs) || maxDurationMs < 1 ||
    maxDurationMs > 600_000) {
    throw integrationError("The execution budget must be between 1 and 600000 milliseconds.");
  }
  const started = performance.now();
  return {
    signal,
    check() {
      if (signal?.aborted) {
        throw integrationError("Execution was cancelled.", "MIGRATION_CANCELLED");
      }
      if (performance.now() - started >= maxDurationMs) {
        throw integrationError("The bounded execution budget was exhausted.", "MIGRATION_TIMEOUT");
      }
    },
    elapsedMs() {
      return Math.round(performance.now() - started);
    },
  };
}

export async function inspectPath(candidate, { allowMissing = false } = {}) {
  const absolute = path.resolve(candidate);
  const parsed = path.parse(absolute);
  let current = parsed.root;
  let metadata;
  for (const segment of absolute.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, segment);
    try {
      metadata = await lstat(current);
    } catch (error) {
      if (allowMissing && error.code === "ENOENT") return null;
      throw integrationError(`A required path is unavailable (${error.code ?? "filesystem error"}).`);
    }
    if (metadata.isSymbolicLink()) {
      throw integrationError("Filesystem links are not permitted for integration inputs or outputs.");
    }
  }
  return metadata ?? await lstat(absolute);
}

export async function requireDirectory(candidate, label) {
  const absolute = path.resolve(requireText(candidate, label));
  const metadata = await inspectPath(absolute);
  if (!metadata.isDirectory()) throw integrationError(`${label} must be a regular directory.`);
  return absolute;
}

export async function readLimitedText(candidate, maximum, budget) {
  budget?.check();
  const metadata = await inspectPath(candidate);
  if (!metadata.isFile() || metadata.size > maximum) {
    throw integrationError(`An input must be a regular file of at most ${maximum} bytes.`);
  }
  const handle = await open(candidate, "r");
  try {
    const before = await handle.stat();
    const chunks = [];
    let bytes = 0;
    while (true) {
      budget?.check();
      const buffer = Buffer.alloc(Math.min(64 * 1024, maximum - bytes + 1));
      const result = await handle.read(buffer, 0, buffer.length, null);
      if (!result.bytesRead) break;
      bytes += result.bytesRead;
      if (bytes > maximum) throw integrationError("An input exceeded its read budget.");
      chunks.push(buffer.subarray(0, result.bytesRead));
    }
    const after = await handle.stat();
    if (before.size !== after.size || before.mtimeMs !== after.mtimeMs) {
      throw integrationError("An input changed while it was being read; retry with a stable snapshot.");
    }
    const buffer = Buffer.concat(chunks);
    let text;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
    } catch {
      throw integrationError("Integration text inputs must be valid UTF-8.");
    }
    return { text, bytes, sha256: sha256(buffer) };
  } finally {
    await handle.close();
  }
}

export async function readAdapterVersion() {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
  const bundle = path.join(root, "bundle.json");
  const source = await inspectPath(bundle, { allowMissing: true })
    ? bundle : path.join(root, "plugin.json");
  const { text } = await readLimitedText(source, 1_000_000);
  const metadata = JSON.parse(text);
  const version = metadata.sourceVersion ?? metadata.version;
  if (typeof version !== "string" || !/^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$/.test(version)) {
    throw integrationError("The source plugin version is unavailable.");
  }
  return version;
}

export async function writeNewText(directory, name, text) {
  if (path.basename(name) !== name || name === "." || name === "..") {
    throw integrationError("An output name must be a direct-child filename.");
  }
  await inspectPath(directory, { allowMissing: true });
  await mkdir(directory, { recursive: true });
  await inspectPath(directory);
  const output = path.join(directory, name);
  let handle;
  try {
    handle = await open(output, "wx", 0o600);
  } catch (error) {
    if (error.code === "EEXIST") {
      throw integrationError("The output already exists; use an empty output directory.");
    }
    throw error;
  }
  try {
    await handle.writeFile(text, "utf8");
  } catch (error) {
    await handle.close();
    handle = null;
    await unlink(output);
    throw error;
  } finally {
    await handle?.close();
  }
  return output;
}

export async function validateOutputDirectory(workspace, directory, protectedRoots = []) {
  const output = path.resolve(workspace, requireText(directory, "output directory"));
  if (isWithin(output, workspace)) {
    throw integrationError("The output directory cannot be the application workspace or its ancestor.");
  }
  for (const root of protectedRoots.filter(Boolean)) {
    if (isWithin(root, output) || isWithin(output, root)) {
      throw integrationError("Outputs must not overlap read-only DDL inputs or the skill package.");
    }
  }
  const metadata = await inspectPath(output, { allowMissing: true });
  if (metadata && !metadata.isDirectory()) throw integrationError("The output path must be a directory.");
  return output;
}

export function parseArguments(argv, allowed, required = []) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    if (!allowed.includes(key) || Object.hasOwn(values, key)) {
      throw integrationError("An unknown or repeated command-line argument was supplied.");
    }
    values[key] = requireText(argv[index + 1], `${key} value`);
    if (values[key].startsWith("--")) throw integrationError(`${key} requires a value.`);
  }
  for (const key of required) {
    if (!Object.hasOwn(values, key)) throw integrationError(`${key} is required.`);
  }
  return values;
}

export function isMain(url) {
  const normalize = (value) => process.platform === "win32" ? value.toLowerCase() : value;
  return process.argv[1] &&
    normalize(path.resolve(process.argv[1])) === normalize(fileURLToPath(url));
}

export async function runCli(action) {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  process.once("SIGINT", cancel);
  process.once("SIGTERM", cancel);
  try {
    if (Number(process.versions.node.split(".")[0]) < 22) {
      throw integrationError("Node.js 22 or newer is required.");
    }
    await action(controller.signal);
  } catch (error) {
    const reason = error.code?.startsWith("MIGRATION_")
      ? error.message
      : `Integration operation failed (${error.code ?? "unexpected error"}).`;
    process.stderr.write(`${reason}\n`);
    process.exitCode = 1;
  } finally {
    process.removeListener("SIGINT", cancel);
    process.removeListener("SIGTERM", cancel);
  }
}
