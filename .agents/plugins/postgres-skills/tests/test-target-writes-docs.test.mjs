import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const requiredDocuments = [
  "SKILL.md",
  "references/options.md",
  "references/database-safety.md",
  "references/connection-context.md",
  "references/postgres-mcp.md",
  "references/conversion.md",
  "references/state-and-convergence.md",
];

test("the skill identity matches its directory and application-migration scope", async () => {
  const skill = await readSkillFile("SKILL.md");

  assert.match(skill, /^name: migrate-oracle-app$/m);
  assert.match(skill, /^# Oracle to PostgreSQL Application Migration$/m);
});

test("migration instructions consistently resolve skill-local script paths", async () => {
  const skill = await readSkillFile("SKILL.md");
  const assessment = await readSkillFile("references/assessment.md");

  for (const content of [skill, assessment]) {
    assert.ok(content.includes("<skill-directory>\\scripts\\detect-migration-scope.mjs"));
    assert.match(content, /absolute path/i);
    assert.match(content, /regardless\s+of the current working directory/i);
    assert.doesNotMatch(content, /Do not run it from|detector is repository-level/i);
    assert.doesNotMatch(content, /<plugin-root>[\\/]+scripts|<skill-directory>\\\.\.\\\.\.\\scripts/);
  }

  const detector = new URL("../skills/migrate-oracle-app/scripts/detect-migration-scope.mjs", import.meta.url);
  const root = await mkdtemp(path.join(os.tmpdir(), "skill-local-detector-test-"));
  try {
    await writeFile(path.join(root, "App.java"), 'class App { String sql = "SELECT 1 FROM DUAL"; }\n');
    const result = JSON.parse(execFileSync(process.execPath, [fileURLToPath(detector), root], {
      cwd: root,
      encoding: "utf8",
      stdio: ["pipe", "pipe", "pipe"],
    }));
    assert.equal(result.status, "MIGRATION_REQUIRED");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("every required skill document defines test-target-writes", async () => {
  for (const relativePath of requiredDocuments) {
    const content = await readSkillFile(relativePath);
    assert.match(content, /test-target-writes/, `${relativePath} omits test-target-writes`);
  }
});

test("the documented validation report has three independent database gates", async () => {
  const conversion = await readSkillFile("references/conversion.md");

  assert.match(conversion, /Docker PostgreSQL runtime validation/);
  assert.match(conversion, /Authoritative target contract validation/);
  assert.match(conversion, /Authoritative target runtime\/write validation/);
  assert.match(conversion, /✅ Passed/);
  assert.match(conversion, /⚠️ Passed with warnings/);
  assert.doesNotMatch(conversion, /✅ \*\*PASSED\*\*/);
  assert.doesNotMatch(conversion, /⚠️ \*\*PASSED WITH WARNINGS\*\*/);
});

test("target-write safety forbids destructive DDL and requires cleanup proof", async () => {
  const safety = await readSkillFile("references/database-safety.md");

  for (const operation of ["TRUNCATE", "ALTER", "REPLACE", "DROP"]) {
    assert.match(safety, new RegExp(`\\b${operation}\\b`));
  }
  assert.match(safety, /zero marker-owned rows remain/);
  assert.match(safety, /Sequence advancement cannot\s+be rolled back or rewound/);
  assert.match(safety, /### Database Objects Exercised with Synthetic Data/);
  assert.match(safety, /Always include functions and procedures even when both counts are zero/);
  assert.match(safety, /### Synthetic Test Data/);
  assert.match(safety, /Do not include a cleanup-status legend/);
  assert.doesNotMatch(safety, /### Fixture Row Ledger/);
  assert.match(
    safety,
    /\| Object \/ signature \| Kind \| Production entry point \| Synthetic fixtures \| Invocation form \| Assertions \/ side effects \| Environment \| Result \|/,
  );
  assert.match(safety, /Standalone SQL setup or catalog discovery never counts/i);
});

test("requested target writes must execute or make conversion partial", async () => {
  const skill = await readSkillFile("SKILL.md");
  const options = await readSkillFile("references/options.md");
  const safety = await readSkillFile("references/database-safety.md");
  const state = await readSkillFile("references/conversion-state-schema.md");
  const template = await readSkillFile(
    "references/conversion-report-template.md",
  );

  for (const contract of [skill, options, safety, state]) {
    assert.match(contract, /not-executed/i);
    assert.match(contract, /PARTIAL/);
  }
  assert.match(options, /request a\s+write-enabled saved profile/i);
  assert.match(state, /targetWriteTests/);
  assert.match(state, /mcpWrites/);
  assert.match(template, /### Target Write Test Reconciliation/);
  assert.match(template, /Recorded write operations/);
  assert.match(template, /⚠️ Not executed/);
});

test("query transaction isolation is not profile write capability", async () => {
  const skill = await readSkillFile("SKILL.md");
  const connection = await readSkillFile("references/connection-context.md");
  const safety = await readSkillFile("references/database-safety.md");
  const options = await readSkillFile("references/options.md");
  const state = await readSkillFile(
    "references/conversion-state-schema.md",
  );

  for (const contract of [skill, connection, safety, options]) {
    assert.match(contract, /postgres_mcp_query/);
    assert.match(contract, /transaction_read_only=on/);
    assert.match(contract, /read-only[\s\S]{0,40}transaction|inspection-tool isolation/i);
    assert.match(contract, /postgres_mcp_modify/);
    assert.match(contract, /default_transaction_read_only/);
  }
  assert.match(state, /inspectionTransactionReadOnly/);
  assert.match(state, /defaultTransactionReadOnly/);
  assert.match(state, /modifyToolAvailable/);
});

test("PostgreSQL provider selection is deterministic by Copilot harness", async () => {
  const skill = await readSkillFile("SKILL.md");
  const routing = await readSkillFile(
    "references/postgres-provider-routing.md",
  );
  const connection = await readSkillFile("references/connection-context.md");
  const state = await readSkillFile(
    "references/conversion-state-schema.md",
  );
  const policy = await readRepoFile("skills/migrate-oracle-app/scripts/postgres-provider-policy.mjs");

  for (const contract of [skill, routing]) {
    assert.match(contract, /VS Code.*Copilot Session Target/is);
    assert.match(contract, /PostgreSQL MCP/);
    assert.match(contract, /pgsql_/);
    assert.match(contract, /Copilot CLI/);
    assert.match(contract, /postgres-mcp/);
    assert.match(contract, /postgres_mcp_/);
    assert.match(contract, /never fallback|do not fall back|Never fallback/i);
  }
  assert.match(routing, /Node\.js 22\+/);
  assert.match(routing, /Never mix connection profiles/i);
  assert.match(connection, /Treat VS Code extension profiles and NPM MCP profiles as separate/);
  assert.match(state, /"postgresProvider"/);
  assert.match(state, /"fallbackAllowed": false/);
  assert.match(state, /"mixedProviderOperationCount": 0/);
  assert.match(policy, /export function resolvePostgresProvider/);
});

test("deferred PostgreSQL discovery requires exact names and individual missing-operation probes", async () => {
  const routing = await readSkillFile("references/postgres-provider-routing.md");
  const skill = await readSkillFile("SKILL.md");
  const connection = await readSkillFile("references/connection-context.md");
  const postgres = await readSkillFile("references/postgres-mcp.md");
  const schema = await readSkillFile("references/conversion-state-schema.md");

  for (const content of [routing]) {
    for (const family of ["pgsql", "postgres_mcp"]) {
      const query = [
        "list_connection_profiles",
        "connect",
        "list_databases",
        "get_server_capabilities",
        "query",
        "modify",
        "disconnect",
      ].map((operation) => `${family}_${operation}`).join(" ");
      assert.ok(content.includes(query));
    }
    assert.match(content, /bounded, not a complete inventory/);
    assert.match(content, /individually|separately/);
    assert.match(content, /one exact-name batch plus one.*probe per/i);
  }
  assert.match(routing, /planPostgresToolDiscovery/);
  assert.match(routing, /search-required/);
  assert.match(routing, /search-failed/);
  for (const content of [skill, connection, postgres]) {
    assert.match(content, /exact-name|exact full function-name/);
    assert.match(content, /individual/);
    assert.match(content, /bounded/);
  }
  assert.match(schema, /"discovery"/);
  assert.match(schema, /"strategy": "exact-operation-names"/);
  assert.match(schema, /"returnedTools"/);
});

test("PostgreSQL database-context tools are unconditionally prohibited", async () => {
  const skill = await readSkillFile("SKILL.md");
  const postgres = await readSkillFile("references/postgres-mcp.md");
  const assessment = await readSkillFile("references/assessment.md");
  const connection = await readSkillFile("references/connection-context.md");
  const safety = await readSkillFile("references/database-safety.md");
  const state = await readSkillFile("references/conversion-state-schema.md");

  for (const contract of [
    skill,
    postgres,
    assessment,
    connection,
    safety,
  ]) {
    assert.match(contract, /postgres_mcp_db_context/);
    assert.match(contract, /never call|prohibited/i);
    assert.match(contract, /bounded/i);
  }
  assert.match(postgres, /function name ends in `_db_context`/);
  assert.match(postgres, /one exact schema/i);
  assert.match(postgres, /current_database\(\)/);
  assert.match(postgres, /pg_catalog\.pg_namespace/);
  assert.match(postgres, /pg_catalog\.pg_attribute/);
  assert.match(postgres, /pg_catalog\.pg_proc/);
  assert.match(postgres, /at most 10 names[\s\S]{0,50}20 is the hard maximum/);
  assert.match(
    postgres,
    /at most 5 returned relation\/routine OIDs[\s\S]{0,50}10 is the hard\s+maximum/,
  );
  assert.doesNotMatch(postgres, /may be used\s+only for one exact object/i);
  assert.match(postgres, /produce a fresh\s+run with zero `_db_context` calls/i);
  assert.match(state, /"dbContextCalls": 0/);
  assert.match(state, /"schemaWideCatalogQueries": 0/);
  assert.match(state, /policy violation/i);
});

test("missing objects prefer Oracle DDL and disclose best-effort reconstruction", async () => {
  const skill = await readSkillFile("SKILL.md");
  const source = await readSkillFile("references/oracle-ddl-source.md");
  const safety = await readSkillFile("references/database-safety.md");
  const assessment = await readSkillFile("references/assessment.md");
  const state = await readSkillFile("references/conversion-state-schema.md");
  const assessmentTemplate = await readSkillFile(
    "references/assessment-report-template.md",
  );
  const conversionTemplate = await readSkillFile(
    "references/conversion-report-template.md",
  );
  const packageManifest = JSON.parse(await readRepoFile("package.json"));
  const indexer = await readRepoFile("skills/migrate-oracle-app/scripts/index-oracle-ddl.mjs");

  assert.match(skill, /oracle-ddl-folder=<path>/);
  assert.match(
    source,
    /Every file has exactly one primary object whose kind matches its containing object-type\s+directory/i,
  );
  assert.match(
    source,
    /\.github\/postgres-migrations\/<migration-name>\/artifacts\/oracle\/<SCHEMA>/,
  );
  assert.match(source, /arbitrary\s+end-user-provided directory name/i);
  assert.match(source, /example, not a required set/i);
  assert.match(source, /at least one non-hidden object-type\s+directory/i);
  assert.match(source, /Unknown or new folder names remain valid/i);
  assert.match(source, /--schema "<schema>"/);
  assert.match(skill, /migration-project\/ancestor root/i);
  assert.match(source, /Convert only code-derived objects that are missing/i);
  assert.match(source, /## Best-Effort Reconstruction When Source DDL Is Unavailable/);
  assert.match(source, /create an explicit fail-closed implementation/i);
  assert.match(source, /representative synthetic input values/i);
  assert.match(safety, /documented best-effort reconstruction evidence/i);
  assert.match(
    assessment,
    /uniquely matched extracted Oracle DDL is authoritative for\s+source behavior/i,
  );
  assert.match(state, /"oracleDdlSource"/);
  assert.match(state, /"layout": "canonical-postgres-migrations"/);
  assert.match(state, /"migrationName": "<user-provided-name>"/);
  assert.match(state, /"objectTypeDirectories"/);
  assert.match(state, /"objectTypeDirectory": "PROCEDURE"/);
  assert.match(state, /best-effort-reconstruction/);
  assert.match(state, /"customerValidationRequired": false/);
  assert.match(assessmentTemplate, /### Extracted Oracle DDL Source/);
  assert.match(conversionTemplate, /### Oracle DDL Source Reconciliation/);
  assert.match(conversionTemplate, /Body completeness \/ confidence/);
  assert.match(indexer, /export async function indexOracleDdlFolder/);
  assert.match(packageManifest.scripts["index:oracle-ddl"], /index-oracle-ddl\.mjs/);
});

test("persistent DDL requires selected-target creation after reconstruction", async () => {
  const documents = await Promise.all(
    [
      "SKILL.md",
      "references/options.md",
      "references/conversion.md",
      "references/database-safety.md",
      "references/oracle-ddl-source.md",
      "references/conversion-state-schema.md",
      "references/conversion-report-template.md",
    ].map(readSkillFile),
  );
  for (const content of documents) {
    assert.match(content, /create-persistent-ddl/);
    assert.match(content, /verif/i);
  }
  const [skill, options, conversion, safety, source, state, template] = documents;
  assert.match(skill, /Generating scripts[\s\S]{0,100}never satisfies the option/);
  assert.match(options, /SQL files alone are not the\s+requested result/);
  assert.match(conversion, /Generating SQL files[\s\S]{0,100}never satisfies/);
  assert.match(safety, /deploy\/undo files[\s\S]{0,80}cannot satisfy it/);
  assert.match(source, /Generating deploy\/undo files[\s\S]{0,80}not completion/);
  assert.match(state, /Do not record a validated[\s\S]{0,180}files were generated/);
  assert.match(template, /not just SQL generation or Docker deployment/);
  for (const content of [skill, options, conversion, safety, source]) {
    assert.match(content, /(?:pgsql_modify|selected provider's modify operation)/);
    assert.match(content, /(?:independent of|never requires|do not require|Do not stop[\s\S]{0,100}require|or require)[\s\S]{0,100}`?test-target-writes`?/i);
  }
  for (const content of [skill, options, conversion, safety, state]) {
    assert.match(content, /not-executed/i);
    assert.match(content, /PARTIAL/);
  }
  assert.match(conversion, /## Persistent Missing-Object Deployment/);
  assert.ok(
    conversion.indexOf("## Persistent Missing-Object Deployment") <
      conversion.indexOf("## Authoritative Target Runtime Testing"),
  );
  assert.match(state, /"create-persistent-ddl": false/);
  assert.match(state, /"persistentCreate"/);
  assert.match(state, /generatedDeploySha256/);
  assert.match(state, /selectedTools\.modify/);
  assert.match(state, /selectedTools\.query/);
  assert.doesNotMatch(source, /generated object may be\s+created/i);
});

test("active report contracts do not request token usage metrics", async () => {
  const reportContracts = [
    "skills/migrate-oracle-app/SKILL.md",
    "skills/migrate-oracle-app/references/assessment.md",
    "skills/migrate-oracle-app/references/conversion.md",
    "skills/migrate-oracle-app/references/state-and-convergence.md",
  ];
  const tokenMetricPattern =
    /token usage|token tables|token counts|inputTokens|outputTokens|totalTokens|input tokens|output tokens|total tokens/i;

  for (const relativePath of reportContracts) {
    const content = await readRepoFile(relativePath);
    assert.doesNotMatch(
      content,
      tokenMetricPattern,
      `${relativePath} still requires report token metrics`,
    );
  }
});

test("run metrics tables consistently use readable timestamps and wall-clock columns", async () => {
  const state = await readSkillFile("references/state-and-convergence.md");
  const reportContracts = [
    "skills/migrate-oracle-app/SKILL.md",
    "skills/migrate-oracle-app/references/state-and-convergence.md",
  ];
  const templates = [
    "references/assessment-report-template.md",
    "references/conversion-report-template.md",
    "references/no-conversion-assessment-template.md",
    "references/no-conversion-report-template.md",
    "references/scope-review-assessment-template.md",
    "references/scope-review-report-template.md",
  ];

  assert.match(state, /\| Phase \| Start \| Completed \| Wall Clock Duration \|/);
  assert.match(state, /4:10pm, June 10, 2026/);
  assert.match(state, /1 hour, 54 minutes, 30 seconds/);
  assert.match(state, /Always include minutes and seconds/);
  assert.match(state, /runMetrics\.timeZone/);
  assert.match(state, /format-run-metrics\.mjs/);
  assert.match(state, /before the\s+two-pass artifact record\/verify checks/);
  for (const relativePath of templates) {
    const content = await readSkillFile(relativePath);
    assert.match(content, /\*\*Time zone:\*\*/);
    assert.match(content, /\| Phase \| Start \| Completed \| Wall Clock Duration \|/);
    for (const phase of ["Assessment", "Conversion", "Total Runs"]) {
      assert.ok(content.includes(`| ${phase} |`), `${relativePath} omits ${phase}`);
    }
    assert.doesNotMatch(content, /\| Entire run \|/);
  }
  for (const relativePath of reportContracts) {
    const content = await readRepoFile(relativePath);
    assert.doesNotMatch(content, /activeExecutionMs|userWaitMs|Active execution|User wait/);
    assert.doesNotMatch(
      content,
      /\| Phase \| Start \| Completed \| Wall Clock Duration \| Status \|/,
    );
    assert.doesNotMatch(content, /`12m 52s`/);
  }
  const packageJson = JSON.parse(await readRepoFile("package.json"));
  assert.match(packageJson.scripts["format:run-metrics"], /format-run-metrics\.mjs/);
  assert.match(packageJson.scripts.test, /run-metrics\.test\.mjs/);
});

test("coverage reports require two waves with value-backed optional continuation", async () => {
  const diagrams = await readSkillFile("references/diagrams.md");
  const coveragePolicy = await readSkillFile("references/testing-and-coverage.md");
  const stateSchema = await readSkillFile(
    "references/conversion-state-schema.md",
  );
  const conversionTemplate = await readSkillFile(
    "references/conversion-report-template.md",
  );
  const activeCoverageContracts = [
    "skills/migrate-oracle-app/SKILL.md",
    "skills/migrate-oracle-app/references/testing-and-coverage.md",
    "skills/migrate-oracle-app/references/diagrams.md",
    "skills/migrate-oracle-app/references/conversion.md",
    "skills/migrate-oracle-app/references/state-and-convergence.md",
  ];

  assert.doesNotMatch(diagrams, /\|\s*Objective\s*\|/i);
  assert.doesNotMatch(diagrams, /TARGET MET/);
  assert.match(
    coveragePolicy,
    /at\s+least two bounded unit\/characterization waves/i,
  );
  assert.match(coveragePolicy, /at\s+least two bounded integration\s+waves/i);
  assert.match(coveragePolicy, /Never exceed ten waves in either lane/i);
  assert.match(coveragePolicy, /unit waves never count toward the integration minimum/i);
  assert.match(stateSchema, /"unitMinimum": 2/);
  assert.match(stateSchema, /"integrationMinimum": 2/);
  assert.match(coveragePolicy, /continuationEvidence/);
  assert.match(coveragePolicy, /validatedTests/);
  assert.match(coveragePolicy, /deferred-low-yield/);
  assert.match(stateSchema, /"unitWaves"/);
  assert.match(stateSchema, /"integrationWaves"/);
  assert.match(
    conversionTemplate,
    /\| Lane \| Wave \| Planned behaviors \| Tests added \/ repaired \|/,
  );
  assert.match(coveragePolicy, /aspirational 80% combined line coverage/i);
  assert.match(coveragePolicy, /use a percentage as a readiness verdict/i);

  for (const relativePath of activeCoverageContracts) {
    const content = await readRepoFile(relativePath);
    assert.doesNotMatch(content, /90%/);
    assert.doesNotMatch(content, /\|\s*Objective\s*\|/i);
  }
});

test("coverage ignore housekeeping requires existing source-edit authorization", async () => {
  const skill = await readSkillFile("SKILL.md");
  const workspace = await readSkillFile("references/git-workspace.md");
  const testing = await readSkillFile("references/testing-and-coverage.md");
  const conversion = await readSkillFile("references/conversion.md");
  const state = await readSkillFile("references/state-and-convergence.md");

  assert.match(workspace, /Only change[\s\S]*?source edits are already authorized/);
  assert.match(workspace, /MIGRATION_REQUIRED/);
  assert.match(workspace, /Read-only assessment must not create or modify `\.gitignore`/);
  assert.match(workspace, /NO_CONVERSION_REQUIRED.*SCOPE_REVIEW_REQUIRED/);
  assert.match(workspace, /append only missing rules/);
  assert.match(workspace, /Preserve existing content and line endings/);
  assert.match(workspace, /repeated runs must not duplicate rules/);
  assert.match(workspace, /explicit unignore rules conflict/);
  assert.match(workspace, /git check-ignore --no-index/);
  assert.match(workspace, /never run `git rm --cached`/);
  assert.match(skill, /After authorization, apply the coverage Git ignore rules/);
  assert.match(skill, /pending coverage ignore edits in the exact baseline-enablement preview/);
  assert.match(testing, /Read-only\s+assessment leaves `\.gitignore` unchanged/);
  assert.match(testing, /Retain the current assessment and conversion coverage locally/);
  assert.match(testing, /Do not delete coverage merely to reduce Git noise/);
  for (const contract of [skill, workspace, testing, conversion, state]) {
    assert.match(contract, /Git-reconciled Changes\s+inventory/);
  }
});

test("documented coverage ignore rules exclude only local coverage and preserve tracked files", async () => {
  const workspace = await readSkillFile("references/git-workspace.md");
  const expectedRules = [
    "/.github/migrate-oracle-app/assessment/coverage/",
    "/.github/migrate-oracle-app/conversions/coverage/",
  ];
  for (const content of [workspace]) {
    const block = content.match(/```gitignore\r?\n([\s\S]*?)\r?\n```/);
    assert.ok(block, "coverage ignore rules must have a gitignore code block");
    assert.deepEqual(block[1].split(/\r?\n/), expectedRules);
  }

  const root = await mkdtemp(path.join(os.tmpdir(), "coverage-ignore-test-"));
  const git = (args, input) =>
    execFileSync("git", ["--no-pager", "-c", "core.excludesFile=", ...args], {
      cwd: root,
      encoding: "utf8",
      input,
    });
  const coveragePaths = [
    ".github/migrate-oracle-app/assessment/coverage/unit/jacoco.exec",
    ".github/migrate-oracle-app/conversions/coverage/waves/combined.xml",
  ];
  const otherPaths = [
    ".github/migrate-oracle-app/assessment/assessment-report.md",
    ".github/migrate-oracle-app/assessment/assessment-data.json",
    ".github/migrate-oracle-app/assessment/test-discovery-baseline.json",
    ".github/migrate-oracle-app/conversions/conversion-report.md",
    ".github/migrate-oracle-app/conversions/conversion-state.json",
    ".github/migrate-oracle-app/conversions/file-diffs.md",
    ".github/migrate-oracle-app/conversions/database-ddl/deploy.sql",
    "src/coverage/manual.xml",
    "nested/.github/migrate-oracle-app/conversions/coverage/jacoco.xml",
  ];
  const allPaths = [...coveragePaths, ...otherPaths];
  try {
    git(["init", "--quiet"]);
    for (const relativePath of allPaths) {
      const absolutePath = path.join(root, ...relativePath.split("/"));
      await mkdir(path.dirname(absolutePath), { recursive: true });
      await writeFile(absolutePath, "fixture\n");
    }
    git(["add", "--", coveragePaths[1]]);
    await writeFile(path.join(root, ".gitignore"), `${expectedRules.join("\n")}\n`);
    const input = `${allPaths.join("\n")}\n`;

    assert.deepEqual(
      git(["check-ignore", "--no-index", "--stdin"], input).trim().split(/\r?\n/),
      coveragePaths,
    );
    assert.deepEqual(
      git(["check-ignore", "--stdin"], input).trim().split(/\r?\n/),
      [coveragePaths[0]],
    );
    assert.equal(git(["ls-files", "--", coveragePaths[1]]).trim(), coveragePaths[1]);
    for (const relativePath of coveragePaths) {
      assert.equal(
        await readFile(path.join(root, ...relativePath.split("/")), "utf8"),
        "fixture\n",
      );
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("conversion reports require an exhaustive Git-reconciled file inventory", async () => {
  const conversion = await readSkillFile("references/conversion.md");
  const gitWorkspace = await readSkillFile("references/git-workspace.md");
  const skill = await readSkillFile("SKILL.md");

  assert.match(conversion, /exhaustive path-by-path working-tree inventory/i);
  assert.match(conversion, /Modified tracked files/);
  assert.match(conversion, /New source and test\s+files/);
  assert.match(conversion, /Generated migration artifacts/);
  assert.match(conversion, /git status --porcelain=v1 --untracked-files=all/);
  assert.match(conversion, /Never use summaries/i);
  assert.match(gitWorkspace, /List every modified, added,\s*deleted, renamed, and generated/i);
  assert.match(skill, /exhaustive path-by-path Changes inventory/i);
  assert.match(conversion, /git diff --numstat/);
  assert.match(conversion, /changed lines as additions plus deletions/i);
  assert.match(conversion, /source, tests, build, and runtime configuration/i);
  assert.match(conversion, /Updated line ranges/);
  assert.match(conversion, /git diff --unified=0/);
  assert.match(conversion, /deletion point/);
  assert.match(conversion, /file-diffs\.md/);
  assert.match(conversion, /before\/after Git hunks/i);
  assert.match(conversion, /simple sequential Markdown headings/i);
  assert.match(conversion, /FILE-00001/);
  assert.match(conversion, /minimum numeric width of five digits/i);
  assert.match(conversion, /exceeds 99,999/i);
  assert.match(conversion, /rather\s+than HTML anchors/i);
  assert.match(conversion, /## Table of Contents/);
  assert.match(conversion, /\[Back to Top\]\(#top\).*Back to Conversion Report/s);
  assert.match(conversion, /do not add that link under the Table of Contents/i);
  assert.match(conversion, /identical counts and mappings/i);
  assert.match(conversion, /redact literal hosts/i);
  assert.match(conversion, /connection\s+strings, credentials/i);
  assert.match(conversion, /email addresses, customer\s+values/i);
  assert.match(conversion, /withheld notice/i);
});

test("validation convergence is de-duplicated into Validation Results", async () => {
  const diagrams = await readSkillFile("references/diagrams.md");
  const conversion = await readSkillFile("references/conversion.md");
  const skill = await readSkillFile("SKILL.md");

  assert.match(
    conversion,
    /\| Stage \/ gate \| Scope \/ evidence \| Result \| Readiness impact \|/,
  );
  assert.doesNotMatch(diagrams, /^### Validation \/ Convergence Map$/m);
  assert.match(conversion, /exactly these three diagrams/i);
  assert.match(conversion, /Do not add a separate\s+`Validation \/ Convergence Map` section/i);
  assert.match(skill, /never create a duplicate Validation \/\s+Convergence Map section/i);
});

test("coverage by lane tables do not contain a status column", async () => {
  const diagrams = await readSkillFile("references/diagrams.md");
  const laneSections = diagrams.split("### Coverage by Lane").slice(1);

  assert.equal(laneSections.length, 2);
  assert.match(
    laneSections[0],
    /\| Lane \| Scope \| Tests \/ engines \| Line coverage \| Branch coverage \| Evidence \|/,
  );
  assert.match(
    laneSections[1],
    /\| Lane \| Baseline line \| Final line \| Baseline branch \| Final branch \| Evidence \|/,
  );
  for (const section of laneSections) {
    assert.doesNotMatch(section, /\| Lane \|[^\r\n]*\bStatus\b/i);
  }
});

test("assessment and conversion coverage omit legends and summary status columns", async () => {
  const diagrams = await readSkillFile("references/diagrams.md");
  const assessmentCoverage = diagrams
    .split("## Assessment Code Coverage Section")[1]
    .split("## Conversion Code Coverage Section")[0];
  const conversionCoverage = diagrams
    .split("## Conversion Code Coverage Section")[1]
    .split("## Conversion Diagrams")[0];

  assert.match(diagrams, /neither report\s+includes a\s+coverage legend/i);
  assert.doesNotMatch(assessmentCoverage, /\| Symbol \| Label \| Meaning \|/);
  assert.doesNotMatch(conversionCoverage, /\| Symbol \| Label \| Meaning \|/);
  assert.match(
    assessmentCoverage,
    /\| Baseline \| Build \/ tests \| Coverage result \|/,
  );
  assert.doesNotMatch(
    assessmentCoverage,
    /\| Baseline \| Build \/ tests \| Coverage result \| Status \|/,
  );
  assert.match(
    assessmentCoverage,
    /\| Module \/ path \| Behavior or database boundary \| Existing tests and lane \| Line coverage \| Branch coverage \| Gap \/ next action \|/,
  );
  assert.match(
    conversionCoverage,
    /\| Scope \| Metric \| Baseline \| Final \| Δ percentage points \|/,
  );
  assert.doesNotMatch(
    conversionCoverage,
    /\| Scope \| Metric \| Baseline \| Final \| Δ percentage points \| Status \|/,
  );
});

test("assessment hotspots are a table and not a Mermaid diagram", async () => {
  const diagrams = await readSkillFile("references/diagrams.md");
  const assessment = await readSkillFile("references/assessment.md");
  const skill = await readSkillFile("SKILL.md");
  const hotspotSection = diagrams
    .split("### Oracle Migration Hotspots")[1]
    .split("## Code Coverage Sections")[0];

  assert.doesNotMatch(diagrams, /^### Oracle Migration Hotspot Map$/m);
  assert.match(
    hotspotSection,
    /\| Hotspot \| Evidence \/ scope \| Status \| Conversion action \/ impact \|/,
  );
  assert.doesNotMatch(
    hotspotSection,
    /```mermaid|^\s*(flowchart|sequenceDiagram)\b/m,
  );
  assert.match(assessment, /assessment must include:/i);
  assert.match(
    assessment,
    /non-Mermaid \*\*Oracle Migration Hotspots\*\*[\s\S]*?table/i,
  );
  assert.match(skill, /assessment architecture\/dependency diagrams/i);
});

test("assessment uses the executive-first readiness hierarchy and coverage heatmap", async () => {
  const assessment = await readSkillFile("references/assessment.md");
  const diagrams = await readSkillFile("references/diagrams.md");
  const reportContract = assessment.split("## Report")[1];
  const expectedSections = [
    "Executive Summary",
    "Application & Database Architecture",
    "Migration Impact Assessment",
    "PostgreSQL Target Readiness",
    "Test & Validation Readiness",
    "Conversion Success Criteria",
    "Assessment Outcome",
    "Appendix / Run Evidence",
  ];

  let previousIndex = -1;
  for (const section of expectedSections) {
    const index = reportContract.indexOf(`**${section}**`);
    assert.ok(index > previousIndex, `${section} is missing or out of order`);
    previousIndex = index;
  }

  assert.match(assessment, /Readiness Scorecard/);
  assert.match(assessment, /Do not add a final Findings table/);
  assert.match(diagrams, /### Coverage Heatmap/);
  assert.match(diagrams, /summed raw counters/i);
  assert.match(diagrams, /Never average\s+package percentages/i);
});

test("conversion uses the customer-outcome hierarchy and migration validation coverage", async () => {
  const conversion = await readSkillFile("references/conversion.md");
  const diagrams = await readSkillFile("references/diagrams.md");
  const reportContract = conversion.split("`conversion-report.md` records:")[1];
  const expectedSections = [
    "Executive Conversion Summary",
    "Conversion Scorecard",
    "Next Steps / Actions Needed",
    "Conversion Transformation",
    "Validation Results",
    "Test & Coverage Results",
    "Database Test Environment",
    "Remaining Work & Exceptions",
    "Conversion Outcome",
    "Appendix / Execution Evidence",
  ];

  let previousIndex = -1;
  for (const section of expectedSections) {
    const index = reportContract.indexOf(`**${section}**`);
    assert.ok(index > previousIndex, `${section} is missing or out of order`);
    previousIndex = index;
  }

  assert.match(diagrams, /### Migration Validation Coverage/);
  assert.match(diagrams, /overall legacy coverage separate/i);
  assert.doesNotMatch(diagrams, /^### Validation \/ Convergence Map$/m);
  assert.match(conversion, /Appendix \/ Execution Evidence/);
  assert.match(conversion, /exactly six rows/i);
  assert.match(conversion, /Application conversion/);
  assert.match(conversion, /PostgreSQL integration tests/);
  assert.match(conversion, /Production readiness/);
  assert.match(conversion, /Keep file counts and change inventory out/i);
  assert.match(conversion, /`Area \| Result \| Status`/);
  assert.match(conversion, /one to five practical,\s+customer-owned handoff actions/i);
  assert.match(conversion, /`Priority \| Area \| Action needed \| Owner`/);
  assert.match(conversion, /Do not copy every warning/i);
  assert.match(conversion, /exactly one emoji plus a plain title-case text label/i);
});

test("assessment and conversion reports use the standard application-code titles", async () => {
  const assessment = await readSkillFile("references/assessment.md");
  const conversion = await readSkillFile("references/conversion.md");

  assert.match(
    assessment,
    /# Oracle to PostgreSQL Application Code Assessment/,
  );
  assert.match(
    conversion,
    /# Oracle to PostgreSQL Application Code Conversion/,
  );
});

test("report templates contain every mandatory section and evidence table", async () => {
  const assessmentTemplate = await readSkillFile(
    "references/assessment-report-template.md",
  );
  const conversionTemplate = await readSkillFile(
    "references/conversion-report-template.md",
  );

  for (const heading of [
    "## Executive Summary",
    "## Application & Database Architecture",
    "## Migration Impact Assessment",
    "## PostgreSQL Target Readiness",
    "## Test & Validation Readiness",
    "## Conversion Success Criteria",
    "## Assessment Outcome",
    "## Appendix / Run Evidence",
  ]) {
    assert.match(assessmentTemplate, new RegExp(escapeRegExp(heading)));
  }
  for (const heading of [
    "## Executive Conversion Summary",
    "## Conversion Transformation",
    "## Validation Results",
    "## Test & Coverage Results",
    "## Database Test Environment",
    "## Remaining Work & Exceptions",
    "## Conversion Outcome",
    "## Appendix / Execution Evidence",
  ]) {
    assert.match(conversionTemplate, new RegExp(escapeRegExp(heading)));
  }
  for (const required of [
    "### Conversion Scorecard",
    "### Next Steps / Actions Needed",
    "### Oracle DDL Source Reconciliation",
    "### Docker Engine Availability",
    "### Synthetic Test Data",
    "### Database Objects Exercised with Synthetic Data",
    "### Behavior Exercised",
    "### Test Discovery Reconciliation",
    "### Target Write Test Reconciliation",
    "### Runtime Driver Packaging Reconciliation",
    "### Conversion Scope Reconciliation",
    "### Unused Artifacts",
    "### Broken-Test Repair Reconciliation",
    "### Parallel Execution & Agent Utilization",
    "### Performance & Execution Efficiency",
    "### Coverage Convergence",
    "### Changes Inventory",
    "| Object kind | Active / code-derived in tested scope | Exercised | Evidence / gap |",
    "| Test runner/adapter availability |",
    "| Test discovery reconciliation |",
    "| Broken-test repair |",
    "| Area | Result | Status |",
    "| Priority | Area | Action needed | Owner |",
    "| Missing PostgreSQL object | Oracle source / reconstruction provenance | Source DDL file / digest | Body completeness / confidence | PostgreSQL DDL and application call updates | Synthetic Docker validation | Persistent target result / customer validation |",
    "**Recommended production-readiness path:**",
  ]) {
    assert.match(conversionTemplate, new RegExp(escapeRegExp(required)));
  }
  assert.match(assessmentTemplate, /### Test Discovery Reconciliation/);
  assert.match(assessmentTemplate, /### Extracted Oracle DDL Source/);
  assert.match(
    assessmentTemplate,
    /\| Missing PostgreSQL object \| Oracle source object \| Source DDL file \/ digest \| Reconstruction provenance \| Body completeness \/ confidence \| Customer validation \|/,
  );
  assert.match(
    assessmentTemplate,
    /Language \| Framework \/ adapter \| Source files \| Test cases \| Adapter available/,
  );
});

test("Docker Desktop startup is explicit and default off", async () => {
  const skill = await readSkillFile("SKILL.md");
  const options = await readSkillFile("references/options.md");
  const testing = await readSkillFile("references/testing-and-coverage.md");
  const safety = await readSkillFile("references/database-safety.md");
  const state = await readSkillFile(
    "references/conversion-state-schema.md",
  );
  const template = await readSkillFile(
    "references/conversion-report-template.md",
  );

  for (const contract of [skill, options, testing, safety]) {
    assert.match(contract, /start-docker-desktop/);
  }
  assert.match(options, /defaults off/i);
  assert.match(options, /never launch Docker Desktop/i);
  assert.match(options, /does not permit\s+installation, update/i);
  assert.match(state, /"start-docker-desktop": false/);
  assert.match(state, /dockerEngineAvailability/);
  assert.match(template, /### Docker Engine Availability/);
});

test("unused artifacts are retained unless explicit removal is enabled", async () => {
  const skill = await readSkillFile("SKILL.md");
  const options = await readSkillFile("references/options.md");
  const testing = await readSkillFile("references/testing-and-coverage.md");
  const conversion = await readSkillFile("references/conversion.md");
  const state = await readSkillFile(
    "references/conversion-state-schema.md",
  );
  const template = await readSkillFile(
    "references/conversion-report-template.md",
  );

  for (const contract of [skill, options, testing, conversion]) {
    assert.match(contract, /remove-unused-artifacts/);
  }
  assert.match(options, /defaults off/i);
  assert.match(options, /Unused — retained/);
  assert.match(options, /post-removal\s+build, complete tests, package, and archive inspection/i);
  assert.match(state, /"remove-unused-artifacts": false/);
  assert.match(state, /unusedArtifactDisposition/);
  assert.match(template, /### Unused Artifacts/);
});

test("no database-facing code stops before builds tests Docker and target access", async () => {
  const skill = await readSkillFile("SKILL.md");
  const assessment = await readSkillFile("references/assessment.md");
  const conversion = await readSkillFile("references/conversion.md");
  const options = await readSkillFile("references/options.md");
  const assessmentTemplate = await readSkillFile(
    "references/no-conversion-assessment-template.md",
  );
  const conversionTemplate = await readSkillFile(
    "references/no-conversion-report-template.md",
  );
  const packageManifest = JSON.parse(await readRepoFile("package.json"));
  const detectorSource = await readSkillFile(
    "../../skills/migrate-oracle-app/scripts/detect-migration-scope.mjs",
  );

  assert.match(skill, /detect-migration-scope\.mjs/);
  assert.match(detectorSource, /export async function detectMigrationScope/);
  assert.match(skill, /`<plugin-root>` is exactly two parent directories/i);
  assert.ok(
    skill.includes(
      '<skill-directory>\\scripts\\detect-migration-scope.mjs',
    ),
  );
  assert.match(skill, /Migration scripts are stored under/i);
  assert.match(skill, /linear\s+comment-aware string scanner/i);
  assert.match(skill, /50,000-file, 100 MB text, 30-second scan budget/i);
  assert.doesNotMatch(
    skill,
    /node <plugin-root>\/scripts\/detect-migration-scope\.mjs/,
  );
  assert.match(skill, /NO_CONVERSION_REQUIRED/);
  assert.match(
    skill,
    /Do not install dependencies, resolve or\s+query the target, build, test, generate tests, collect coverage, start Docker, create DDL, or\s+change application files/i,
  );
  assert.match(assessment, /stop before dependency installation, build, test discovery/i);
  assert.match(conversion, /NO_CONVERSION_REQUIRED/);
  assert.match(options, /takes precedence over every option/i);
  for (const template of [assessmentTemplate, conversionTemplate]) {
    assert.match(template, /No application conversion required/);
    assert.match(template, /Not run by design/);
    assert.match(template, /Not applicable/);
  }
  assert.match(assessmentTemplate, /Angular \/ React \/ Vue/);
  assert.match(conversionTemplate, /Passive \/ Unused Artifact Candidates/);
  assert.match(
    packageManifest.scripts["detect:scope"],
    /detect-migration-scope\.mjs/,
  );
});

test("independent migration work requires child-agent parallel orchestration", async () => {
  const skill = await readSkillFile("SKILL.md");
  const orchestration = await readSkillFile(
    "references/agent-orchestration.md",
  );
  const state = await readSkillFile(
    "references/conversion-state-schema.md",
  );
  const template = await readSkillFile(
    "references/conversion-report-template.md",
  );

  assert.match(skill, /launch at least two in a parallel batch/i);
  assert.match(orchestration, /Mandatory Planning Gate/);
  assert.match(orchestration, /exclusive path[\s\S]*ownership|disjoint owned paths/i);
  assert.match(orchestration, /Only the primary agent may/i);
  assert.match(orchestration, /database modifications always remain primary-agent work/i);
  assert.match(orchestration, /background execution/i);
  assert.match(state, /"orchestration"/);
  assert.match(state, /"eligibleIndependentUnits"/);
  assert.match(state, /"parallelBatches"/);
  assert.match(template, /Parallel Execution & Agent Utilization/);
});

test("Maven waves compile once and package cleanly once", async () => {
  const performance = await readSkillFile(
    "references/performance-and-execution.md",
  );
  const state = await readSkillFile(
    "references/conversion-state-schema.md",
  );
  const template = await readSkillFile(
    "references/maven-wave-config-template.json",
  );
  const packageManifest = JSON.parse(await readRepoFile("package.json"));

  assert.match(performance, /test-compile` once/i);
  assert.match(performance, /direct\s+`surefire:test` or `failsafe:integration-test`/i);
  assert.match(performance, /Use the warmed local repository and `-o`/i);
  assert.match(performance, /JaCoCo CLI/i);
  assert.match(performance, /Generate HTML at most once/i);
  assert.match(performance, /one final online `clean package`/i);
  assert.match(performance, /pre-package source\/classification checks/i);
  assert.match(performance, /Do not perform fixed synchronous waits/i);
  assert.match(performance, /parallel when the client supports parallel tool calls/i);
  assert.match(performance, /preparationProof/);
  assert.match(performance, /finalizeOnly/);
  assert.match(performance, /MIGRATION_TEST_PG_JDBC_URL/);
  assert.match(performance, /Never enable global Testcontainers reuse/);
  assert.match(performance, /Several test methods\/scenarios are not several waves/);
  assert.match(state, /"strategy": "precompiled-waves"/);
  assert.match(state, /"cleanPackageCount": 1/);
  assert.match(template, /"unitWaves"/);
  assert.match(template, /"integrationWaves"/);
  assert.match(
    packageManifest.scripts.test,
    /tests\/maven-wave-runner\.test\.mjs/,
  );
});

test("runtime descriptors reconcile exact deployment driver paths", async () => {
  const skill = await readSkillFile("SKILL.md");
  const conversion = await readSkillFile("references/conversion.md");
  const performance = await readSkillFile(
    "references/performance-and-execution.md",
  );
  const state = await readSkillFile(
    "references/conversion-state-schema.md",
  );
  const template = await readSkillFile(
    "references/conversion-report-template.md",
  );
  const planTemplate = await readSkillFile(
    "references/driver-packaging-plan-template.json",
  );
  const packageManifest = JSON.parse(await readRepoFile("package.json"));

  for (const contract of [skill, conversion, performance]) {
    assert.match(contract, /inspect-driver-packaging\.mjs/);
    assert.match(contract, /WAR.*alone|only inside.*WAR/i);
  }
  assert.match(state, /runtimeDriverPackaging/);
  assert.match(state, /warOnlyDriverDetected/);
  assert.match(template, /Runtime Driver Packaging Reconciliation/);
  assert.match(planTemplate, /"archives"/);
  assert.match(planTemplate, /"descriptors"/);
  assert.match(planTemplate, /"runtimeDriverPatterns"/);
  assert.match(
    packageManifest.scripts["inspect:driver-packaging"],
    /inspect-driver-packaging\.mjs/,
  );
});

test("fix-broken-tests covers pre-existing failures and scope reconciles all findings", async () => {
  const skill = await readSkillFile("SKILL.md");
  const testing = await readSkillFile("references/testing-and-coverage.md");
  const conversion = await readSkillFile("references/conversion.md");
  const state = await readSkillFile(
    "references/conversion-state-schema.md",
  );

  assert.match(skill, /including pre-existing failures/i);
  assert.match(testing, /Pre-existing[\s\S]*not permission to skip repair/i);
  assert.match(conversion, /exhaustive assessment Oracle-finding manifest/i);
  assert.match(conversion, /zero safely convertible findings/i);
  assert.match(state, /repairAttempts/);
  assert.match(state, /unresolvedFailureClasses/);
  assert.match(state, /conversionScope/);
  assert.match(state, /safeConvertibleRemaining/);
});

test("test discovery requires adapters, container reconciliation, repair, and fresh coverage", async () => {
  const skill = await readSkillFile("SKILL.md");
  const testing = await readSkillFile("references/testing-and-coverage.md");
  const stateSchema = await readSkillFile(
    "references/conversion-state-schema.md",
  );
  const discoverySchema = await readSkillFile(
    "references/test-discovery-baseline-schema.md",
  );

  for (const contract of [skill, testing]) {
    assert.match(contract, /runner\/adapter availability/i);
    assert.match(contract, /discovery reconciliation/i);
    assert.match(contract, /failure repair/i);
  }
  assert.match(testing, /junit-vintage-engine/);
  assert.match(testing, /junit-jupiter-engine/);
  assert.match(testing, /green build exit alone is\s+insufficient/i);
  assert.match(testing, /failIfNoTests/);
  assert.match(testing, /failIfNoSpecifiedTests/);
  assert.match(testing, /Prohibit `skipTests`/);
  assert.match(testing, /framework\/toolchain,[\s\S]*integration,[\s\S]*combined lanes/i);
  assert.match(discoverySchema, /eligibleContainers/);
  assert.match(discoverySchema, /representativeContainers/);
  assert.match(discoverySchema, /minimumExecutedTests/);
  assert.match(stateSchema, /coverageGeneratedAfterReconciliation/);
  assert.match(stateSchema, /coverageArtifactsFresh/);
});

test("language adapters cover .NET, Python, and C/C++ conversion and native tests", async () => {
  const adapters = await readSkillFile("references/language-adapters.md");

  for (const required of [
    "## .NET",
    "Npgsql",
    "Preserve native MTP projects",
    "dotnet test --project <project> -- --list-tests",
    "For existing VSTest projects",
    "dotnet test <project> --list-tests",
    "TRX",
    "## Python",
    "Psycopg 3",
    "pytest --collect-only",
    "coverage.py",
    "## C and C++",
    "libpqxx",
    "ctest --show-only=json-v1",
    "llvm-cov",
  ]) {
    assert.match(adapters, new RegExp(escapeRegExp(required)));
  }
});

test("conversion artifacts use one stable overwrite-on-rerun directory", async () => {
  const skill = await readSkillFile("SKILL.md");
  const conversion = await readSkillFile("references/conversion.md");
  const state = await readSkillFile("references/state-and-convergence.md");

  for (const contract of [skill, conversion, state]) {
    assert.match(contract, /\.github\/migrate-oracle-app\/conversions\//);
    assert.doesNotMatch(contract, /conversions\/<run-name>/);
  }
  assert.match(skill, /Never create timestamped or named conversion\s+subdirectories/i);
  assert.match(conversion, /atomically replace only the\s+skill-owned/i);
  assert.match(conversion, /Remove obsolete prior skill-owned files/i);
  assert.match(state, /rather than creating a new run directory/i);
});

test("assessment and conversion reports require table-of-contents navigation", async () => {
  const assessment = await readSkillFile("references/assessment.md");
  const conversion = await readSkillFile("references/conversion.md");

  for (const contract of [assessment, conversion]) {
    assert.match(contract, /<a id="top"><\/a>/);
    assert.match(contract, /## Table of Contents/);
    assert.match(contract, /explicit stable HTML anchors/i);
    assert.match(contract, /\[Back to Top\]\(#top\)/);
    assert.match(contract, /every top-level section except Table of Contents/i);
  }
});

test("plugin and package versions agree", async () => {
  const plugin = JSON.parse(await readRepoFile("plugin.json"));
  const packageManifest = JSON.parse(await readRepoFile("package.json"));

  assert.equal(plugin.version, "0.3.0");
  assert.equal(packageManifest.version, plugin.version);
});

test("coverage scopes use customer-readable fixed-set and migration-critical labels", async () => {
  const diagrams = await readSkillFile("references/diagrams.md");
  const coverage = await readSkillFile("references/testing-and-coverage.md");

  assert.match(diagrams, /Original production classes — fixed comparison set/);
  assert.match(diagrams, /Eight migration-critical classes/);
  assert.match(diagrams, /not every modified file\/class/i);
  assert.match(coverage, /list every included class/i);
  assert.match(coverage, /Never imply that this subset is\s+all modified files or classes/i);
});

async function readSkillFile(relativePath) {
  return readRepoFile(
    `skills/migrate-oracle-app/${relativePath}`,
  );
}

async function readRepoFile(relativePath) {
  return readFile(new URL(`../${relativePath}`, import.meta.url), "utf8");
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
