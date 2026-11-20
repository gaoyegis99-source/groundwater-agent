import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import {
  readFile,
  readdir,
  lstat,
  stat,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { maskSourceComments, sourceLiteralAt } from "./source-comment-mask.mjs";

const EXCLUDED_DIRECTORIES = new Set([
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

const TEXT_EXTENSIONS = new Set([
  ".c",
  ".ascx",
  ".asp",
  ".aspx",
  ".bdy",
  ".cbl",
  ".cc",
  ".cfg",
  ".conf",
  ".config",
  ".cpp",
  ".cpy",
  ".cs",
  ".cshtml",
  ".csproj",
  ".css",
  ".cxx",
  ".ddl",
  ".fnc",
  ".fs",
  ".fsproj",
  ".go",
  ".gradle",
  ".groovy",
  ".h",
  ".hpp",
  ".html",
  ".ini",
  ".java",
  ".js",
  ".json",
  ".jsp",
  ".jspx",
  ".jsx",
  ".kt",
  ".kts",
  ".less",
  ".m",
  ".ora",
  ".pc",
  ".php",
  ".pck",
  ".pkb",
  ".pks",
  ".plb",
  ".pll",
  ".pm",
  ".pl",
  ".pls",
  ".plsql",
  ".prc",
  ".properties",
  ".props",
  ".ps1",
  ".py",
  ".rb",
  ".rs",
  ".resx",
  ".sass",
  ".scala",
  ".scss",
  ".sh",
  ".sln",
  ".slnx",
  ".sql",
  ".spc",
  ".toml",
  ".trg",
  ".tpb",
  ".tps",
  ".ts",
  ".tsx",
  ".txt",
  ".targets",
  ".vb",
  ".vbproj",
  ".vbs",
  ".vcxproj",
  ".vue",
  ".vw",
  ".xml",
  ".yaml",
  ".yml",
]);

const TEXT_FILE_NAMES = new Set([
  ".classpath",
  "build.xml",
  "CMakeLists.txt",
  "Dockerfile",
  "go.mod",
  "Makefile",
  "MANIFEST.MF",
  "package.json",
  "pom.xml",
  "pyproject.toml",
  "requirements.txt",
  "web.xml",
]);

const INERT_FILE_NAMES = new Set([
  "CODEOWNERS",
  "LICENSE",
  "LICENSE.txt",
  "NOTICE",
  "Procfile",
]);

const ACTIVE_DATABASE_RULES = [
  [
    "JDBC/SQL API",
    /\b(?:java\.sql|javax\.sql|jakarta\.persistence|JdbcTemplate|EntityManager)\b/,
  ],
  [
    "Java database framework",
    /\b(?:org\.jooq|DSLContext|HikariCP|io\.r2dbc|org\.apache\.ibatis|@Select\s*\(|@Mapper\b|PanacheRepository|io\.micronaut\.data)\b/,
  ],
  [
    "ADO.NET/ORM",
    /\b(?:DbContext|DbConnection|DbCommand|EntityFrameworkCore|Dapper)\b/,
  ],
  [
    "Python database API",
    /\b(?:sqlalchemy|django\.db|psycopg|asyncpg|cx_Oracle|oracledb)\b/i,
  ],
  [
    "Node database library",
    /(?:"(?:pg|knex|sequelize|typeorm|prisma|oracledb|mysql2)"\s*:|from\s+["'](?:pg|knex|sequelize|typeorm|@prisma\/client|oracledb|mysql2))/i,
  ],
  [
    "Native database API",
    /\b(?:libpq|pqxx|OCIEnv|OCIStmt|oracle::occi|SQLConnect|SQLExecDirect|ADODB\.Connection|EXEC\s+SQL)\b/i,
  ],
  ["Perl database API", /\b(?:DBI|DBD::(?:Oracle|Pg))\b/],
  [
    "Datasource/connection configuration",
    /(?:\bspring\.datasource\b|\bjdbc:|\bconnectionStrings?\b|\bdatabase_url\b|\b(?:DataSource|DbConnection)\s+[A-Za-z_]\w*|(?<!\.)\b(?:createConnection|getConnection)\s*\()/i,
  ],
  [
    "ORM mapping",
    /(?:@Entity\b|<persistence-unit\b|<hibernate-configuration\b|\balembic\b|\bflyway\b|\bliquibase\b|\bknex\s+migrate\b|\btypeorm\b.{0,80}\bmigration\b)/i,
  ],
];

const ACTIVE_ORACLE_RULES = [
  [
    "Oracle JDBC/API",
    /\b(?:oracle\.jdbc|OracleTypes|OracleConnection|OraclePreparedStatement)\b/,
  ],
  ["Oracle connection", /jdbc:oracle:|Data Source\s*=\s*\(DESCRIPTION/i],
  [
    "Oracle .NET provider",
    /\b(?:Oracle\.ManagedDataAccess|Oracle\.DataAccess|System\.Data\.OracleClient)\b/,
  ],
  ["Oracle Python provider", /\b(?:cx_Oracle|oracledb)\b/],
  ["Oracle native API", /\b(?:OCIEnv|OCIStmt|oracle::occi|Pro\*C)\b/],
  ["Oracle Perl provider", /\bDBD::Oracle\b/],
  [
    "Oracle client/tooling",
    /\b(?:sqlplus|sqlldr|expdp|impdp|tnsnames|TNS_ADMIN|SERVICE_NAME\s*=)\b|\(\s*DESCRIPTION\s*=\s*\(\s*ADDRESS/i,
  ],
  [
    "Oracle dependency",
    /\b(?:ojdbc\d*|com\.oracle\.database|com\.oracle:ojdbc|OracleDialect)\b/i,
  ],
];

const ORACLE_SQL_PATTERN =
  /(?:\b(?:ROWNUM|SYSDATE|SYSTIMESTAMP|CONNECT\s+BY|FROM\s+DUAL|NEXTVAL\s+FROM\s+DUAL|NLSSORT|UTL_RAW)\b|(?<!\.)\b(?:NVL|DECODE)\s*\()/i;
const SQL_STATEMENT_PATTERN =
  /\b(?:SELECT\s+(?:DISTINCT\s+)?(?:\*|[\w"])?.{0,500}?\s+FROM\s+[\w"]|INSERT\s+INTO\b|UPDATE\s+[\w."]+\s+SET\b|DELETE\s+FROM\b|MERGE\s+INTO\b|CREATE\s+(?:TABLE|VIEW|SEQUENCE|FUNCTION|PROCEDURE|TRIGGER)\b|CALL\s+[\w."]+\s*\()/i;

const SQL_LIKE_EXTENSIONS = new Set([
  ".ddl",
  ".bdy",
  ".fnc",
  ".pkb",
  ".pks",
  ".plb",
  ".pls",
  ".plsql",
  ".prc",
  ".pck",
  ".sql",
  ".trg",
  ".tpb",
  ".tps",
  ".vw",
]);

const ARTIFACT_EXTENSIONS = new Set([
  ".a",
  ".dll",
  ".dylib",
  ".ear",
  ".jar",
  ".lib",
  ".nupkg",
  ".so",
  ".war",
  ".whl",
  ".zip",
]);

const INERT_EXTENSIONS = new Set([
  ".bmp",
  ".eot",
  ".gif",
  ".ico",
  ".jpeg",
  ".jpg",
  ".lock",
  ".map",
  ".md",
  ".mp3",
  ".mp4",
  ".pdf",
  ".png",
  ".svg",
  ".ttf",
  ".otf",
  ".wav",
  ".webm",
  ".webp",
  ".woff",
  ".woff2",
]);
const EXECUTION_ARTIFACT_PATHS = [
  ".gradle",
  ".next",
  ".nuxt",
  ".nyc_output",
  ".pytest_cache",
  ".tox",
  ".venv",
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
];
const EMBEDDED_SQL_EXTENSIONS = new Set([
  ".asp",
  ".aspx",
  ".c",
  ".cc",
  ".cbl",
  ".cpp",
  ".cs",
  ".cshtml",
  ".cpy",
  ".cxx",
  ".fs",
  ".go",
  ".groovy",
  ".h",
  ".hpp",
  ".java",
  ".js",
  ".jsp",
  ".jspx",
  ".jsx",
  ".kt",
  ".kts",
  ".m",
  ".pc",
  ".php",
  ".pl",
  ".pm",
  ".ps1",
  ".py",
  ".rb",
  ".rs",
  ".scala",
  ".ts",
  ".tsx",
  ".vb",
  ".vbs",
]);
const DEFAULT_MAX_FILES = 50_000;
const DEFAULT_MAX_TEXT_BYTES = 100_000_000;
const DEFAULT_MAX_DURATION_MS = 30_000;
const MAX_EMBEDDED_LITERAL_LENGTH = 100_000;

export async function detectMigrationScope(
  rootPath,
  {
    onProgress = null,
    verboseProgress = false,
    maxFiles = DEFAULT_MAX_FILES,
    maxTextBytes = DEFAULT_MAX_TEXT_BYTES,
    maxDurationMs = DEFAULT_MAX_DURATION_MS,
    excludePaths = [],
    captureBaseline = true,
    additionalTextExtensions = [],
    additionalTextFileNames = [],
  } = {},
) {
  const root = path.resolve(rootPath);
  if (!Array.isArray(additionalTextExtensions) ||
    additionalTextExtensions.some((entry) => typeof entry !== "string" || !/^\.[a-z0-9]+$/i.test(entry))) {
    throw new Error("Additional static scope text extensions must be simple dotted extensions.");
  }
  if (!Array.isArray(additionalTextFileNames) ||
    additionalTextFileNames.some((entry) => typeof entry !== "string" || !entry ||
      entry === "." || entry === ".." || /[\\/\u0000-\u001f\u007f]/u.test(entry))) {
    throw new Error("Additional static scope text filenames must be plain filenames.");
  }
  const excludedRoots = excludePaths.map((entry) => {
    const absolute = path.resolve(root, entry);
    const relative = path.relative(root, absolute);
    if (!relative || path.isAbsolute(relative) || relative === ".." ||
      relative.startsWith(`..${path.sep}`)) {
      throw new Error("Static scope exclusions must be strict descendants of the codebase.");
    }
    return absolute;
  });
  const files = [];
  const skippedFiles = [];
  const budget = {
    deadline: Date.now() + maxDurationMs,
    exhausted: false,
    maxFiles,
    maxTextBytes,
    textBytes: 0,
    excludedRoots,
    textExtensions: new Set([...TEXT_EXTENSIONS, ...additionalTextExtensions.map((entry) => entry.toLowerCase())]),
    textFileNames: new Set([...TEXT_FILE_NAMES, ...additionalTextFileNames]),
  };
  onProgress?.({ phase: "walk-start", root });
  await walk(root, root, files, skippedFiles, budget);
  onProgress?.({
    phase: "walk-complete",
    files: files.length,
    skippedFiles: skippedFiles.length,
  });
  const technologies = new Map();
  const activeDatabaseFindings = [];
  const activeOracleFindings = [];
  const passiveArtifacts = [];
  const buildReferenceText = [];
  const scannedFileDigests = [];
  let scannedFiles = 0;

  onProgress?.({ phase: "scan-start", files: files.length });
  for (const [index, file] of files.entries()) {
    if (Date.now() > budget.deadline) {
      skippedFiles.push({
        path: file.relativePath,
        reason: `Static scope scan exceeded ${maxDurationMs}-millisecond duration limit with ${files.length - index} candidates remaining.`,
      });
      onProgress?.({
        phase: "scan-limit",
        current: index,
        files: files.length,
        reason: "duration",
      });
      break;
    }
    if (verboseProgress) {
      onProgress?.({
        phase: "scan-file",
        current: index + 1,
        files: files.length,
        path: file.relativePath,
      });
    } else if (index === 0 || (index + 1) % 100 === 0) {
      onProgress?.({
        phase: "scan-progress",
        current: index + 1,
        files: files.length,
      });
    }
    if (file.kind === "artifact") {
      passiveArtifacts.push({
        path: file.relativePath,
        kind: file.extension.slice(1) || "artifact",
        classification: "reference-not-proven",
        action: "retained",
        evidence: [
          "Checked-in artifact detected; active use requires independent build/package evidence.",
        ],
      });
      scannedFiles += 1;
      continue;
    }

    let content;
    let bytes;
    try {
      bytes = await readFile(file.absolutePath);
      content = maskSourceComments(new TextDecoder("utf-8", { fatal: true }).decode(bytes), file.relativePath);
    } catch (error) {
      skippedFiles.push({
        path: file.relativePath,
        reason: `Unreadable text candidate: ${error.code ?? error.message}`,
      });
      continue;
    }
    scannedFileDigests.push({
      path: file.relativePath,
      sha256: createHash("sha256").update(bytes).digest("hex"),
    });
    if (isBuildDescriptor(file.relativePath)) {
      buildReferenceText.push(`${file.relativePath}\n${content}`);
    }
    detectTechnologies(file, content, technologies);
    if (
      isConfigurationFile(file) &&
      /\bdb[_-](?:host|name|url|user)\b/i.test(content)
    ) {
      activeDatabaseFindings.push({
        category: "Datasource/connection configuration",
        path: file.relativePath,
        evidence: matchedLine(
          content,
          /\bdb[_-](?:host|name|url|user)\b/i,
        ),
      });
    }
    for (const [category, pattern] of ACTIVE_DATABASE_RULES) {
      if (pattern.test(content)) {
        activeDatabaseFindings.push({
          category,
          path: file.relativePath,
          evidence: matchedLine(content, pattern),
        });
      }
    }
    for (const [category, pattern] of ACTIVE_ORACLE_RULES) {
      if (pattern.test(content)) {
        activeOracleFindings.push({
          category,
          path: file.relativePath,
          evidence: matchedLine(content, pattern),
        });
      }
    }
    const sqlContexts = SQL_LIKE_EXTENSIONS.has(file.extension)
      ? [content]
      : EMBEDDED_SQL_EXTENSIONS.has(file.extension)
        ? extractEmbeddedSql(content, { rawStrings: file.extension === ".cs" })
        : [];
    const sqlContext = sqlContexts.join("\n");
    if (sqlContexts.length > 0 && ORACLE_SQL_PATTERN.test(sqlContext)) {
      activeOracleFindings.push({
        category: "Oracle SQL",
        path: file.relativePath,
        evidence: matchedLine(sqlContext, ORACLE_SQL_PATTERN),
      });
    }
    if (
      (SQL_LIKE_EXTENSIONS.has(file.extension) && content.trim().length > 0) ||
      sqlContexts.length > 0
    ) {
      activeDatabaseFindings.push({
        category: "SQL source",
        path: file.relativePath,
        evidence: matchedLine(sqlContext || content, SQL_STATEMENT_PATTERN),
      });
    }
    scannedFiles += 1;
  }
  onProgress?.({ phase: "scan-complete", files: scannedFiles });

  const buildText = buildReferenceText.join("\n");
  for (const artifact of passiveArtifacts) {
    const baseName = path.basename(artifact.path);
    const stem = baseName.slice(0, -path.extname(baseName).length);
    const referenced =
      buildText.includes(baseName) ||
      buildText.includes(artifact.path) ||
      buildText.includes(stem);
    artifact.evidence = referenced
      ? ["Artifact name appears in a scanned build/package descriptor."]
      : ["No artifact name reference found in scanned build/package descriptors."];
    artifact.classification = referenced
      ? "referenced-artifact"
      : "reference-not-proven";
  }

  const activeDatabase = deduplicateFindings(activeDatabaseFindings);
  const activeOracle = deduplicateFindings(activeOracleFindings);
  const noConversionRequired =
    activeDatabase.length === 0 &&
    activeOracle.length === 0 &&
    skippedFiles.length === 0;
  const scopeReviewRequired =
    activeDatabase.length === 0 &&
    activeOracle.length === 0 &&
    (skippedFiles.length > 0 ||
      files.length === 0 ||
      technologies.size === 0);
  const confirmedNoConversion =
    noConversionRequired && !scopeReviewRequired;
  onProgress?.({ phase: "git-baseline-start" });
  const gitBaseline = captureBaseline
    ? readGitStatus(root) : { available: false, entries: [] };
  onProgress?.({
    phase: "git-baseline-complete",
    available: gitBaseline.available,
    entries: gitBaseline.entries.length,
  });
  onProgress?.({ phase: "execution-baseline-start" });
  const executionArtifactBaseline = captureBaseline
    ? await readExecutionArtifactBaseline(root) : null;
  onProgress?.({ phase: "execution-baseline-complete" });
  const result = {
    schemaVersion: 1,
    status: scopeReviewRequired
      ? "SCOPE_REVIEW_REQUIRED"
      : confirmedNoConversion
        ? "NO_CONVERSION_REQUIRED"
        : "MIGRATION_REQUIRED",
    reason: scopeReviewRequired
      ? "Potentially migration-sensitive files could not be inspected or application technology could not be identified."
      : confirmedNoConversion
        ? "No active database-facing or Oracle-specific application references were found."
        : "Active database-facing or Oracle-specific application references were found.",
    scanLimits: {
      maxFiles,
      maxTextBytes,
      maxDurationMs,
      candidateFiles: files.length,
      candidateTextBytes: budget.textBytes,
    },
    scannedFiles,
    scannedAt: new Date().toISOString(),
    detectedTechnologies: [...technologies.entries()].map(([name, evidence]) => ({
      name,
      evidencePaths: [...evidence].sort(),
    })),
    activeDatabaseFindings: activeDatabase,
    activeOracleFindings: activeOracle,
    passiveArtifacts,
    skippedFiles,
    scannedFileDigests: scannedFileDigests.sort((left, right) =>
      left.path.localeCompare(right.path),
    ),
    gitBaselineAvailable: gitBaseline.available,
    baselineGitStatus: gitBaseline.entries,
    executionArtifactBaseline,
    executionSuppression: confirmedNoConversion || scopeReviewRequired
      ? {
          dependencyInstallationAttempted: false,
          baselineBuildAttempted: false,
          testsAttempted: false,
          coverageAttempted: false,
          testGenerationAttempted: false,
          sourceChangesAttempted: false,
          dockerAttempted: false,
          targetDatabaseCallsAttempted: false,
          ddlAttempted: false,
        }
      : null,
  };
  result.inventoryDigest = scopeInventoryDigest(result);
  onProgress?.({
    phase: "complete",
    status: result.status,
    scannedFiles: result.scannedFiles,
  });
  return result;
}

export function validateNoConversionExecution(scopeDecision) {
  const errors = [];
  if (scopeDecision?.status !== "NO_CONVERSION_REQUIRED") {
    errors.push("scope decision is not NO_CONVERSION_REQUIRED");
    return errors;
  }
  if ((scopeDecision.activeDatabaseFindings ?? []).length > 0) {
    errors.push("active database findings contradict no-conversion decision");
  }
  if ((scopeDecision.activeOracleFindings ?? []).length > 0) {
    errors.push("active Oracle findings contradict no-conversion decision");
  }
  if ((scopeDecision.detectedTechnologies ?? []).length === 0) {
    errors.push("detected application technology evidence is required");
  }
  if ((scopeDecision.skippedFiles ?? []).length > 0) {
    errors.push("no-conversion decision contains uninspected relevant files");
  }
  const suppression = scopeDecision.executionSuppression;
  if (
    !suppression ||
    Object.values(suppression).some((attempted) => attempted !== false)
  ) {
    errors.push("no-conversion execution attempted prohibited build/test/database work");
  }
  for (const artifact of scopeDecision.passiveArtifacts ?? []) {
    if (artifact.action !== "retained") {
      errors.push(`passive artifact was not retained: ${artifact.path}`);
    }
  }
  return errors;
}

async function walk(root, directory, files, skippedFiles, budget) {
  if (budget.exhausted) return;
  if (Date.now() > budget.deadline) {
    skippedFiles.push({
      path: path.relative(root, directory).replaceAll("\\", "/") || ".",
      reason: "Static scope traversal exceeded the duration limit.",
    });
    budget.exhausted = true;
    return;
  }
  let entries;
  try {
    entries = await readdir(directory, { withFileTypes: true });
  } catch (error) {
    skippedFiles.push({
      path: path.relative(root, directory).replaceAll("\\", "/") || ".",
      reason: `Unreadable directory: ${error.code ?? error.message}`,
    });
    return;
  }
  for (const entry of entries) {
    if (Date.now() > budget.deadline) {
      skippedFiles.push({
        path: path.relative(root, directory).replaceAll("\\", "/") || ".",
        reason: "Static scope traversal exceeded the duration limit.",
      });
      budget.exhausted = true;
      return;
    }
    if (
      entry.isFile() &&
      /^(?:\.gitignore|\.gitattributes|\.editorconfig|\.eslint.*|\.prettier.*|\.npmrc|\.nvmrc|\.browserslistrc)$/i.test(
        entry.name,
      )
    ) {
      continue;
    }
    if (
      entry.isDirectory() &&
      EXCLUDED_DIRECTORIES.has(entry.name.toLowerCase())
    ) {
      continue;
    }
    const absolutePath = path.join(directory, entry.name);
    if (budget.excludedRoots.some((excluded) => {
      const relative = path.relative(excluded, absolutePath);
      return relative === "" || (!path.isAbsolute(relative) &&
        relative !== ".." && !relative.startsWith(`..${path.sep}`));
    })) {
      continue;
    }
    const relativeDirectoryPath = path
      .relative(root, absolutePath)
      .replaceAll("\\", "/");
    if (
      entry.isDirectory() &&
      relativeDirectoryPath === ".github/migrate-oracle-app"
    ) {
      continue;
    }
    if (entry.isDirectory()) {
      await walk(root, absolutePath, files, skippedFiles, budget);
      if (budget.exhausted) return;
      continue;
    }
    if (!entry.isFile()) {
      skippedFiles.push({
        path: path.relative(root, absolutePath).replaceAll("\\", "/"),
        reason: "Non-regular filesystem entry requires manual scope review.",
      });
      continue;
    }
    const extension = path.extname(entry.name).toLowerCase();
    const relativePath = path.relative(root, absolutePath).replaceAll("\\", "/");
    const textCandidate =
      budget.textExtensions.has(extension) ||
      budget.textFileNames.has(entry.name) ||
      entry.name.startsWith(".env");
    if (!textCandidate && !ARTIFACT_EXTENSIONS.has(extension)) {
      if (INERT_FILE_NAMES.has(entry.name)) continue;
      if (!INERT_EXTENSIONS.has(extension)) {
        skippedFiles.push({
          path: relativePath,
          reason: "Unknown non-inert file extension requires manual scope review.",
        });
      }
      continue;
    }
    let metadata;
    try {
      metadata = await stat(absolutePath);
    } catch (error) {
      skippedFiles.push({
        path: relativePath,
        reason: `File metadata unavailable: ${error.code ?? error.message}`,
      });
      continue;
    }
    if (metadata.size > 2_000_000 && !ARTIFACT_EXTENSIONS.has(extension)) {
      skippedFiles.push({
        path: relativePath,
        reason: `Text candidate exceeds 2000000-byte static scan limit (${metadata.size}).`,
      });
      continue;
    }
    if (files.length >= budget.maxFiles) {
      skippedFiles.push({
        path: relativePath,
        reason: `Static scope traversal exceeded ${budget.maxFiles}-file candidate limit.`,
      });
      budget.exhausted = true;
      return;
    }
    if (
      !ARTIFACT_EXTENSIONS.has(extension) &&
      budget.textBytes + metadata.size > budget.maxTextBytes
    ) {
      skippedFiles.push({
        path: relativePath,
        reason: `Static scope traversal exceeded ${budget.maxTextBytes}-byte text limit.`,
      });
      budget.exhausted = true;
      return;
    }
    if (!ARTIFACT_EXTENSIONS.has(extension)) {
      budget.textBytes += metadata.size;
    }
    files.push({
      absolutePath,
      relativePath,
      extension,
      kind: ARTIFACT_EXTENSIONS.has(extension) ? "artifact" : "text",
    });
  }
}

function detectTechnologies(file, content, technologies) {
  const name = path.basename(file.relativePath);
  const extension = file.extension;
  const rules = [];
  if (
    name === "angular.json" ||
    [".js", ".jsx", ".ts", ".tsx"].includes(extension)
  ) {
    rules.push(["Angular", /@angular\/core|angular\.json/i]);
    rules.push(["AngularJS", /angular\.module\s*\(|angular\.min\.js|angularjs/i]);
    rules.push(["React", /from\s+["']react["']|require\(["']react["']\)/i]);
  }

  if (name === "package.json") {
    rules.push(["Node.js", /"scripts"\s*:|"dependencies"\s*:/i]);
    rules.push(["Angular", /"@angular\/core"\s*:/i]);
    rules.push(["React", /"react"\s*:/i]);
    rules.push(["Vue", /"vue"\s*:/i]);
  }
  if (extension === ".vue") rules.push(["Vue", /./]);
  if (
    extension === ".java" ||
    name === "pom.xml" ||
    /^build\.gradle(?:\.kts)?$/i.test(name)
  ) {
    rules.push(["Java", /./]);
  }
  if ([".cs", ".csproj", ".fs", ".fsproj", ".vb", ".vbproj", ".sln", ".slnx"].includes(extension)) {
    rules.push([".NET", /./]);
  }
  if (
    extension === ".py" ||
    name === "pyproject.toml" ||
    /^requirements.*\.txt$/i.test(name)
  ) {
    rules.push(["Python", /./]);
  }
  if (
    [".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"].includes(extension) ||
    ["CMakeLists.txt", "Makefile"].includes(name)
  ) {
    rules.push(["C/C++", /./]);
  }
  if (extension === ".go" || name === "go.mod") rules.push(["Go", /./]);
  if (extension === ".rs" || name === "Cargo.toml") rules.push(["Rust", /./]);
  for (const [technology, pattern] of rules) {
    if (!pattern.test(`${file.relativePath}\n${content}`)) continue;
    const evidence = technologies.get(technology) ?? new Set();
    evidence.add(file.relativePath);
    technologies.set(technology, evidence);
  }
}

export function extractEmbeddedSql(content, { includeRoutineCalls = false, rawStrings = false } = {}) {
  const contexts = [];
  let index = 0;
  while (index < content.length) {
    if (content.startsWith("//", index)) {
      index = skipToLineEnd(content, index + 2);
      continue;
    }
    if (content.startsWith("/*", index)) {
      const close = content.indexOf("*/", index + 2);
      index = close < 0 ? content.length : close + 2;
      continue;
    }
    if (
      content[index] === "#" &&
      (index === 0 || content[index - 1] === "\n" || /\s/.test(content[index - 1]))
    ) {
      index = skipToLineEnd(content, index + 1);
      continue;
    }
    const literal = sourceLiteralAt(content, index, { maxContentLength: MAX_EMBEDDED_LITERAL_LENGTH, rawStrings });
    if (!literal) {
      index += 1;
      continue;
    }
    index = literal.end;
    if (!literal.terminated || literal.oversized) continue;
    const body = content.slice(literal.contentStart, literal.contentEnd);
    const candidate = literal.verbatim ? body.replaceAll('""', '"') : body;
    if (isLikelySql(candidate) || (includeRoutineCalls &&
      /^\s*(?:\{\s*)?(?:CALL|EXEC|EXECUTE)\b/i.test(candidate))) {
      contexts.push(candidate);
    }
  }
  return contexts;
}

function skipToLineEnd(content, index) {
  const newline = content.indexOf("\n", index);
  return newline < 0 ? content.length : newline + 1;
}

function isLikelySql(candidate) {
  if (
    /\b(?:INSERT\s+INTO|UPDATE\s+[\w."]+\s+SET|DELETE\s+FROM|MERGE\s+INTO|CREATE\s+(?:TABLE|VIEW|SEQUENCE|FUNCTION|PROCEDURE|TRIGGER)|CALL\s+[\w."]+\s*\()\b/i.test(
      candidate,
    )
  ) {
    return true;
  }
  const select = /\bSELECT\s+/i.exec(candidate);
  if (!select) return false;
  const expressionStart = select.index + select[0].length;
  const from = /\s+FROM\s+([\w."]+)/i.exec(candidate.slice(expressionStart));
  if (!from) return false;
  const expression = candidate.slice(expressionStart, expressionStart + from.index)
    .replace(/["']/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
  const relation = from[1].replace(/["']/g, "").toLowerCase();
  const proseExpressions = new Set([
    "a value",
    "an item",
    "the item",
    "a value from the dropdown list",
  ]);
  const proseRelations = new Set([
    "a",
    "an",
    "the",
    "your",
    "dropdown",
    "list",
  ]);
  return !proseExpressions.has(expression) && !proseRelations.has(relation);
}

function deduplicateFindings(findings) {
  const seen = new Set();
  return findings.filter((finding) => {
    const key = `${finding.category}\0${finding.path}\0${finding.evidence}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function matchedLine(content, pattern) {
  return (
    content
      .split(/\r?\n/)
      .find((line) => pattern.test(line))
      ?.trim()
      .slice(0, 240) ?? "Pattern detected."
  );
}

function isBuildDescriptor(relativePath) {
  return /(?:^|\/)(?:pom\.xml|build\.xml|build\.gradle(?:\.kts)?|package\.json|.*\.(?:csproj|fsproj|vbproj|vcxproj|sln|slnx|props|targets)|CMakeLists\.txt|Makefile|assembly\.xml|\.classpath|MANIFEST\.MF|web\.xml)$/i.test(
    relativePath,
  );
}

function isConfigurationFile(file) {
  return (
    [
      ".cfg",
      ".conf",
      ".config",
      ".env",
      ".ini",
      ".properties",
      ".toml",
      ".xml",
      ".yaml",
      ".yml",
    ].includes(file.extension) ||
    path.basename(file.relativePath).startsWith(".env")
  );
}

function readGitStatus(root) {
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
    {
      encoding: "utf8",
      windowsHide: true,
      timeout: 10_000,
      maxBuffer: 5 * 1024 * 1024,
    },
  );
  if (result.status !== 0) return { available: false, entries: [] };
  return {
    available: true,
    entries: result.stdout
      .split(/\r?\n/)
      .filter(Boolean)
      .filter((line) => !line.includes(".github/migrate-oracle-app/"))
      .sort(),
  };
}

export async function readExecutionArtifactBaseline(root) {
  const entries = [];
  for (const relativePath of EXECUTION_ARTIFACT_PATHS) {
    try {
      const metadata = await stat(path.join(root, relativePath));
      entries.push({
        path: relativePath,
        exists: true,
        mtimeMs: Math.trunc(metadata.mtimeMs),
        treeDigest: await executionTreeDigest(
          path.join(root, relativePath),
          relativePath,
        ),
      });
    } catch {
      entries.push({
        path: relativePath,
        exists: false,
        mtimeMs: null,
        treeDigest: null,
      });
    }
  }
  return entries;
}

async function executionTreeDigest(target, relativePath) {
  const hash = createHash("sha256");
  await hashExecutionEntry(hash, target, relativePath);
  return hash.digest("hex");
}

async function hashExecutionEntry(hash, target, relativePath) {
  const metadata = await lstat(target);
  hash.update(
    `${relativePath.replaceAll("\\", "/")}\0${metadata.size}\0${Math.trunc(metadata.mtimeMs)}\0`,
  );
  if (!metadata.isDirectory() || metadata.isSymbolicLink()) return;
  const entries = await readdir(target, { withFileTypes: true });
  for (const entry of entries.sort((left, right) =>
    left.name.localeCompare(right.name),
  )) {
    await hashExecutionEntry(
      hash,
      path.join(target, entry.name),
      path.join(relativePath, entry.name),
    );
  }
}

function scopeInventoryDigest(result) {
  return createHash("sha256")
    .update(
      JSON.stringify({
        scannedFiles: result.scannedFiles,
        scanLimits: result.scanLimits,
        detectedTechnologies: result.detectedTechnologies,
        activeDatabaseFindings: result.activeDatabaseFindings,
        activeOracleFindings: result.activeOracleFindings,
        passiveArtifacts: result.passiveArtifacts,
        skippedFiles: result.skippedFiles,
        scannedFileDigests: result.scannedFileDigests,
        gitBaselineAvailable: result.gitBaselineAvailable,
        baselineGitStatus: result.baselineGitStatus,
        executionArtifactBaseline: result.executionArtifactBaseline,
      }),
    )
    .digest("hex");
}

async function main() {
  const args = process.argv.slice(2);
  const verboseProgress = args.includes("--verbose-progress");
  const quiet = args.includes("--quiet");
  const positional = args.filter((argument) => !argument.startsWith("--"));
  const root = positional[0];
  const output = positional[1];
  if (!root) {
    throw new Error(
      "Usage: node detect-migration-scope.mjs <codebase> [output.json] [--verbose-progress|--quiet]",
    );
  }
  const startedAt = Date.now();
  const onProgress = quiet
    ? null
    : (event) => {
        const fields = Object.entries(event)
          .filter(([key]) => key !== "phase" && key !== "root")
          .map(([key, value]) => `${key}=${value}`)
          .join(" ");
        process.stderr.write(
          `[scope] ${event.phase} elapsedMs=${Date.now() - startedAt}${fields ? ` ${fields}` : ""}\n`,
        );
      };
  const result = await detectMigrationScope(root, {
    onProgress,
    verboseProgress,
  });
  const serialized = `${JSON.stringify(result, null, 2)}\n`;
  if (output) await writeFile(path.resolve(output), serialized, "utf8");
  process.stdout.write(serialized);
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
