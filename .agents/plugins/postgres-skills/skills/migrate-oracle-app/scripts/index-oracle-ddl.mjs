import { createHash } from "node:crypto";
import { lstat, readFile, readdir, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const DDL_EXTENSIONS = new Set([
  ".bdy",
  ".ddl",
  ".fnc",
  ".pck",
  ".pkb",
  ".pks",
  ".plb",
  ".pls",
  ".plsql",
  ".prc",
  ".spc",
  ".sql",
  ".tpb",
  ".tps",
  ".trg",
  ".vw",
]);
const MAX_FILES = 10_000;
const MAX_FILE_BYTES = 10_000_000;
const MAX_TOTAL_BYTES = 250_000_000;
const MAX_LAYOUT_DEPTH = 12;
const MAX_LAYOUT_DIRECTORIES = 50_000;
const KNOWN_OBJECT_TYPE_DIRECTORIES = Object.freeze({
  FUNCTION: ["function"],
  INDEX: ["index"],
  MATERIALIZED_VIEW: ["materialized view"],
  PACKAGE: ["package"],
  PROCEDURE: ["procedure"],
  SCHEMA: ["schema"],
  SEQUENCE: ["sequence"],
  SYNONYM: ["synonym"],
  TABLE: ["table"],
  TRIGGER: ["trigger"],
  TYPE: ["type"],
  VIEW: ["view", "materialized view"],
});
const LAYOUT_EXCLUDED_DIRECTORIES = new Set([
  ".git",
  ".gradle",
  ".idea",
  ".next",
  ".nuxt",
  ".tox",
  ".venv",
  ".vs",
  ".vscode",
  "__pycache__",
  "build",
  "coverage",
  "dist",
  "node_modules",
  "obj",
  "out",
  "target",
  "vendor",
  "venv",
]);
const OBJECT_PATTERN =
  /\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:EDITIONABLE|NONEDITIONABLE|FORCE|NOFORCE|PUBLIC|GLOBAL\s+TEMPORARY)\s+)*(?:(?:UNIQUE|BITMAP)\s+)?(MATERIALIZED\s+VIEW|PACKAGE\s+BODY|TYPE\s+BODY|PACKAGE|PROCEDURE|FUNCTION|TRIGGER|VIEW|TABLE|SEQUENCE|TYPE|SYNONYM|INDEX|SCHEMA)\s+(?:IF\s+NOT\s+EXISTS\s+)?((?:"(?:[^"]|"")+"|[A-Za-z][A-Za-z0-9_$#]*)(?:\s*\.\s*(?:"(?:[^"]|"")+"|[A-Za-z][A-Za-z0-9_$#]*))?)/gi;
const PACKAGE_MEMBER_PATTERN =
  /\b(PROCEDURE|FUNCTION)\s+("(?:[^"]|"")+"|[A-Za-z][A-Za-z0-9_$#]*)\s*(?:\(|(?:RETURN|IS|AS)\b)/gi;

export async function indexOracleDdlFolder(
  folderPath,
  {
    schema = null,
    maxFiles = MAX_FILES,
    maxFileBytes = MAX_FILE_BYTES,
    maxTotalBytes = MAX_TOTAL_BYTES,
    maxLayoutDepth = MAX_LAYOUT_DEPTH,
    maxLayoutDirectories = MAX_LAYOUT_DIRECTORIES,
  } = {},
) {
  const root = path.resolve(folderPath);
  const rootMetadata = await lstat(root);
  if (!rootMetadata.isDirectory() || rootMetadata.isSymbolicLink()) {
    throw new Error(`Oracle DDL folder is not a regular directory: ${root}`);
  }

  const candidates = [];
  const errors = [];
  const warnings = [];
  const layout = await resolveSchemaLayout(root, schema, {
    maxLayoutDepth,
    maxLayoutDirectories,
  });
  errors.push(...layout.errors);
  warnings.push(...layout.warnings);
  if (layout.selected) {
    for (const objectType of layout.selected.objectTypeDirectories) {
      await walkDdlTypeDirectory(
        root,
        objectType.absolutePath,
        objectType.name,
        objectType.expectedObjectKinds,
        candidates,
        errors,
      );
    }
  }
  if (candidates.length > maxFiles) {
    errors.push(
      `Oracle DDL folder contains ${candidates.length} files; maximum is ${maxFiles}.`,
    );
  }

  let totalBytes = 0;
  const files = [];
  for (const candidate of candidates.slice(0, maxFiles)) {
    const metadata = await stat(candidate.absolutePath);
    totalBytes += metadata.size;
    if (metadata.size > maxFileBytes) {
      errors.push(
        `${candidate.relativePath}: exceeds ${maxFileBytes}-byte per-object DDL limit.`,
      );
      continue;
    }
    if (totalBytes > maxTotalBytes) {
      errors.push(
        `${candidate.relativePath}: folder exceeds ${maxTotalBytes}-byte total DDL limit.`,
      );
      break;
    }
    const content = await readFile(candidate.absolutePath, "utf8");
    const definitions = extractObjectDefinitions(
      content,
      layout.selected?.schemaName ?? null,
    );
    let identitySource = "ddl-primary-definition";
    let primaryDefinitions = [];
    if (definitions.length === 0) {
      identitySource = "folder-and-filename";
      primaryDefinitions = [
        fallbackDefinition(
          candidate,
          layout.selected?.schemaName ?? null,
        ),
      ];
      warnings.push(
        `${candidate.relativePath}: object identity inferred from schema folder, object-type folder, and filename because no supported CREATE definition was parsed.`,
      );
    } else if (candidate.expectedObjectKinds.length > 0) {
      primaryDefinitions = definitions.filter((definition) =>
        candidate.expectedObjectKinds.includes(definition.objectKind),
      );
      if (primaryDefinitions.length === 0) {
        errors.push(
          `${candidate.relativePath}: parsed object kinds (${[
            ...new Set(definitions.map((definition) => definition.objectKind)),
          ].join(", ")}) do not match object-type folder ${candidate.objectTypeDirectory}.`,
        );
        continue;
      }
    } else {
      primaryDefinitions = definitions.filter(
        (definition) =>
          definition.objectKind === definitions[0].objectKind,
      );
    }
    const primaryObjects = new Map(
      primaryDefinitions.map((definition) => [
        definition.objectKey,
        definition,
      ]),
    );
    if (primaryObjects.size === 0) {
      errors.push(
        `${candidate.relativePath}: no primary object definition found.`,
      );
      continue;
    }
    if (primaryObjects.size > 1) {
      errors.push(
        `${candidate.relativePath}: defines multiple primary ${candidate.objectTypeDirectory} objects (${[...primaryObjects.values()]
          .map(
            (definition) =>
              `${definition.objectKind} ${definition.displayIdentity}`,
          )
          .join(", ")}); one file per object is required.`,
      );
      continue;
    }
    const object = [...primaryObjects.values()][0];
    const declaredSchema =
      object.objectKind === "schema"
        ? object.oracleObjectName
        : object.oracleSchema;
    if (
      declaredSchema?.toLowerCase() !==
      layout.selected?.schemaName.toLowerCase()
    ) {
      errors.push(
        `${candidate.relativePath}: primary object schema ${declaredSchema ?? "<none>"} does not match schema folder ${layout.selected?.schemaName}.`,
      );
      continue;
    }
    files.push({
      relativePath: candidate.relativePath,
      objectTypeDirectory: candidate.objectTypeDirectory,
      expectedObjectKinds: candidate.expectedObjectKinds,
      identitySource,
      bytes: metadata.size,
      sha256: sha256(content),
      objectKind: object.objectKind,
      oracleSchema: object.oracleSchema,
      oracleObjectName: object.oracleObjectName,
      displayIdentity: object.displayIdentity,
      identityKey: object.identityKey,
      objectKey: object.objectKey,
      statementKinds: [
        ...new Set(
          definitions
            .filter((definition) => definition.objectKey === object.objectKey)
            .map((definition) => definition.statementKind),
        ),
      ].sort(),
      packageMembers:
        object.objectKind === "package"
          ? extractPackageMembers(content)
          : [],
      auxiliaryDefinitions: definitions
        .filter((definition) => definition.objectKey !== object.objectKey)
        .map((definition) => ({
          objectKind: definition.objectKind,
          displayIdentity: definition.displayIdentity,
          objectKey: definition.objectKey,
        }))
        .sort((left, right) => left.objectKey.localeCompare(right.objectKey)),
    });
  }

  const filesByObject = new Map();
  for (const file of files) {
    const paths = filesByObject.get(file.objectKey) ?? [];
    paths.push(file.relativePath);
    filesByObject.set(file.objectKey, paths);
  }
  for (const [objectKey, paths] of filesByObject) {
    const matchingFiles = files.filter((file) => file.objectKey === objectKey);
    const componentKinds = matchingFiles.flatMap(
      (file) => file.statementKinds,
    );
    const componentsAreUnique =
      componentKinds.length === new Set(componentKinds).size;
    const isPackageCompanionSet =
      matchingFiles.every((file) => file.objectKind === "package") &&
      componentKinds.every((kind) =>
        ["package", "package body"].includes(kind),
      ) &&
      componentsAreUnique;
    if (paths.length > 1 && !isPackageCompanionSet) {
      errors.push(
        `${objectKey}: duplicate Oracle object DDL files (${paths.join(", ")}).`,
      );
    }
  }

  files.sort((left, right) => left.relativePath.localeCompare(right.relativePath));
  errors.sort();
  const result = {
    schemaVersion: 1,
    folderPath: root,
    requestedSchema: schema,
    schema: layout.selected?.schemaName ?? null,
    resolvedSchemaPath: layout.selected?.absolutePath ?? null,
    relativeSchemaPath: layout.selected?.relativePath ?? null,
    layout: layout.selected?.layout ?? null,
    migrationName: layout.selected?.migrationName ?? null,
    objectTypeDirectories: layout.selected
      ? layout.selected.objectTypeDirectories.map((objectType) => ({
          name: objectType.name,
          relativePath: path
            .relative(root, objectType.absolutePath)
            .replaceAll("\\", "/"),
          expectedObjectKinds: objectType.expectedObjectKinds,
        }))
      : [],
    discoveredSchemaPaths: layout.candidates.map(
      (candidate) => candidate.relativePath,
    ),
    indexedAt: new Date().toISOString(),
    limits: {
      maxFiles,
      maxFileBytes,
      maxTotalBytes,
      maxLayoutDepth,
      maxLayoutDirectories,
    },
    candidateFiles: candidates.length,
    indexedObjects: filesByObject.size,
    totalBytes,
    files,
    errors,
    warnings: warnings.sort(),
    status: errors.length === 0 ? "VALID" : "INVALID",
  };
  result.folderDigest = sha256(
    JSON.stringify(
      {
        schema: result.schema,
        migrationName: result.migrationName,
        relativeSchemaPath: result.relativeSchemaPath,
        objectTypeDirectories: result.objectTypeDirectories,
        files: files.map((file) => ({
          relativePath: file.relativePath,
          sha256: file.sha256,
          objectKey: file.objectKey,
        })),
      },
    ),
  );
  return result;
}

async function resolveSchemaLayout(
  root,
  requestedSchema,
  { maxLayoutDepth, maxLayoutDirectories },
) {
  const candidates = [];
  const schemaHints = [];
  const warnings = [];
  const queue = [{ directory: root, depth: 0 }];
  let visitedDirectories = 0;
  while (queue.length > 0) {
    const current = queue.shift();
    visitedDirectories += 1;
    if (visitedDirectories > maxLayoutDirectories) {
      return {
        selected: null,
        candidates,
        warnings,
        errors: [
          `Oracle DDL layout search exceeded ${maxLayoutDirectories} directories.`,
        ],
      };
    }
    let entries;
    try {
      entries = await readdir(current.directory, { withFileTypes: true });
    } catch (error) {
      if (
        path.basename(current.directory).toLowerCase() ===
        requestedSchema?.toLowerCase()
      ) {
        schemaHints.push({
          path: path.relative(root, current.directory).replaceAll("\\", "/"),
          error: error.code ?? error.message,
        });
      }
      continue;
    }
    const directories = entries
      .filter((entry) => entry.isDirectory() && !entry.isSymbolicLink())
      .sort((left, right) => left.name.localeCompare(right.name));
    const objectTypeDirectories = directories.filter(
      (entry) =>
        !entry.name.startsWith(".") &&
        !LAYOUT_EXCLUDED_DIRECTORIES.has(entry.name.toLowerCase()),
    );
    const relativePath =
      path.relative(root, current.directory).replaceAll("\\", "/") || ".";
    const schemaName = path.basename(current.directory);
    const canonical = canonicalLayoutInfo(current.directory);
    const requestedMatch =
      requestedSchema &&
      schemaName.toLowerCase() === requestedSchema.toLowerCase();
    const directWithoutSchema =
      !requestedSchema &&
      current.directory === root &&
      objectTypeDirectories.some(
        (entry) => expectedKindsForDirectory(entry.name).length > 0,
      );
    const canonicalWithoutSchema =
      !requestedSchema && canonical !== null;
    if (
      objectTypeDirectories.length > 0 &&
      (requestedMatch || directWithoutSchema || canonicalWithoutSchema)
    ) {
      candidates.push({
        schemaName,
        absolutePath: current.directory,
        relativePath,
        layout: canonical
          ? "canonical-postgres-migrations"
          : "schema-object-type-folders",
        migrationName: canonical?.migrationName ?? null,
        objectTypeDirectories: objectTypeDirectories.map((entry) => ({
          name: entry.name,
          absolutePath: path.join(current.directory, entry.name),
          expectedObjectKinds: expectedKindsForDirectory(entry.name),
        })),
      });
      continue;
    }
    if (requestedMatch && objectTypeDirectories.length === 0) {
      schemaHints.push({
        path: relativePath,
        error: "Schema folder contains no object-type directories.",
      });
    }
    if (current.depth >= maxLayoutDepth) continue;
    for (const entry of directories) {
      if (LAYOUT_EXCLUDED_DIRECTORIES.has(entry.name.toLowerCase())) continue;
      queue.push({
        directory: path.join(current.directory, entry.name),
        depth: current.depth + 1,
      });
    }
  }

  const requested = requestedSchema?.trim().toLowerCase();
  const matching = requested
    ? candidates.filter(
        (candidate) => candidate.schemaName.toLowerCase() === requested,
      )
    : candidates;
  const canonical = matching.filter(
    (candidate) => candidate.layout === "canonical-postgres-migrations",
  );
  const direct = matching.filter(
    (candidate) => candidate.absolutePath === root,
  );
  let selected = null;
  if (canonical.length === 1) {
    selected = canonical[0];
  } else if (direct.length === 1) {
    selected = direct[0];
  } else if (matching.length === 1) {
    selected = matching[0];
  }

  const errors = [];
  if (!selected) {
    if (matching.length > 1) {
      errors.push(
        `Oracle DDL schema layout is ambiguous; matching schema folders: ${matching
          .map((candidate) => candidate.relativePath)
          .join(", ")}.`,
      );
    } else if (requested && matching.length === 0) {
      const hints = schemaHints.filter(
        (hint) =>
          path.basename(hint.path).toLowerCase() === requested ||
          hint.path === ".",
      );
      if (hints.length > 0) {
        errors.push(
          `Oracle DDL schema ${requestedSchema} has no usable object-type directories (${hints
            .map((hint) => `${hint.path}: ${hint.error}`)
            .join("; ")}).`,
        );
      } else {
        errors.push(
          `No Oracle DDL schema folder named ${requestedSchema} contains object-type directories.`,
        );
      }
    } else if (candidates.length === 0) {
      errors.push(
        "No Oracle DDL schema folder containing object-type directories was found.",
      );
    } else {
      errors.push(
        `Multiple Oracle DDL schema folders were found; supply schema=<name>: ${candidates
          .map((candidate) => candidate.relativePath)
          .join(", ")}.`,
      );
    }
  }
  if (
    selected &&
    selected.objectTypeDirectories.some(
      (objectType) => objectType.expectedObjectKinds.length === 0,
    )
  ) {
    warnings.push(
      `Unrecognized object-type folders will be indexed by parsed DDL kind or filename fallback: ${selected.objectTypeDirectories
        .filter((objectType) => objectType.expectedObjectKinds.length === 0)
        .map((objectType) => objectType.name)
        .join(", ")}.`,
    );
  }
  return { selected, candidates, errors, warnings };
}

function canonicalLayoutInfo(absolutePath) {
  const normalized = absolutePath.replaceAll("\\", "/");
  const match = normalized.match(
    /(?:^|\/)\.github\/postgres-migrations\/([^/]+)\/artifacts\/oracle\/([^/]+)$/i,
  );
  if (!match) return null;
  return {
    migrationName: match[1],
    schemaName: match[2],
  };
}

function expectedKindsForDirectory(directoryName) {
  const normalized = directoryName
    .trim()
    .toUpperCase()
    .replace(/[\s-]+/g, "_");
  const singular =
    normalized.endsWith("S") && !normalized.endsWith("SS")
      ? normalized.slice(0, -1)
      : normalized;
  return (
    KNOWN_OBJECT_TYPE_DIRECTORIES[normalized] ??
    KNOWN_OBJECT_TYPE_DIRECTORIES[singular] ??
    []
  );
}

async function walkDdlTypeDirectory(
  root,
  directory,
  objectTypeDirectory,
  expectedObjectKinds,
  candidates,
  errors,
) {
  const entries = await readdir(directory, { withFileTypes: true });
  for (const entry of entries.sort((left, right) =>
    left.name.localeCompare(right.name),
  )) {
    const absolutePath = path.join(directory, entry.name);
    const relativePath = path
      .relative(root, absolutePath)
      .replaceAll("\\", "/");
    if (entry.isSymbolicLink()) {
      errors.push(`${relativePath}: symbolic links are not permitted.`);
      continue;
    }
    if (entry.isDirectory()) {
      await walkDdlTypeDirectory(
        root,
        absolutePath,
        objectTypeDirectory,
        expectedObjectKinds,
        candidates,
        errors,
      );
      continue;
    }
    if (!entry.isFile()) {
      errors.push(`${relativePath}: non-regular entries are not permitted.`);
      continue;
    }
    const extension = path.extname(entry.name).toLowerCase();
    if (!DDL_EXTENSIONS.has(extension)) {
      errors.push(`${relativePath}: unsupported Oracle DDL file extension.`);
      continue;
    }
    candidates.push({
      absolutePath,
      relativePath,
      objectTypeDirectory,
      expectedObjectKinds,
    });
  }
}

function extractObjectDefinitions(content, defaultSchema) {
  const definitions = [];
  const code = maskOracleNonCode(content);
  for (const match of code.matchAll(OBJECT_PATTERN)) {
    const statementKind = normalizeKind(match[1]);
    const identity = parseIdentity(
      match[2],
      defaultSchema,
      statementKind === "schema",
    );
    definitions.push({
      statementKind: match[1].toLowerCase().replace(/\s+/g, " "),
      objectKind: statementKind,
      ...identity,
      objectKey: `${statementKind}:${identity.identityKey}`,
    });
  }
  return definitions;
}

function fallbackDefinition(candidate, defaultSchema) {
  const objectKind =
    candidate.expectedObjectKinds[0] ??
    candidate.objectTypeDirectory
      .trim()
      .toLowerCase()
      .replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ")
      .replace(/s$/, "");
  const oracleObjectName = path.basename(
    candidate.relativePath,
    path.extname(candidate.relativePath),
  );
  const identityKey = `${normalizeIdentifier(defaultSchema)}.${normalizeIdentifier(
    oracleObjectName,
  )}`;
  return {
    statementKind: objectKind,
    objectKind,
    oracleSchema: defaultSchema,
    oracleObjectName,
    displayIdentity: `${defaultSchema}.${oracleObjectName}`,
    identityKey,
    objectKey: `${objectKind}:${identityKey}`,
  };
}

function extractPackageMembers(content) {
  const members = new Map();
  const code = maskOracleNonCode(content);
  for (const match of code.matchAll(PACKAGE_MEMBER_PATTERN)) {
    const name = unquoteIdentifier(match[2]);
    const key = `${match[1].toLowerCase()}:${normalizeIdentifier(match[2])}`;
    members.set(key, {
      kind: match[1].toLowerCase(),
      name,
    });
  }
  return [...members.values()].sort((left, right) =>
    `${left.kind}:${left.name}`.localeCompare(`${right.kind}:${right.name}`),
  );
}

function maskOracleNonCode(content) {
  const output = content.split("");
  let index = 0;
  while (index < content.length) {
    if (content.startsWith("--", index)) {
      const end = content.indexOf("\n", index + 2);
      const stop = end < 0 ? content.length : end;
      maskRange(output, index, stop);
      index = stop;
      continue;
    }
    if (content.startsWith("/*", index)) {
      const end = content.indexOf("*/", index + 2);
      const stop = end < 0 ? content.length : end + 2;
      maskRange(output, index, stop);
      index = stop;
      continue;
    }
    if (
      (content[index] === "q" || content[index] === "Q") &&
      content[index + 1] === "'" &&
      index + 2 < content.length
    ) {
      const opener = content[index + 2];
      const closer = new Map([
        ["[", "]"],
        ["{", "}"],
        ["(", ")"],
        ["<", ">"],
      ]).get(opener) ?? opener;
      const end = content.indexOf(`${closer}'`, index + 3);
      const stop = end < 0 ? content.length : end + 2;
      maskRange(output, index, stop);
      index = stop;
      continue;
    }
    if (content[index] !== "'") {
      index += 1;
      continue;
    }
    const start = index;
    index += 1;
    while (index < content.length) {
      if (content[index] !== "'") {
        index += 1;
        continue;
      }
      if (content[index + 1] === "'") {
        index += 2;
        continue;
      }
      index += 1;
      break;
    }
    maskRange(output, start, index);
  }
  return output.join("");
}

function maskRange(output, start, end) {
  for (let index = start; index < end; index += 1) {
    if (output[index] !== "\r" && output[index] !== "\n") output[index] = " ";
  }
}

function parseIdentity(value, defaultSchema, schemaObject) {
  const identifiers =
    value.match(/"(?:[^"]|"")+"|[A-Za-z][A-Za-z0-9_$#]*/g) ?? [];
  if (identifiers.length === 0) {
    return {
      oracleSchema: defaultSchema,
      oracleObjectName: "<unknown>",
      displayIdentity: defaultSchema
        ? `${defaultSchema}.<unknown>`
        : "<unknown>",
      identityKey: `${defaultSchema?.toUpperCase() ?? "<default>"}.<unknown>`,
    };
  }
  if (schemaObject) {
    const schemaName = unquoteIdentifier(identifiers.at(-1));
    return {
      oracleSchema: schemaName,
      oracleObjectName: schemaName,
      displayIdentity: schemaName,
      identityKey: normalizeIdentifier(identifiers.at(-1)),
    };
  }
  const oracleSchema =
    identifiers.length > 1
      ? unquoteIdentifier(identifiers[0])
      : defaultSchema;
  const oracleObjectName = unquoteIdentifier(
    identifiers[identifiers.length - 1],
  );
  const normalizedSchema =
    identifiers.length > 1
      ? normalizeIdentifier(identifiers[0])
      : defaultSchema
        ? normalizeIdentifier(defaultSchema)
        : null;
  const normalizedObject = normalizeIdentifier(
    identifiers[identifiers.length - 1],
  );
  return {
    oracleSchema,
    oracleObjectName,
    displayIdentity: oracleSchema
      ? `${oracleSchema}.${oracleObjectName}`
      : oracleObjectName,
    identityKey: `${normalizedSchema ?? "<default>"}.${normalizedObject}`,
  };
}

function normalizeKind(value) {
  return value
    .toLowerCase()
    .replace(/\s+body$/, "")
    .replace(/\s+/g, " ");
}

function normalizeIdentifier(value) {
  if (value.startsWith('"')) return `quoted:${unquoteIdentifier(value)}`;
  return unquoteIdentifier(value).toUpperCase();
}

function unquoteIdentifier(value) {
  if (!value?.startsWith('"')) return value;
  return value.slice(1, -1).replaceAll('""', '"');
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

async function main() {
  const args = process.argv.slice(2);
  let schema = null;
  const positional = [];
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--schema") {
      schema = args[index + 1] ?? null;
      index += 1;
      continue;
    }
    positional.push(args[index]);
  }
  const folder = positional[0];
  const output = positional[1];
  if (!folder) {
    throw new Error(
      "Usage: node index-oracle-ddl.mjs <oracle-ddl-folder> [output.json] [--schema <schema>]",
    );
  }
  if (args.includes("--schema") && !schema) {
    throw new Error("--schema requires a schema name");
  }
  const result = await indexOracleDdlFolder(folder, { schema });
  const serialized = `${JSON.stringify(result, null, 2)}\n`;
  if (output) await writeFile(path.resolve(output), serialized, "utf8");
  process.stdout.write(serialized);
  if (result.status !== "VALID") process.exitCode = 2;
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
