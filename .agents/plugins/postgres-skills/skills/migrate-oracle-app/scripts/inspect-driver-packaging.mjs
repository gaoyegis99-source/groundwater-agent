import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  mkdtemp,
  readFile,
  rm,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { parseXmlDocument } from "./java-test-preflight.mjs";

export async function inspectDriverPackaging(rootPath, config) {
  validateConfig(config);
  const root = path.resolve(rootPath);
  const archives = [];
  const errors = [];
  for (const archiveConfig of config.archives) {
    const archivePath = path.resolve(root, archiveConfig.path);
    const entriesResult = listArchive(archivePath, config.jarExecutable);
    if (entriesResult.status !== 0) {
      errors.push(`${archiveConfig.path}: ${entriesResult.stderr}`);
      continue;
    }
    const entries = entriesResult.entries;
    const descriptorResults = [];
    const archiveDriverEntries = entries.filter(
      (entry) =>
        isPostgresqlDriverEntry(entry) && !isApplicationInternalPath(entry),
    );
    const nestedInspection = await inspectNestedWars(
      archivePath,
      entries,
      config.jarExecutable,
    );
    errors.push(
      ...nestedInspection.errors.map(
        (error) => `${archiveConfig.path}: ${error}`,
      ),
    );
    const nestedWarDrivers = nestedInspection.drivers;
    for (const descriptor of archiveConfig.descriptors) {
      const previousErrorCount = errors.length;
      const sourcePath = path.resolve(root, descriptor.sourcePath);
      let sourceContent;
      try {
        sourceContent = await readFile(sourcePath, "utf8");
      } catch (error) {
        errors.push(
          `${archiveConfig.path}: source descriptor ${descriptor.sourcePath} is missing or unreadable (${error.message})`,
        );
        continue;
      }
      const sourceXml = runtimeDescriptor(sourceContent, `${descriptor.sourcePath} (source)`, errors);
      if (!sourceXml) continue;
      const sourceFilesets = externalPostgresqlFilesets(sourceXml);
      if (sourceFilesets.length === 0) {
        errors.push(
          `${archiveConfig.path}: source descriptor ${descriptor.sourcePath} has no external PostgreSQL fileset declaration`,
        );
      }
      const descriptorEntry = entries.find(
        (entry) => normalize(entry) === normalize(descriptor.entry),
      );
      if (!descriptorEntry) {
        errors.push(
          `${archiveConfig.path}: missing runtime descriptor ${descriptor.entry}`,
        );
        continue;
      }
      const content = await readArchiveText(
        archivePath,
        descriptorEntry,
        config.jarExecutable,
      );
      const packagedXml = runtimeDescriptor(content, `${descriptor.entry} (packaged)`, errors);
      if (!packagedXml) continue;
      const filesets = externalPostgresqlFilesets(packagedXml);
      const descriptorReferencesPostgresql = filesets.length > 0;
      if (filesets.length === 0) {
        errors.push(
          `${archiveConfig.path}: ${descriptor.entry} has no external PostgreSQL fileset declaration`,
        );
      }
      if (!sameFilesets(sourceFilesets, filesets)) {
        errors.push(
          `${archiveConfig.path}: packaged descriptor ${descriptor.entry} PostgreSQL filesets differ from source ${descriptor.sourcePath}`,
        );
      }
      const matchedEntries = [];
      for (const pattern of descriptor.runtimeDriverPatterns) {
        if (isApplicationInternalPath(pattern)) {
          errors.push(
            `${archiveConfig.path}: ${descriptor.entry} runtime driver pattern points inside an application archive: ${pattern}`,
          );
          continue;
        }
        if (
          !sourceFilesets.some((fileset) =>
            patternMatchesFileset(pattern, fileset),
          ) ||
          !filesets.some((fileset) => patternMatchesFileset(pattern, fileset))
        ) {
          errors.push(
            `${archiveConfig.path}: ${descriptor.entry} runtime driver pattern is not substantiated by descriptor fileset dir/includes: ${pattern}`,
          );
          continue;
        }
        const matches = entries.filter(
          (entry) =>
            wildcardMatch(normalize(entry), normalize(pattern)) &&
            isPostgresqlDriverEntry(entry) &&
            !isApplicationInternalPath(entry),
        );
        matchedEntries.push(...matches);
        if (matches.length === 0) {
          errors.push(
            `${archiveConfig.path}: ${descriptor.entry} expects PostgreSQL driver at ${pattern}, but no archive entry matches`,
          );
        }
      }
      if (!descriptorReferencesPostgresql) {
        errors.push(
          `${archiveConfig.path}: ${descriptor.entry} does not contain a PostgreSQL runtime driver reference`,
        );
      }
      const uniqueMatches = [...new Set(matchedEntries)].sort();
      descriptorResults.push({
        entry: descriptor.entry,
        sourcePath: descriptor.sourcePath,
        descriptorReferencesPostgresql,
        runtimeDriverPatterns: descriptor.runtimeDriverPatterns,
        sourceDeclaredFilesets: sourceFilesets,
        declaredFilesets: filesets,
        matchingDriverEntries: uniqueMatches,
        status:
          errors.length === previousErrorCount && descriptorReferencesPostgresql && uniqueMatches.length > 0
            ? "passed"
            : "failed",
      });
    }
    const warOnlyDriver =
      archiveDriverEntries.length === 0 && nestedWarDrivers.length > 0;
    if (warOnlyDriver) {
      errors.push(
        `${archiveConfig.path}: PostgreSQL driver exists only inside nested WAR content, not at a runtime descriptor location`,
      );
    }
    archives.push({
      id: archiveConfig.id,
      path: archiveConfig.path,
      sha256: createHash("sha256")
        .update(await readFile(archivePath))
        .digest("hex"),
      entryCount: entries.length,
      descriptorResults,
      archiveDriverEntries: archiveDriverEntries.sort(),
      nestedWarDrivers,
      warOnlyDriver,
      status:
        descriptorResults.length === archiveConfig.descriptors.length &&
        descriptorResults.every(
          (descriptor) =>
            descriptor.status === "passed" &&
            descriptor.sourceDeclaredFilesets.length > 0 &&
            sameFilesets(
              descriptor.sourceDeclaredFilesets,
              descriptor.declaredFilesets,
            ),
        ) &&
        !warOnlyDriver
          ? "passed"
          : "failed",
    });
  }
  return {
    schemaVersion: 1,
    status: errors.length === 0 ? "passed" : "failed",
    archives,
    errors,
  };
}

function validateConfig(config) {
  if (!Array.isArray(config?.archives) || config.archives.length === 0) {
    throw new Error("driver packaging config requires at least one archive");
  }
  for (const archive of config.archives) {
    if (
        !archive?.id ||
      !archive?.path ||
      !Array.isArray(archive?.descriptors) ||
      archive.descriptors.length === 0
    ) {
      throw new Error("each archive requires id, path, and descriptors");
    }
    for (const descriptor of archive.descriptors) {
      if (
        !descriptor?.entry ||
        !descriptor?.sourcePath ||
        !Array.isArray(descriptor?.runtimeDriverPatterns) ||
        descriptor.runtimeDriverPatterns.length === 0
      ) {
        throw new Error(
          "each descriptor requires sourcePath, entry, and runtimeDriverPatterns",
        );
      }
    }
  }
}

function listArchive(archivePath, jarExecutable = "jar") {
  const result = runCommand(jarExecutable, ["tf", archivePath]);
  return {
    status: result.status,
    stderr: result.stderr || result.stdout,
    entries:
      result.status === 0
        ? result.stdout.split(/\r?\n/).filter(Boolean)
        : [],
  };
}

async function readArchiveText(
  archivePath,
  entry,
  jarExecutable = "jar",
) {
  const temporary = await mkdtemp(
    path.join(os.tmpdir(), "driver-packaging-descriptor-"),
  );
  try {
    const result = runCommand(
      jarExecutable,
      ["xf", archivePath, entry],
      temporary,
    );
    if (result.status !== 0) {
      throw new Error(`could not extract ${entry}: ${result.stderr}`);
    }
    return await readFile(path.join(temporary, ...normalize(entry).split("/")), "utf8");
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
}

async function inspectNestedWars(
  archivePath,
  entries,
  jarExecutable = "jar",
) {
  const warEntries = entries.filter((entry) => /\.war$/i.test(entry));
  if (warEntries.length === 0) return { drivers: [], errors: [] };
  const temporary = await mkdtemp(
    path.join(os.tmpdir(), "driver-packaging-war-"),
  );
  try {
    const extracted = runCommand(
      jarExecutable,
      ["xf", archivePath, ...warEntries],
      temporary,
    );
    if (extracted.status !== 0) {
      return {
        drivers: [],
        errors: [`could not extract nested WARs (${extracted.stderr})`],
      };
    }
    const results = [];
    const errors = [];
    for (const warEntry of warEntries) {
      const nestedPath = path.join(
        temporary,
        ...normalize(warEntry).split("/"),
      );
      const nested = listArchive(nestedPath, jarExecutable);
      if (nested.status !== 0) {
        errors.push(`could not inspect nested WAR ${warEntry} (${nested.stderr})`);
        continue;
      }
      for (const entry of nested.entries.filter(isPostgresqlDriverEntry)) {
        results.push(`${warEntry}!/${entry}`);
      }
    }
    return { drivers: results.sort(), errors };
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
}

function isPostgresqlDriverEntry(entry) {
  return /(?:^|\/)(?:postgresql|pgjdbc)(?:-[^/]+)?\.jar$/i.test(
    normalize(entry),
  );
}

function isApplicationInternalPath(entry) {
  return /(?:\.war\/|\/WEB-INF\/|\/BOOT-INF\/)/i.test(normalize(entry));
}

function runtimeDescriptor(content, label, errors) {
  try {
    return parseXmlDocument(content);
  } catch (error) {
    if (!["JAVA_POM_XML_INVALID", "JAVA_TEST_PREFLIGHT_INVALID"].includes(error.code)) throw error;
    errors.push(`${label}: invalid or unsupported runtime descriptor XML (${error.code})`);
    return null;
  }
}

function* elements(node) {
  yield node;
  for (const child of node.children) yield* elements(child);
}

function externalPostgresqlFilesets(document) {
  const results = [];
  for (const library of elements(document)) {
    if (library.name !== "library") continue;
    for (const fileset of library.children.filter((child) => child.name === "fileset")) {
      const directory = fileset.attributes.get("dir");
      const includes = fileset.attributes.get("includes");
      if (!directory || !includes) continue;
      for (const include of includes.split(/[\s,;]+/).filter(Boolean)) {
        if (!/(?:postgresql|pgjdbc).*\.jar/i.test(include)) continue;
        results.push({ directory, include });
      }
    }
  }
  return results;
}

function patternMatchesFileset(pattern, fileset) {
  const normalizedPattern = normalize(pattern);
  const patternDirectory = path.posix.dirname(normalizedPattern);
  const patternFile = path.posix.basename(normalizedPattern);
  const descriptorSuffix = normalize(fileset.directory)
    .replace(/^\$\{server\.config\.dir\}\/?/, "")
    .replace(/^\/+|\/+$/g, "");
  return (
    patternFile === fileset.include &&
    (descriptorSuffix === "." ||
      descriptorSuffix === "" ||
      patternDirectory === descriptorSuffix ||
      patternDirectory.endsWith(`/${descriptorSuffix}`))
  );
}

function sameFilesets(left, right) {
  const normalized = (values) =>
    values
      .map((value) => `${normalize(value.directory)}\0${normalize(value.include)}`)
      .sort();
  return JSON.stringify(normalized(left)) === JSON.stringify(normalized(right));
}

function wildcardMatch(value, pattern) {
  const token = "__DOUBLE_STAR__";
  const escaped = pattern
    .replaceAll("**", token)
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replaceAll("*", "[^/]*")
    .replaceAll("?", "[^/]")
    .replaceAll(token, ".*");
  return new RegExp(`^${escaped}$`, "i").test(value);
}

function normalize(value) {
  return String(value).replaceAll("\\", "/").replace(/^\.\/+/, "");
}

function runCommand(command, args, cwd = undefined) {
  const spawn =
    process.platform === "win32" && !/\.exe$/i.test(command)
      ? {
          command: process.env.ComSpec ?? "cmd.exe",
          args: ["/d", "/s", "/c", [command, ...args].map(quote).join(" ")],
          windowsVerbatimArguments: true,
        }
      : { command, args, windowsVerbatimArguments: false };
  const result = spawnSync(spawn.command, spawn.args, {
    cwd,
    encoding: "utf8",
    shell: false,
    windowsHide: true,
    windowsVerbatimArguments: spawn.windowsVerbatimArguments,
  });
  if (result.error) throw result.error;
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
  };
}

function quote(value) {
  const text = String(value);
  if (/^[A-Za-z0-9_./:\\=-]+$/.test(text)) return text;
  return `"${text.replaceAll('"', '""')}"`;
}

async function main() {
  const root = process.argv[2];
  const configPath = process.argv[3];
  if (!root || !configPath) {
    throw new Error(
      "Usage: node inspect-driver-packaging.mjs <codebase> <config.json>",
    );
  }
  const config = JSON.parse(await readFile(path.resolve(configPath), "utf8"));
  const result = await inspectDriverPackaging(root, config);
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  if (result.status !== "passed") process.exitCode = 1;
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
