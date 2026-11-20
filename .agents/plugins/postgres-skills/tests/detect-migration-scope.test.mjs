import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { unlinkSync } from "node:fs";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  detectMigrationScope,
  extractEmbeddedSql,
  validateNoConversionExecution,
} from "../skills/migrate-oracle-app/scripts/detect-migration-scope.mjs";
import { maskSourceComments, sourceLiteralAt } from "../skills/migrate-oracle-app/scripts/source-comment-mask.mjs";

const detector = path.resolve("skills/migrate-oracle-app/scripts", "detect-migration-scope.mjs");

test("comment masking preserves line/column positions, Unicode, and literal URL markers", () => {
  const content = '// Oracle.ManagedDataAccess \r\nclass App {\r  string url = @"jdbc:oracle:thin:@//host"; /* old OracleConnection */\n  string raw = """http://host/*literal*/""";\n  string empty = """"""; // Oracle.DataAccess\n}\n';
  const masked = maskSourceComments(content, "App.cs");
  assert.equal(masked.length, content.length);
  assert.deepEqual([...masked.matchAll(/\r\n|\r|\n/g)].map((match) => match.index),
    [...content.matchAll(/\r\n|\r|\n/g)].map((match) => match.index));
  assert.equal(masked.indexOf("jdbc:oracle:"), content.indexOf("jdbc:oracle:"));
  assert.ok(masked.includes('"""http://host/*literal*/"""'));
  assert.doesNotMatch(masked, /Oracle\.ManagedDataAccess|OracleConnection|Oracle\.DataAccess/);
  const unicode = "/* \u{1f642} OracleConnection */ class App {}";
  assert.equal(maskSourceComments(unicode, "App.java").length, unicode.length);
});

test("property continuation values and inline comment characters are not stripped", () => {
  const content = "# old jdbc:oracle:\r\n! oracle.jdbc.OracleDriver\rurl=jdbc:oracle:thin:@host#part!value\ncontinued=\\\n #jdbc:oracle:thin:@continued\n";
  const masked = maskSourceComments(content, "application.properties");
  assert.doesNotMatch(masked, /old jdbc|oracle\.jdbc\.OracleDriver/);
  assert.match(masked, /url=jdbc:oracle:thin:@host#part!value/);
  assert.match(masked, /#jdbc:oracle:thin:@continued/);
  assert.equal(masked.length, content.length);
});

test("XML comments are masked but CDATA and active attribute values are retained", () => {
  const content = '<Project><!-- Oracle.ManagedDataAccess --><PropertyGroup><Url value="jdbc:oracle:thin:@//host"><![CDATA[<!-- literal jdbc:oracle: -->]]></Url></PropertyGroup></Project>';
  const masked = maskSourceComments(content, "Directory.Build.props");
  assert.doesNotMatch(masked, /Oracle\.ManagedDataAccess/);
  assert.match(masked, /value="jdbc:oracle:thin:@\/\/host"/);
  assert.match(masked, /CDATA\[<!-- literal jdbc:oracle: -->/);
  assert.equal(masked.length, content.length);
});

test("incomplete supported syntax is reported for review and unknown syntax is not stripped", async () => {
  assert.throws(() => maskSourceComments("/* missing", "App.java"), /requires review/);
  assert.throws(() => maskSourceComments("<Project><!-- missing", "App.csproj"), /requires review/);
  assert.equal(maskSourceComments("# literal OracleConnection", "unknown.format"), "# literal OracleConnection");
  const root = await fixture({ "App.cs": "class App { /* incomplete" });
  try {
    const result = await detectMigrationScope(root, { captureBaseline: false });
    assert.equal(result.status, "SCOPE_REVIEW_REQUIRED");
    assert.match(result.skippedFiles[0].reason, /SOURCE_COMMENT_UNTERMINATED/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("shared literal scanning keeps the bounded SQL extraction contract", () => {
  const source = '"SELECT id FROM customers"';
  const literal = sourceLiteralAt(source, 0, { maxContentLength: 4 });
  assert.equal(literal.terminated, true);
  assert.equal(literal.oversized, true);
  assert.equal(sourceLiteralAt("x", 0), null);
  const raw = '"""C:\\files\\"""';
  assert.equal(sourceLiteralAt(raw, 0, { rawStrings: true }).terminated, true);
  assert.equal(maskSourceComments(raw, "App.cs"), raw);
});

test("recognized build files still report read failures rather than silently disappearing", async () => {
  const root = await fixture({
    "App.cs": "using Oracle.ManagedDataAccess.Client; class App {}",
    "Directory.Build.props": "<Project/>",
  });
  try {
    const result = await detectMigrationScope(root, {
      captureBaseline: false,
      onProgress(event) {
        if (event.phase === "scan-start") unlinkSync(path.join(root, "Directory.Build.props"));
      },
    });
    assert.ok(result.skippedFiles.some((entry) => entry.path === "Directory.Build.props" && /ENOENT/.test(entry.reason)));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("active dependency evidence excludes Java/C# and configuration comments", async () => {
  const root = await fixture({
    "App.cs": "// using Oracle.ManagedDataAccess.Client;\r\nusing System.Data.Common;\r\nclass App {}",
    "src/App.java": "/* import oracle.jdbc.OracleConnection; */\nclass App {}",
    "application.properties": "# jdbc:oracle:thin:@legacy\n! oracle.jdbc.OracleDriver\nurl=jdbc:postgresql://localhost/app",
    "App.csproj": '<Project><!-- <PackageReference Include="Oracle.ManagedDataAccess.Core"/> --></Project>',
  });
  try {
    const scope = await detectMigrationScope(root, { captureBaseline: false });
    assert.deepEqual(scope.activeOracleFindings, []);
    assert.deepEqual(scope.skippedFiles, []);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("comment masking retains literal Oracle values, evidence lines, and original file digests", async () => {
  const content = Buffer.from('\ufeff// historical Oracle.ManagedDataAccess.Client;\r\nclass App {\r\n  string url = @"jdbc:oracle:thin:@//host/service";\r\n}\r\n');
  const root = await fixture({ "App.cs": content });
  try {
    const scope = await detectMigrationScope(root, { captureBaseline: false });
    assert.ok(scope.activeOracleFindings.some((finding) => finding.category === "Oracle connection"));
    assert.ok(scope.activeOracleFindings.every((finding) => !finding.evidence.includes("historical")));
    assert.equal(scope.scannedFileDigests[0].sha256, createHash("sha256").update(content).digest("hex"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("multiline Java and escaped/raw C# queries retain their only Oracle evidence", async () => {
  for (const [file, content] of [
    ["src/App.java", 'class App { String sql = """\nSELECT id,\nNVL(name, \'unknown\')\nFROM customers\n"""; }'],
    ["App.cs", 'class App { string sql = @"SELECT NVL(""NAME"", \'unknown\') FROM CUSTOMERS"; }'],
    ["App.cs", 'class App { string sql = """\nSELECT NVL("NAME", \'unknown\')\nFROM CUSTOMERS\n"""; }'],
    ["App.cs", 'class App { string directory = """C:\\files\\"""; string sql = "SELECT NVL(name, \'unknown\') FROM customers"; }'],
  ]) {
    const root = await fixture({ [file]: content });
    try {
      const result = await detectMigrationScope(root, { captureBaseline: false });
      assert.equal(result.status, "MIGRATION_REQUIRED", file);
      assert.ok(result.activeOracleFindings.some((finding) => finding.category === "Oracle SQL"), file);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  }
});

test("verbatim SQL identifiers are decoded while comments and dropdown prose remain excluded", () => {
  assert.deepEqual(
    extractEmbeddedSql('string sql = @"SELECT NVL(""NAME"", \'unknown\') FROM CUSTOMERS";'),
    ['SELECT NVL("NAME", \'unknown\') FROM CUSTOMERS'],
  );
  assert.deepEqual(extractEmbeddedSql('// @"SELECT NVL(""NAME"", \'\') FROM CUSTOMERS"\n' +
    'string prose = "Select an item from the dropdown list";'), []);
});

test("pure Angular application with unused OJDBC JAR exits without conversion", async () => {
  const root = await fixture({
    "package.json": JSON.stringify({
      dependencies: { "@angular/core": "18.0.0" },
      scripts: { build: "ng build", test: "ng test" },
    }),
    "src/app/app.component.ts":
      "import { Component } from '@angular/core';\n@Component({selector: 'app-root'}) export class AppComponent {}",
    "lib/ojdbc8.jar": "placeholder",
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "NO_CONVERSION_REQUIRED");
    assert.equal(result.activeDatabaseFindings.length, 0);
    assert.equal(result.activeOracleFindings.length, 0);
    assert.ok(result.detectedTechnologies.some((item) => item.name === "Angular"));
    assert.deepEqual(result.passiveArtifacts[0], {
      path: "lib/ojdbc8.jar",
      kind: "jar",
      classification: "reference-not-proven",
      action: "retained",
      evidence: [
        "No artifact name reference found in scanned build/package descriptors.",
      ],
    });
    assert.deepEqual(validateNoConversionExecution(result), []);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("React application with active PostgreSQL package requires migration review", async () => {
  const root = await fixture({
    "package.json": JSON.stringify({
      dependencies: { react: "19.0.0", pg: "8.13.0" },
    }),
    "src/index.tsx": "import React from 'react'; export const App = () => <div/>;",
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "MIGRATION_REQUIRED");
    assert.ok(result.activeDatabaseFindings.length > 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Java JDBC application requires migration", async () => {
  const root = await fixture({
    "src/main/java/App.java":
      "import java.sql.Connection; public class App { Connection connection; }",
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "MIGRATION_REQUIRED");
    assert.ok(
      result.activeDatabaseFindings.some(
        (finding) => finding.category === "JDBC/SQL API",
      ),
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("additional known text formats are inspected only when explicitly enabled", async () => {
  const files = {
    "src/App.java": "import java.sql.Connection; class App {}",
    ".project": "<projectDescription><name>example</name></projectDescription>",
    ".rest": "GET https://example.invalid/tasks",
    "requests.http": "GET https://example.invalid/tasks",
    ".settings/org.eclipse.jdt.core.prefs": "org.eclipse.jdt.core.compiler.compliance=17",
  };
  const root = await fixture(files);
  try {
    const defaults = await detectMigrationScope(root, { captureBaseline: false });
    assert.equal(defaults.skippedFiles.length, 4);
    const enabled = await detectMigrationScope(root, {
      captureBaseline: false,
      additionalTextExtensions: [".prefs", ".rest", ".http"],
      additionalTextFileNames: [".project", ".rest", ".http"],
    });
    assert.equal(enabled.status, "MIGRATION_REQUIRED");
    assert.deepEqual(enabled.skippedFiles, []);
    assert.deepEqual(enabled.scannedFileDigests.map((entry) => entry.path).sort(), Object.keys(files).sort());
    await assert.rejects(detectMigrationScope(root, { additionalTextExtensions: ["prefs"] }), /dotted extensions/);
    await assert.rejects(detectMigrationScope(root, { additionalTextFileNames: ["../unsafe"] }), /plain filenames/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("additional text formats retain size limits and unknown-file review", async () => {
  const root = await fixture({
    ".rest": "x".repeat(2_000_001),
    ".settings/unknown.metadataformat": "unclassified",
    "src/App.java": "import java.sql.Connection; class App {}",
  });
  try {
    const result = await detectMigrationScope(root, {
      captureBaseline: false,
      additionalTextFileNames: [".rest"],
    });
    assert.ok(result.skippedFiles.some((entry) => entry.path === ".rest" && /2000000-byte/.test(entry.reason)));
    assert.ok(result.skippedFiles.some((entry) => entry.path.endsWith("unknown.metadataformat") &&
      /Unknown non-inert/.test(entry.reason)));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Oracle SQL file requires migration", async () => {
  const root = await fixture({
    "sql/task.sql": "SELECT SYSDATE FROM DUAL;",
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "MIGRATION_REQUIRED");
    assert.ok(result.activeOracleFindings.length > 0);
    assert.ok(result.activeDatabaseFindings.length > 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("no-conversion validation rejects attempted build or artifact removal", async () => {
  const root = await fixture({
    "package.json": JSON.stringify({ dependencies: { react: "19.0.0" } }),
    "lib/ojdbc8.jar": "placeholder",
  });
  try {
    const result = await detectMigrationScope(root);
    result.executionSuppression.testsAttempted = true;
    result.passiveArtifacts[0].action = "removed";

    const errors = validateNoConversionExecution(result);

    assert.match(errors.join("\n"), /prohibited build\/test\/database work/);
    assert.match(errors.join("\n"), /passive artifact was not retained/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Angular dataSource migration comments and jwt decode do not trigger database scope", async () => {
  const root = await fixture({
    "package.json": JSON.stringify({
      dependencies: {
        "@angular/core": "18.0.0",
        "@angular/material": "18.0.0",
        "jwt-decode": "4.0.0",
      },
    }),
    "src/app/users.ts": `
      import { DataSource } from '@angular/cdk/collections';
      import { jwtDecode } from 'jwt-decode';
      // migration to standalone components is tracked in UI-421.
      export class UserDataSource extends DataSource<unknown> {}
      export const decode = (token) => jwtDecode(token);
      export const select = (store) => store.select('users');
      export const text = (decoder, chunk) => decoder.decode(chunk);
    `,
    "src/styles.css": ".table { display: block; }",
    "src/app/users.scss": "$color: red;",
    "LICENSE": "Example license",
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "NO_CONVERSION_REQUIRED");
    assert.equal(result.activeDatabaseFindings.length, 0);
    assert.equal(result.activeOracleFindings.length, 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("environment and requirements database dependencies are scanned", async () => {
  const root = await fixture({
    ".env.local": "DATABASE_URL=postgresql://example.invalid/app",
    "requirements.txt": "cx_Oracle==8.3.0",
    "app.py": "print('frontend helper')",
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "MIGRATION_REQUIRED");
    assert.ok(
      result.activeDatabaseFindings.some(
        (finding) => finding.path === ".env.local",
      ),
    );
    assert.ok(
      result.activeOracleFindings.some(
        (finding) => finding.path === "requirements.txt",
      ),
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("PLSQL package files are scanned as Oracle database sources", async () => {
  const root = await fixture({
    "database/packages/task_api.pkb":
      "BEGIN SELECT NVL(task_id, 0) INTO v_id FROM task_tm WHERE ROWNUM = 1; END;",
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "MIGRATION_REQUIRED");
    assert.ok(result.activeDatabaseFindings.length > 0);
    assert.ok(result.activeOracleFindings.length > 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("UPDATE and CALL only SQL files require migration", async () => {
  const root = await fixture({
    "sql/update.sql":
      "UPDATE employees SET status = 'A';\nCALL task_pkg.refresh(42);",
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "MIGRATION_REQUIRED");
    assert.ok(result.activeDatabaseFindings.length > 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("embedded portable SQL and Java database frameworks require migration", async () => {
  const root = await fixture({
    "pom.xml":
      "<project><dependencies><dependency><groupId>org.jooq</groupId><artifactId>jooq</artifactId></dependency></dependencies></project>",
    "src/main/java/OrderRepo.java":
      'class OrderRepo { Object run() { return dsl.fetch("SELECT order_id FROM orders WHERE status = ?"); } }',
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "MIGRATION_REQUIRED");
    assert.ok(
      result.activeDatabaseFindings.some(
        (finding) =>
          finding.category === "Java database framework" ||
          finding.category === "SQL source",
      ),
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("database references in non-excluded hidden configuration are scanned", async () => {
  const root = await fixture({
    "package.json": JSON.stringify({ dependencies: { react: "19.0.0" } }),
    ".ebextensions/database.config":
      "option_settings:\n  DATABASE_URL: jdbc:oracle:thin:@example",
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "MIGRATION_REQUIRED");
    assert.ok(result.activeOracleFindings.length > 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Oracle CLI and TNS descriptor references require migration", async () => {
  const root = await fixture({
    "package.json": JSON.stringify({ dependencies: { react: "19.0.0" } }),
    "tnsnames.ora":
      "APP = (DESCRIPTION = (ADDRESS = (PROTOCOL=TCP)(HOST=db))(CONNECT_DATA=(SERVICE_NAME=ORCL)))",
    "bin/run-report.sh": "sqlplus -s app/password@ORCL @refresh.sql",
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "MIGRATION_REQUIRED");
    assert.ok(
      result.activeOracleFindings.some(
        (finding) => finding.category === "Oracle client/tooling",
      ),
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("frontend prose and WebSocket connection names do not trigger database scope", async () => {
  const root = await fixture({
    "package.json": JSON.stringify({ dependencies: { react: "19.0.0" } }),
    "src/help.ts": `
      export const help = 'Select a value from the dropdown list';
      export const reload = (socket) => socket.getConnection();
      export interface ServerInfo { db_name: string; }
      // Call reloadRows() after the user edits a cell.
    `,
    "src/app.tsx": "import React from 'react'; export const App=()=>null;",
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "NO_CONVERSION_REQUIRED");
    assert.equal(result.activeDatabaseFindings.length, 0);
    assert.equal(result.activeOracleFindings.length, 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("unknown non-inert legacy files require scope review", async () => {
  const root = await fixture({
    "forms/login.fmb": "binary-placeholder",
    "package.json": JSON.stringify({ dependencies: { react: "19.0.0" } }),
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "SCOPE_REVIEW_REQUIRED");
    assert.ok(
      result.skippedFiles.some((file) => file.path === "forms/login.fmb"),
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("oversized database source requires review instead of a no-conversion verdict", async () => {
  const root = await fixture({
    "database/schema.sql": `SELECT 1;\n${" ".repeat(2_000_001)}`,
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "SCOPE_REVIEW_REQUIRED");
    assert.equal(result.skippedFiles.length, 1);
    assert.match(result.skippedFiles[0].reason, /exceeds 2000000-byte/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("technology attribution is based on file type rather than generic import syntax", async () => {
  const root = await fixture({
    "src/main/java/App.java":
      "package example; import java.util.List; public class App {}",
    "pom.xml": "<project><artifactId>app</artifactId></project>",
  });
  try {
    const result = await detectMigrationScope(root);
    const names = result.detectedTechnologies.map((technology) => technology.name);

    assert.ok(names.includes("Java"));
    assert.ok(!names.includes("Python"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("unknown application technology requires review instead of no conversion", async () => {
  const root = await fixture({
    "README.md": "Undetected custom application",
  });
  try {
    const result = await detectMigrationScope(root);

    assert.equal(result.status, "SCOPE_REVIEW_REQUIRED");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test(
  "unmatched apostrophes in comments cannot stall embedded SQL scanning",
  { timeout: 2_000 },
  async () => {
    const root = await fixture({
      "src/main/java/example/RestServiceHelper.java": `
        package example;
        /**
         * Rejects an input when it doesn't match the expected format.
         */
        public class RestServiceHelper {
          private static final String VALUE = "${"x".repeat(10_000)}";
        }
      `,
    });
    try {
      const result = await detectMigrationScope(root);

      assert.equal(result.status, "NO_CONVERSION_REQUIRED");
      assert.equal(result.scannedFiles, 1);
      assert.equal(result.activeDatabaseFindings.length, 0);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  },
);

test("detector emits progress on stderr without polluting JSON stdout", async () => {
  const root = await fixture({
    "src/main/java/example/App.java":
      "package example; public class App {}",
  });
  try {
    const result = spawnSync(process.execPath, [detector, root], {
      encoding: "utf8",
      timeout: 5_000,
      maxBuffer: 5 * 1024 * 1024,
      windowsHide: true,
    });

    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stderr, /\[scope\] walk-start/);
    assert.match(result.stderr, /\[scope\] scan-progress/);
    assert.match(result.stderr, /\[scope\] complete/);
    assert.equal(JSON.parse(result.stdout).status, "NO_CONVERSION_REQUIRED");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("detector stops at the configured file budget and requires review", async () => {
  const root = await fixture({
    "src/main/java/example/App.java":
      "package example; public class App {}",
    "src/main/java/example/Other.java":
      "package example; public class Other {}",
  });
  try {
    const result = await detectMigrationScope(root, { maxFiles: 1 });

    assert.equal(result.status, "SCOPE_REVIEW_REQUIRED");
    assert.equal(result.scannedFiles, 1);
    assert.ok(
      result.skippedFiles.some((file) => /1-file candidate limit/.test(file.reason)),
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

async function fixture(files) {
  const root = await mkdtemp(path.join(os.tmpdir(), "migration-scope-"));
  for (const [relativePath, content] of Object.entries(files)) {
    const absolutePath = path.join(root, relativePath);
    await mkdir(path.dirname(absolutePath), { recursive: true });
    await writeFile(absolutePath, content);
  }
  return root;
}
