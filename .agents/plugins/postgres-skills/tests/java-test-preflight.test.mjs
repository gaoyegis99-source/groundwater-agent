import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  detectJavaTestRelease,
  validateJavaTestFiles,
  validateJavaTestSource,
} from "../skills/migrate-oracle-app/scripts/java-test-preflight.mjs";

const script = fileURLToPath(new URL("../skills/migrate-oracle-app/scripts/java-test-preflight.mjs", import.meta.url));
const digest = (value) => createHash("sha256").update(value).digest("hex");
const compiler = (configuration, executions = "") =>
  `<build><plugins><plugin>
    <artifactId>maven-compiler-plugin</artifactId>
    <configuration>${configuration}</configuration>
    ${executions ? `<executions>${executions}</executions>` : ""}
  </plugin></plugins></build>`;
const project = (body = "", artifact = "app") =>
  `<project><modelVersion>4.0.0</modelVersion>
    <groupId>example</groupId><artifactId>${artifact}</artifactId><version>1</version>
    ${body}
  </project>`;
const execution = (id, goal, configuration, extra = "") =>
  `<execution><id>${id}</id><goals><goal>${goal}</goal></goals>
    ${extra}<configuration>${configuration}</configuration></execution>`;

async function fixture(t, pom = project()) {
  // Fixtures stay in the repository, not the OS temporary directory.
  const base = await mkdtemp(path.join(process.cwd(), ".java-test-preflight-test-"));
  t.after(() => rm(base, { recursive: true, force: true }));
  const root = path.join(base, "codebase");
  await mkdir(root);
  await writeFile(path.join(root, "pom.xml"), pom);
  return { base, root, outputDirectory: path.join(base, "private-output") };
}

async function write(root, relative, content) {
  const absolute = path.join(root, ...relative.split("\\"));
  await mkdir(path.dirname(absolute), { recursive: true });
  await writeFile(absolute, content);
  return absolute;
}

async function detect(root, effectivePomText, options = {}) {
  return detectJavaTestRelease({
    root,
    runtimeJavaMajor: 22,
    effectivePomText,
    ...options,
  });
}

function outputFile(args) {
  const output = args.find((arg) => arg.startsWith("-Doutput="));
  assert.ok(output, "the effective POM must use a private output file");
  return output.slice("-Doutput=".length);
}

test("explicit release produces deterministic, sanitized module evidence without commands", async (t) => {
  const { root } = await fixture(t);
  const xml = project(
    `<properties><service.password>synthetic-secret-marker</service.password></properties>
     ${compiler("<release>17</release><source>1.8</source><target>1.8</target>")}`,
  );
  const options = {
    mavenArgs: ["-Pproduction", "-Dauth.token=synthetic-argument-marker"],
    runtimeJavaMajor: 17,
    commandRunner() { assert.fail("supplied evidence must not execute a command"); },
  };
  const result = await detect(root, xml, options);
  const repeated = await detect(root, xml, options);

  assert.deepEqual(Object.keys(result), [
    "schemaVersion", "status", "capturedAt", "inputDigest", "runtimeJavaMajor", "modules", "evidence",
  ]);
  assert.equal(result.schemaVersion, 1);
  assert.equal(result.status, "resolved");
  assert.equal(new Date(result.capturedAt).toISOString(), result.capturedAt);
  assert.equal(result.inputDigest, repeated.inputDigest);
  assert.deepEqual(result.modules, [{
    modulePath: ".",
    mainRelease: 17,
    testRelease: 17,
    features: {
      textBlocks: true,
      localVariableInference: true,
      records: true,
      sealedTypes: true,
      switchExpressions: true,
    },
  }]);
  const serialized = JSON.stringify(result);
  assert.doesNotMatch(serialized, /synthetic-secret-marker|synthetic-argument-marker|<project>|service\.password/);
  assert.equal(result.evidence.find((entry) => entry.kind === "effective-pom").sha256, digest(xml));
});

test("legacy explicit 1.8 source and target resolve to Java 8", async (t) => {
  const { root } = await fixture(t);
  const result = await detect(root, project(compiler("<source>1.8</source><target>1.8</target>")), {
    runtimeJavaMajor: 8,
  });
  assert.equal(result.modules[0].mainRelease, 8);
  assert.equal(result.modules[0].testRelease, 8);
  assert.ok(Object.values(result.modules[0].features).every((value) => value === false));
});

test("compiler properties, nested substitution, and Maven user properties are authoritative", async (t) => {
  const { root } = await fixture(t);
  const xml = project(`<properties>
    <language.base>1.8</language.base><java.level>\${language.base}</java.level>
    <maven.compiler.source>\${java.level}</maven.compiler.source>
    <maven.compiler.target>\${java.level}</maven.compiler.target>
    <maven.compiler.testSource>11</maven.compiler.testSource>
    <maven.compiler.testTarget>11</maven.compiler.testTarget>
  </properties>`);
  const result = await detect(root, xml, { mavenArgs: ["-Djava.level=8"] });
  assert.equal(result.modules[0].mainRelease, 8);
  assert.equal(result.modules[0].testRelease, 11);
  assert.equal(result.modules[0].features.localVariableInference, true);
  assert.equal(result.modules[0].features.textBlocks, false);
});

test("explicit compiler configuration beats same-parameter properties", async (t) => {
  const { root } = await fixture(t);
  const xml = project(`<properties><maven.compiler.release>8</maven.compiler.release></properties>
    ${compiler("<release>11</release><testRelease>17</testRelease>")}`);
  const result = await detect(root, xml, { mavenArgs: ["-Dmaven.compiler.release=21"] });
  assert.equal(result.modules[0].mainRelease, 11);
  assert.equal(result.modules[0].testRelease, 17);
});

test("effective parent properties and active profile results apply per module, not inactive profiles", async (t) => {
  const rootPom = project("<packaging>pom</packaging><modules><module>child</module></modules>");
  const { root } = await fixture(t, rootPom);
  await write(root, "child\\pom.xml", project("", "child"));
  const parent = project(`<packaging>pom</packaging><modules><module>child</module></modules>
    <properties><java.level>8</java.level><maven.compiler.release>\${java.level}</maven.compiler.release></properties>`);
  const child = project(`<parent><groupId>example</groupId><artifactId>app</artifactId><version>1</version></parent>
    <properties><profile.test.level>11</profile.test.level></properties>
    ${compiler("<release>\${java.level}</release><testRelease>\${profile.test.level}</testRelease>")}
    <profiles><profile><id>inactive</id>${compiler("<release>99</release>")}</profile></profiles>`, "child");
  const result = await detect(root, `<projects>${parent}${child}</projects>`);
  assert.deepEqual(
    result.modules.map(({ modulePath, mainRelease, testRelease }) => ({ modulePath, mainRelease, testRelease })),
    [
      { modulePath: ".", mainRelease: 8, testRelease: 8 },
      { modulePath: "child", mainRelease: 8, testRelease: 11 },
    ],
  );
});

test("effective profile-only reactor modules map using effective module declarations", async (t) => {
  const { root } = await fixture(t, project("<packaging>pom</packaging>"));
  await write(root, "profile-child\\pom.xml", project("", "profile-child"));
  const parent = project("<packaging>pom</packaging><modules><module>profile-child</module></modules>");
  const child = project(compiler("<release>11</release>"), "profile-child");
  const result = await detect(root, `<projects>${child}${parent}</projects>`);
  assert.deepEqual(result.modules.map((module) => module.modulePath), ["profile-child"]);
});

test("testCompile execution release overrides only the test lane", async (t) => {
  const { root } = await fixture(t);
  const xml = project(compiler(
    "<release>8</release>",
    execution("default-testCompile", "testCompile", "<release>17</release>"),
  ));
  const result = await detect(root, xml);
  assert.equal(result.modules[0].mainRelease, 8);
  assert.equal(result.modules[0].testRelease, 17);
});

test("testSource and testTarget execution overrides preserve a legacy main source", async (t) => {
  const { root } = await fixture(t);
  const xml = project(compiler(
    "<source>1.8</source><target>1.8</target>",
    execution("default-testCompile", "testCompile", "<testSource>11</testSource><testTarget>11</testTarget>"),
  ));
  const result = await detect(root, xml);
  assert.equal(result.modules[0].mainRelease, 8);
  assert.equal(result.modules[0].testRelease, 11);
});

test("compiler plugin management and execution combine.self overrides are honored", async (t) => {
  const { root } = await fixture(t);
  const managed = project(`<build><pluginManagement><plugins><plugin>
    <artifactId>maven-compiler-plugin</artifactId><configuration><release>11</release></configuration>
  </plugin></plugins></pluginManagement></build>`);
  assert.equal((await detect(root, managed)).modules[0].testRelease, 11);
  const overriding = project(compiler("<release>17</release>", `<execution>
    <id>default-testCompile</id><goals><goal>testCompile</goal></goals>
    <configuration combine.self="override"><testSource>8</testSource><testTarget>8</testTarget></configuration>
  </execution>`));
  assert.equal((await detect(root, overriding)).modules[0].testRelease, 8);
});

test("disabled compiler executions do not conflict with effective levels", async (t) => {
  const { root } = await fixture(t);
  const xml = project(compiler(
    "<release>11</release>",
    execution("disabled-test", "testCompile", "<release>17</release>", "<phase>none</phase>"),
  ));
  assert.equal((await detect(root, xml)).modules[0].testRelease, 11);
});

test("source below target gates language conservatively and checks the target runtime", async (t) => {
  const { root } = await fixture(t);
  const xml = project(compiler("<source>8</source><target>11</target>"));
  assert.equal((await detect(root, xml, { runtimeJavaMajor: 11 })).modules[0].testRelease, 8);
  await assert.rejects(detect(root, xml, { runtimeJavaMajor: 8 }), { code: "JAVA_RUNTIME_INCOMPATIBLE" });
});

test("older runtime JDK rejects either main or test release", async (t) => {
  const { root } = await fixture(t);
  await assert.rejects(detect(root, project(compiler("<release>17</release>")), {
    runtimeJavaMajor: 11,
  }), { code: "JAVA_RUNTIME_INCOMPATIBLE" });
  await assert.rejects(detect(root, project(compiler("<release>8</release><testRelease>11</testRelease>")), {
    runtimeJavaMajor: 8,
  }), { code: "JAVA_RUNTIME_INCOMPATIBLE" });
});

test("namespaces, comments, CDATA, predefined entities, and numeric entities parse safely", async (t) => {
  const { root } = await fixture(t);
  const xml = `<?xml version="1.0" encoding="UTF-8"?>
    <!-- <release>99</release> <!DOCTYPE harmless-comment> -->
    <m:project xmlns:m="http://maven.apache.org/POM/4.0.0">
      <m:artifactId>app</m:artifactId>
      <m:properties><m:java.level><![CDATA[ 1.8 ]]></m:java.level><m:note>A &amp; B &lt; C</m:note></m:properties>
      <m:build><m:plugins><m:plugin><m:artifactId>maven-compiler-plugin</m:artifactId>
        <m:configuration><m:source>\${java.level}</m:source><m:target>1.&#x38;</m:target></m:configuration>
      </m:plugin></m:plugins></m:build>
    </m:project>`;
  assert.equal((await detect(root, xml)).modules[0].testRelease, 8);
  const defaultNamespace = project(compiler("<release><![CDATA[17]]></release>")).replace(
    "<project>",
    '<project xmlns="http://maven.apache.org/POM/4.0.0">',
  );
  assert.equal((await detect(root, defaultNamespace)).modules[0].testRelease, 17);
});

test("DOCTYPE, external entities, and undeclared entities are rejected without commands", async (t) => {
  const { root } = await fixture(t);
  for (const xml of [
    `<!DOCTYPE project SYSTEM "https://invalid.example/pom.dtd">${project(compiler("<release>17</release>"))}`,
    `<!DOCTYPE project [<!ENTITY release SYSTEM "file:///synthetic-secret">]>${project(compiler("<release>&release;</release>"))}`,
    project(compiler("<release>&unknown;</release>")),
  ]) {
    await assert.rejects(detect(root, xml, {
      commandRunner() { assert.fail("entity rejection must never execute commands"); },
    }), { code: "JAVA_POM_XML_INVALID" });
  }
});

test("malformed XML and malformed Maven scalar fields fail explicitly", async (t) => {
  const { root } = await fixture(t);
  for (const xml of [
    "<project><artifactId>app</artifactId>",
    "<project><release>17</source></project>",
    `${project()}${project()}`,
    '<project bad=unquoted />',
    '<project bad="first" bad="second" />',
    "<m:project><m:artifactId>app</m:artifactId></m:project>",
    "<project><!-- invalid -- comment --></project>",
    project(compiler("<release><level>17</level></release>")),
  ]) {
    await assert.rejects(detect(root, xml), /Malformed|Malformed scalar/);
  }
});

test("XML depth and byte budgets fail closed", async (t) => {
  const { root } = await fixture(t);
  await assert.rejects(detect(root, "<project>" + "<nested>".repeat(130) + "</nested>".repeat(130) + "</project>"), /budget/);
  await assert.rejects(detect(root, " ".repeat(8 * 1024 * 1024 + 1)), /bounded/);
  await assert.rejects(detect(root, `<project ${Array.from({ length: 257 }, (_, index) => `a${index}="value"`).join(" ")} />`), /budget/);
  await assert.rejects(detect(root, project(compiler("<release>17</release>") + "<note>\uffff</note>")), {
    code: "JAVA_POM_XML_INVALID",
  });
});

test("unresolved defaults, target-only levels, cycles, and malformed releases never use the JDK", async (t) => {
  const { root } = await fixture(t);
  for (const xml of [
    project(),
    project("<properties><java.version>17</java.version></properties>"),
    project(compiler("<target>8</target>")),
    project(compiler("<release>\${missing.release}</release>")),
    project(`<properties><a>\${b}</a><b>\${a}</b></properties>${compiler("<release>\${a}</release>")}`),
    project(compiler("<release>17.0</release>")),
    project(compiler("<release/>")),
    project(compiler("<release>0</release>")),
  ]) {
    await assert.rejects(detect(root, xml), { code: "JAVA_RELEASE_UNRESOLVED" });
  }
});

test("conflicting compiler executions, duplicate configuration, and incompatible source/target reject", async (t) => {
  const { root } = await fixture(t);
  const conflicting = project(compiler("<release>8</release>",
    execution("first-test", "testCompile", "<testRelease>8</testRelease>") +
    execution("second-test", "testCompile", "<testRelease>11</testRelease>")));
  await assert.rejects(detect(root, conflicting), { code: "JAVA_RELEASE_AMBIGUOUS" });
  await assert.rejects(detect(root, project(compiler("<release>8</release><release>17</release>"))), /Ambiguous/);
  await assert.rejects(detect(root, project(compiler("<source>17</source><target>8</target>"))), {
    code: "JAVA_COMPILER_INCOMPATIBLE",
  });
  await assert.rejects(detect(root, project(compiler("<release>8</release><compilerArgs><arg>--release</arg><arg>17</arg></compilerArgs>"))), /version overrides/);
  await assert.rejects(detect(root, project(`<properties><extra.compiler.flag>--release=17</extra.compiler.flag></properties>
    ${compiler("<release>8</release><compilerArgs><arg>\${extra.compiler.flag}</arg></compilerArgs>")}`)), /version overrides/);
  await assert.rejects(detect(root, project(compiler("<release>8</release><compilerArguments><source>17</source></compilerArguments>"))), /version overrides/);
});

test("compiler execution goals, phases, and IDs resolve effective properties", async (t) => {
  const { root } = await fixture(t);
  const xml = project(`<properties>
      <execution.id>default-testCompile</execution.id><execution.goal>testCompile</execution.goal>
      <execution.phase>none</execution.phase>
    </properties>${compiler("<release>11</release>", execution(
      "\${execution.id}", "\${execution.goal}", "<release>21</release>", "<phase>\${execution.phase}</phase>",
    ))}`);
  assert.equal((await detect(root, xml, { runtimeJavaMajor: 11 })).modules[0].testRelease, 11);
});

test("ambiguous or unknown reactor coordinates reject rather than guessing artifact paths", async (t) => {
  const { root } = await fixture(t, project("<modules><module>one</module><module>two</module></modules>"));
  await write(root, "one\\pom.xml", project("", "duplicate"));
  await write(root, "two\\pom.xml", project("", "duplicate"));
  await assert.rejects(detect(root, `<projects>${project(compiler("<release>8</release>"), "duplicate")}</projects>`), /Ambiguous Maven module/);
  await assert.rejects(detect(root, project(compiler("<release>8</release>"), "not-in-layout")), {
    code: "JAVA_MODULE_UNKNOWN",
  });
});

test("invalid detector inputs and lifecycle arguments reject before commands", async (t) => {
  const { root } = await fixture(t);
  const xml = project(compiler("<release>17</release>"));
  for (const options of [
    { runtimeJavaMajor: "17" },
    { runtimeJavaMajor: 0 },
    { runtimeJavaMajor: null },
    { effectivePomText: {} },
    { commandRunner: "not-a-function" },
    { mavenArgs: ["compile"] },
    { mavenArgs: ["install"] },
    { mavenArgs: ["-Doutput=elsewhere.xml"] },
    { mavenArgs: ["--define=forceStdout=true"] },
    { mavenArgs: ["-P"] },
    { mavenArgs: ["-Pbad\u0000profile"] },
  ]) {
    await assert.rejects(detect(root, xml, options), /Invalid|must|private|options/);
  }
  await assert.rejects(detectJavaTestRelease(), /Invalid root/);
});

test("mocked commands run java -version and one offline effective-POM goal with actual arguments", async (t) => {
  const { root, outputDirectory } = await fixture(t);
  const calls = [];
  const xml = project(compiler("<release>\${selected.release}</release>"));
  const result = await detectJavaTestRelease({
    root,
    outputDirectory,
    javaExecutable: "custom-java",
    mavenExecutable: ".\\mvnw.cmd",
    mavenArgs: ["-P", "legacy", "-Dselected.release=8"],
    async commandRunner(cwd, command, args, options) {
      calls.push({ cwd, command, args });
      assert.equal(cwd, root);
      assert.ok(options.env);
      if (command === "custom-java") {
        assert.deepEqual(args, ["-version"]);
        return { status: 0, stderr: 'openjdk version "17.0.14" 2025-01-21', stdout: "" };
      }
      await writeFile(outputFile(args), xml);
      return { status: 0, stdout: "synthetic command output", stderr: "" };
    },
  });
  assert.equal(calls.length, 2);
  assert.equal(calls[1].command, ".\\mvnw.cmd");
  assert.ok(calls[1].args.includes("-o"));
  assert.deepEqual(calls[1].args.slice(2, 5), ["-P", "legacy", "-Dselected.release=8"]);
  assert.equal(calls[1].args.filter((arg) => arg === "help:effective-pom").length, 1);
  assert.ok(path.dirname(outputFile(calls[1].args)) === outputDirectory);
  assert.equal(result.runtimeJavaMajor, 17);
  assert.equal(result.modules[0].testRelease, 8);
  assert.deepEqual(await readdir(outputDirectory), []);
});

test("mocked legacy runtime output resolves 1.8 without deriving the language level from it", async (t) => {
  const { root } = await fixture(t);
  const result = await detectJavaTestRelease({
    root,
    effectivePomText: project(compiler("<source>1.8</source><target>1.8</target>")),
    commandRunner(cwd, command, args) {
      assert.equal(command, "java");
      assert.deepEqual(args, ["-version"]);
      return { status: 0, stderr: 'java version "1.8.0_422"' };
    },
  });
  assert.equal(result.runtimeJavaMajor, 8);
  assert.equal(result.modules[0].testRelease, 8);
});

test("one online retry is limited to explicit missing-plugin resolution failures", async (t) => {
  const { root, outputDirectory } = await fixture(t);
  const calls = [];
  const result = await detectJavaTestRelease({
    root,
    outputDirectory,
    runtimeJavaMajor: 17,
    mavenArgs: ["--activate-profiles=production"],
    async commandRunner(cwd, command, args) {
      calls.push(args);
      if (calls.length === 1) {
        return {
          status: 1,
          stderr: "Plugin org.apache.maven.plugins:maven-help-plugin could not be resolved: artifact has not been downloaded in offline mode",
        };
      }
      await writeFile(outputFile(args), project(compiler("<release>17</release>")));
      return { status: 0 };
    },
  });
  assert.equal(calls.length, 2);
  assert.ok(calls[0].includes("-o"));
  assert.ok(!calls[1].includes("-o"));
  assert.ok(calls[1].includes("--activate-profiles=production"));
  assert.equal(result.evidence[0].onlineRetry, true);
  assert.deepEqual(await readdir(outputDirectory), []);
});

test("offline dependency-resolution failures can retry, but arbitrary build failures cannot", async (t) => {
  const { root, outputDirectory } = await fixture(t);
  for (const [message, retry] of [
    ["Could not resolve dependencies for project: artifact is not cached in offline mode", true],
    ["Non-parseable POM: synthetic-secret-marker", false],
    ["BUILD FAILURE: arbitrary build error synthetic-secret-marker", false],
    ["Compilation failure: synthetic-secret-marker", false],
  ]) {
    let calls = 0;
    const promise = detectJavaTestRelease({
      root,
      outputDirectory,
      runtimeJavaMajor: 17,
      async commandRunner(cwd, command, args) {
        calls += 1;
        if (calls === 1) return { status: 1, stderr: message };
        await writeFile(outputFile(args), project(compiler("<release>17</release>")));
        return { status: 0 };
      },
    });
    if (retry) await promise;
    else {
      await assert.rejects(promise, (error) => {
        assert.equal(error.code, "JAVA_PREFLIGHT_COMMAND_FAILED");
        assert.doesNotMatch(error.message, /synthetic-secret-marker/);
        return true;
      });
    }
    assert.equal(calls, retry ? 2 : 1);
  }
});

test("an explicitly offline caller is not silently retried online", async (t) => {
  const { root, outputDirectory } = await fixture(t);
  let calls = 0;
  await assert.rejects(detectJavaTestRelease({
    root,
    outputDirectory,
    runtimeJavaMajor: 17,
    mavenArgs: ["--offline"],
    commandRunner() {
      calls += 1;
      return { status: 1, stderr: "No plugin found for prefix 'help'" };
    },
  }), { code: "JAVA_PREFLIGHT_COMMAND_FAILED" });
  assert.equal(calls, 1);
  assert.deepEqual(await readdir(outputDirectory), []);
});

test("missing effective-POM output, failed processes, and ambiguous runtime versions fail explicitly", async (t) => {
  const { root, outputDirectory } = await fixture(t);
  await assert.rejects(detectJavaTestRelease({
    root, outputDirectory, runtimeJavaMajor: 17, commandRunner: () => ({ status: 0 }),
  }), /Malformed effective POM/);
  for (const result of [
    { status: 1, stderr: "synthetic-secret-marker" },
    { status: null, signal: "SIGTERM" },
    { status: 0, stderr: "not a Java version" },
    { status: 0, stderr: 'openjdk version "17.0.1"\njava version "21.0.1"' },
  ]) {
    await assert.rejects(detectJavaTestRelease({
      root,
      effectivePomText: project(compiler("<release>17</release>")),
      commandRunner: () => result,
    }), (error) => {
      assert.doesNotMatch(error.message, /synthetic-secret-marker/);
      return true;
    });
  }
});

test("Maven -f selection maps a nested standalone project without selecting the repository POM", async (t) => {
  const { root } = await fixture(t);
  await write(root, "selected\\custom-pom.xml", project("", "selected"));
  const result = await detect(root, project(compiler("<release>11</release>"), "selected"), {
    mavenArgs: ["-f", "selected\\custom-pom.xml", "-Plegacy"],
  });
  assert.equal(result.modules[0].modulePath, "selected");
  assert.ok(result.evidence.find((entry) => entry.kind === "inputs").files.some(
    (file) => file.path === "selected\\custom-pom.xml",
  ));
  const validated = await validateJavaTestFiles({
    root, evidence: result,
    files: [{ path: "selected\\src\\test\\SelectedTest.java", source: "class SelectedTest { void test() { var value = 1; } }" }],
  });
  assert.equal(validated.files[0].modulePath, "selected");
  await assert.rejects(detect(root, project(compiler("<release>11</release>")), {
    mavenArgs: ["-f", "..\\escape.xml"],
  }), { code: "JAVA_PATH_ESCAPE" });
});

const syntaxCases = [
  ["textBlocks", 15, 'class Draft { String value = """\nexample\n"""; }'],
  ["localVariableInference", 10, "class Draft { void test() { var answer = 42; } }"],
  ["records", 16, "record Pair(int left, int right) {}"],
  ["sealedTypes", 17, "sealed interface Shape permits Circle {} final class Circle implements Shape {}"],
  ["switchExpressions", 14, "class Draft { int test(int n) { return switch (n) { case 1 -> 2; default -> 3; }; } }"],
];

for (const [feature, minimum, source] of syntaxCases) {
  test(`${feature} rejects below Java ${minimum} with a source location, not a source snapshot`, () => {
    assert.throws(
      () => validateJavaTestSource(source, { testRelease: minimum - 1 }, "Draft.java"),
      (error) => {
        assert.equal(error.code, "JAVA_TEST_SYNTAX_INCOMPATIBLE");
        assert.equal(error.feature, feature);
        assert.equal(error.requiredRelease, minimum);
        assert.equal(error.testRelease, minimum - 1);
        assert.ok(error.line > 0);
        assert.ok(error.column > 0);
        assert.match(error.message, /Draft\.java:\d+:\d+/);
        assert.doesNotMatch(error.message, /class Draft|record Pair|permits Circle|example/);
        return true;
      },
    );
  });

  test(`${feature} passes its non-preview release boundary`, () => {
    assert.deepEqual(validateJavaTestSource(source, { testRelease: minimum }), {
      status: "passed", validation: "lexical", testRelease: minimum,
    });
  });
}

test("comments, ordinary strings, escaped quotes, and character literals do not trigger syntax gates", () => {
  const source = String.raw`
    // """ var answer = 1; record Pair(int x) {} sealed class X {} switch (n) { default -> 1; }
    /* """ var answer = 1; record Pair(int x) {} sealed class X {} */
    class Draft {
      String markers = "\"\"\" var answer = 1; record Pair(int x) {} sealed class X {} switch (n) { default -> 1; }";
      String escaped = "\\u0076ar value = 1;";
      char quote = '\'';
      char doubleQuote = '"';
      void test() { int var = 1; int record = var; int sealed = record; }
    }`;
  assert.equal(validateJavaTestSource(source, { testRelease: 8 }).status, "passed");
});

test("contextual keyword variable, method, field, imported type, and class names do not trigger", () => {
  const source = `
    import example.record;
    class var {}
    class sealed {}
    class Names {
      var field = new var();
      record build() { return null; }
      sealed shape;
      Class<?> type = sealed.class;
      int record() { return 1; }
      int sealed() { return 2; }
      void test() { var ordinaryTypedValue = new var(); int var = 1; int record = var; }
    }`;
  assert.equal(validateJavaTestSource(source, { testRelease: 8 }).status, "passed");
  assert.equal(validateJavaTestSource("class record {} class Names { record build() { return new record(); } }", {
    testRelease: 8,
  }).status, "passed");
});

test("generic records and annotated sealed modifiers are gated", () => {
  for (const source of [
    "record Box<T>(T value) {}",
    "record Box<T extends Comparable<T>>(T value) implements Marker {}",
  ]) {
    assert.throws(() => validateJavaTestSource(source, { testRelease: 15 }), { feature: "records" });
    assert.equal(validateJavaTestSource(source, { testRelease: 16 }).status, "passed");
  }
  for (const source of [
    "sealed @Marker class Parent permits Child {}",
    "sealed @example.Marker(value = \"name\") abstract class Parent permits Child {}",
  ]) {
    assert.throws(() => validateJavaTestSource(source, { testRelease: 16 }), { feature: "sealedTypes" });
  }
});

test("lambda var parameters are gated at Java 11 rather than Java 10", () => {
  const source = "class Draft { Object function = (var first, var second) -> first; }";
  assert.throws(() => validateJavaTestSource(source, { testRelease: 10 }), {
    feature: "localVariableInference", requiredRelease: 11,
  });
  assert.equal(validateJavaTestSource(source, { testRelease: 11 }).status, "passed");
  const annotated = "class Draft { Object function = (@Marker final var value) -> value; }";
  assert.throws(() => validateJavaTestSource(annotated, { testRelease: 10 }), { requiredRelease: 11 });
});

test("classic switch statements with Java 8 lambdas and yield identifiers are not switch expressions", () => {
  const source = `class Draft {
    int yield;
    void yield() {}
    void test(int value) {
      switch (value) {
        case 1: java.util.function.Supplier<Integer> f = () -> 2; yield = f.get(); break;
        default: yield(); break;
      }
    }
  }`;
  assert.equal(validateJavaTestSource(source, { testRelease: 8 }).status, "passed");
});

test("colon-style switch expressions and ternary switch expressions are gated", () => {
  for (const source of [
    "class Draft { int test(int n) { return switch(n) { case 1: yield 2; default: yield 3; }; } }",
    "class Draft { int test(int n) { return (int) switch(n) { default: yield 3; }; } }",
    "class Draft { int test(int n) { return n == 1 ? 2 : switch(n) { default: yield 3; }; } }",
  ]) {
    assert.throws(() => validateJavaTestSource(source, { testRelease: 13 }), {
      code: "JAVA_TEST_SYNTAX_INCOMPATIBLE", feature: "switchExpressions",
    });
  }
});

test("local var is caught in enhanced loops, initializers, and control flow containing new expressions", () => {
  for (const source of [
    "class Draft { void test() { for (var item : items) {} } }",
    "class Draft { { var item = 1; } }",
    "class Draft { void test() { if (new Object() != null) { var café = 1; } } }",
  ]) {
    assert.throws(() => validateJavaTestSource(source, { testRelease: 8 }), {
      feature: "localVariableInference",
    });
  }
  assert.equal(validateJavaTestSource("class var {} class Draft { var field = new var(); }", {
    testRelease: 8,
  }).status, "passed");
});

test("non-sealed type modifiers are gated, and labels use original CRLF source locations", () => {
  assert.throws(() => validateJavaTestSource("non-sealed class Child extends Parent {}", { testRelease: 16 }), {
    feature: "sealedTypes",
  });
  assert.throws(
    () => validateJavaTestSource("class Draft {\r\n  void test() {\r\n    var answer = 42;\r\n  }\r\n}", {
      testRelease: 8,
    }, "src\\Draft.java"),
    { fileLabel: "src\\Draft.java", line: 3, column: 5, feature: "localVariableInference" },
  );
});

test("Java Unicode escapes cannot hide syntax and preserve raw source locations", () => {
  const source = String.raw`class Draft {
  void test() {
    \u0076\u0061\u0072 answer = 42;
  }
}`;
  assert.throws(() => validateJavaTestSource(source, { testRelease: 8 }), {
    feature: "localVariableInference", line: 3, column: 5,
  });
  const comments = String.raw`\u002f\u002f var answer = 1; record Pair(int value) {}
    class Draft { String value = "\u0076ar answer = 1;"; }`;
  assert.equal(validateJavaTestSource(comments, { testRelease: 8 }).status, "passed");
});

test("unterminated lexical constructs, invalid source inputs, and invalid module flags fail closed", () => {
  for (const source of ['class Draft { String value = "unterminated;', "/* unterminated", '"""']) {
    assert.throws(() => validateJavaTestSource(source, { testRelease: 8 }), {
      code: "JAVA_SOURCE_LEXICAL_INVALID",
    });
  }
  assert.throws(() => validateJavaTestSource(String.raw`\u00zz`, { testRelease: 17 }), /Unicode escape/);
  assert.throws(() => validateJavaTestSource(null, { testRelease: 8 }), /bounded text/);
  assert.throws(() => validateJavaTestSource("class Draft {}", { testRelease: "8" }), /integer/);
  assert.throws(() => validateJavaTestSource("class Draft {}", { testRelease: 8, features: { records: true } }), /feature evidence/);
  assert.throws(() => validateJavaTestSource("class Draft {}", { testRelease: 8 }, "bad\nlabel"), /file label/);
  assert.throws(() => validateJavaTestSource(" ".repeat(4 * 1024 * 1024 + 1), { testRelease: 8 }), /bounded text/);
});

async function reactor(t) {
  const rootPom = project("<modules><module>older</module></modules>");
  const { root, base } = await fixture(t, rootPom);
  await write(root, "older\\pom.xml", project("<modules><module>nested</module></modules>", "older"));
  await write(root, "older\\nested\\pom.xml", project("", "nested"));
  const effective = `<projects>
    ${project(compiler("<release>17</release>") + "<modules><module>older</module></modules>")}
    ${project(compiler("<release>8</release>") + "<modules><module>nested</module></modules>", "older")}
    ${project(compiler("<release>8</release><testRelease>11</testRelease>"), "nested")}
  </projects>`;
  const evidence = await detect(root, effective);
  return { root, base, evidence };
}

test("file validation chooses the nearest reactor module and returns exact byte digests", async (t) => {
  const { root, evidence } = await reactor(t);
  const sources = [
    ["src\\test\\RootTest.java", "record RootTest(int value) {}"],
    ["older\\src\\test\\OlderTest.java", "class OlderTest { void test() { int value = 1; } }"],
    ["older\\nested\\src\\test\\NestedTest.java", "class NestedTest { void test() { var value = 1; } }"],
  ];
  for (const [file, source] of sources) await write(root, file, source);
  const result = await validateJavaTestFiles({ root, evidence, files: sources.map(([file]) => file) });
  assert.equal(result.status, "passed");
  assert.equal(result.validation, "lexical");
  assert.deepEqual(result.files, sources.map(([file, source], index) => ({
    path: file,
    sha256: digest(source),
    modulePath: [".", "older", "older\\nested"][index],
    testRelease: [17, 8, 11][index],
  })));
  await assert.rejects(validateJavaTestFiles({
    root, evidence,
    files: [{ path: "older\\src\\test\\BadTest.java", source: "class BadTest { void test() { var value = 1; } }" }],
  }), { feature: "localVariableInference", testRelease: 8 });
  await assert.rejects(validateJavaTestFiles({
    root, evidence,
    files: [{ path: "older\\nested\\src\\test\\BadTest.java", source: "record BadTest(int value) {}" }],
  }), { feature: "records", testRelease: 11 });
});

test("in-memory drafts need not exist and validation does not write them", async (t) => {
  const { root } = await fixture(t);
  const evidence = await detect(root, project(compiler("<release>11</release>")));
  const file = "new\\src\\test\\Draft.java";
  const source = "class Draft { void test() { var value = 1; } }";
  const result = await validateJavaTestFiles({ root, evidence, files: [{ path: file, source }] });
  assert.equal(result.files[0].sha256, digest(source));
  await assert.rejects(readFile(path.join(root, ...file.split("\\"))), { code: "ENOENT" });
});

test("test file paths outside the root, including prefix siblings, are rejected", async (t) => {
  const { root, base } = await fixture(t);
  const evidence = await detect(root, project(compiler("<release>17</release>")));
  const outside = path.join(base, "codebase-sibling", "Escape.java");
  for (const file of ["..\\Escape.java", outside]) {
    await assert.rejects(validateJavaTestFiles({
      root, evidence, files: [{ path: file, source: "class Escape {}" }],
    }), { code: "JAVA_PATH_ESCAPE" });
  }
});

test("unknown nested POM owners and unknown evidence modules are rejected", async (t) => {
  const { root, evidence } = await reactor(t);
  await write(root, "unresolved\\pom.xml", project("", "unresolved"));
  await write(root, "unresolved\\src\\test\\UnknownTest.java", "class UnknownTest {}");
  await assert.rejects(validateJavaTestFiles({
    root, evidence, files: ["unresolved\\src\\test\\UnknownTest.java"],
  }), { code: "JAVA_MODULE_UNKNOWN" });
  const { root: aggregate } = await fixture(t, project("<packaging>pom</packaging><modules><module>child</module></modules>"));
  await write(aggregate, "child\\pom.xml", project("", "child"));
  const aggregateEvidence = await detect(aggregate,
    `<projects>${project("<packaging>pom</packaging><modules><module>child</module></modules>")}${project(compiler("<release>11</release>"), "child")}</projects>`);
  await assert.rejects(validateJavaTestFiles({
    root: aggregate, evidence: aggregateEvidence,
    files: [{ path: "src\\test\\UnknownTest.java", source: "class UnknownTest {}" }],
  }), { code: "JAVA_MODULE_UNKNOWN" });
});

test("symlinked test ancestors, draft ancestors, root directories, and input POMs are rejected", async (t) => {
  const { root, base } = await fixture(t);
  const evidence = await detect(root, project(compiler("<release>17</release>")));
  const outside = path.join(base, "outside");
  await mkdir(outside);
  await writeFile(path.join(outside, "LinkedTest.java"), "class LinkedTest {}");
  const linked = path.join(root, "linked");
  await symlink(outside, linked, process.platform === "win32" ? "junction" : "dir");
  for (const files of [
    ["linked\\LinkedTest.java"],
    [{ path: "linked\\Draft.java", source: "class Draft {}" }],
  ]) {
    await assert.rejects(validateJavaTestFiles({ root, evidence, files }), { code: "JAVA_PATH_SYMLINK" });
  }
  const rootLink = path.join(base, "root-link");
  await symlink(root, rootLink, process.platform === "win32" ? "junction" : "dir");
  await assert.rejects(detect(rootLink, project(compiler("<release>17</release>"))), {
    code: "JAVA_PATH_SYMLINK",
  });
  await rm(path.join(root, "pom.xml"));
  await mkdir(path.join(outside, "inputs"));
  await writeFile(path.join(outside, "inputs", "pom.xml"), project());
  await symlink(path.join(outside, "inputs"), path.join(root, "inputs"), process.platform === "win32" ? "junction" : "dir");
  await assert.rejects(detect(root, project(compiler("<release>17</release>")), {
    mavenArgs: ["-f", "inputs\\pom.xml"],
  }), { code: "JAVA_PATH_SYMLINK" });
});

test("file symlinks are rejected where the platform permits creating them", async (t) => {
  const { root, base } = await fixture(t);
  const evidence = await detect(root, project(compiler("<release>17</release>")));
  const target = path.join(base, "Actual.java");
  await writeFile(target, "class Actual {}");
  try {
    await symlink(target, path.join(root, "Link.java"), "file");
  } catch (error) {
    if (error.code === "EPERM" || error.code === "EACCES") {
      t.skip("Windows file symlink creation requires a suitable privilege");
      return;
    }
    throw error;
  }
  await assert.rejects(validateJavaTestFiles({ root, evidence, files: ["Link.java"] }), {
    code: "JAVA_PATH_SYMLINK",
  });
});

test("changed POMs, changed settings, and a different root invalidate release evidence", async (t) => {
  const { root } = await fixture(t);
  await write(root, "settings.xml", "<settings />");
  const xml = project(compiler("<release>17</release>"));
  const evidence = await detect(root, xml, { mavenArgs: ["-s", "settings.xml"] });
  await write(root, "settings.xml", "<settings><!-- changed --></settings>");
  await assert.rejects(validateJavaTestFiles({ root, evidence, files: [] }), { code: "JAVA_EVIDENCE_STALE" });
  const fresh = await detect(root, xml);
  await write(root, "pom.xml", project("<!-- changed -->"));
  await assert.rejects(validateJavaTestFiles({ root, evidence: fresh, files: [] }), { code: "JAVA_EVIDENCE_STALE" });
  const { root: different } = await fixture(t);
  await assert.rejects(validateJavaTestFiles({ root: different, evidence: fresh, files: [] }), { code: "JAVA_EVIDENCE_STALE" });
});

test("new Maven configuration files and missing module directories invalidate evidence", async (t) => {
  const { root } = await fixture(t);
  const evidence = await detect(root, project(compiler("<release>17</release>")));
  await write(root, ".mvn\\maven.config", "-Pnew-profile");
  await assert.rejects(validateJavaTestFiles({ root, evidence, files: [] }), {
    code: "JAVA_EVIDENCE_STALE",
  });
  const { root: modules, evidence: moduleEvidence } = await reactor(t);
  await rm(path.join(modules, "older", "nested"), { recursive: true });
  await assert.rejects(validateJavaTestFiles({ root: modules, evidence: moduleEvidence, files: [] }), {
    code: "JAVA_EVIDENCE_STALE",
  });
});

test("invalid, modified, future-dated, and module-path-escaping evidence is rejected", async (t) => {
  const { root } = await fixture(t);
  const evidence = await detect(root, project(compiler("<release>8</release>")));
  for (const change of [
    (value) => { value.schemaVersion = 2; },
    (value) => { value.status = "unresolved"; },
    (value) => { value.inputDigest = "bad"; },
    (value) => { value.modules[0].testRelease = 17; },
    (value) => { value.modules[0].features.records = true; },
    (value) => { value.modules[0].modulePath = "..\\escape"; },
    (value) => { value.capturedAt = "not a date"; },
    (value) => { value.capturedAt = "2999-01-01T00:00:00.000Z"; },
    (value) => { value.evidence[0].sha256 = "invalid"; },
  ]) {
    const invalid = structuredClone(evidence);
    change(invalid);
    await assert.rejects(validateJavaTestFiles({ root, files: [], evidence: invalid }), /Invalid/);
  }
  await assert.rejects(validateJavaTestFiles({ root, files: [], evidence: undefined }), /Invalid/);
});

test("invalid files, missing files, duplicate paths, and non-Java inputs are rejected", async (t) => {
  const { root } = await fixture(t);
  const evidence = await detect(root, project(compiler("<release>8</release>")));
  await write(root, "Test.java", "class Test {}");
  for (const files of [
    "Test.java",
    ["Missing.java"],
    ["Test.java", "Test.java"],
    [{ path: "Draft.java", source: null }],
    ["pom.xml"],
  ]) {
    await assert.rejects(validateJavaTestFiles({ root, evidence, files }));
  }
});

test("build inputs changed during command execution cannot become fresh evidence", async (t) => {
  const { root, outputDirectory } = await fixture(t);
  await assert.rejects(detectJavaTestRelease({
    root, outputDirectory, runtimeJavaMajor: 17,
    async commandRunner(cwd, command, args) {
      await write(root, "pom.xml", project("<!-- changed during command -->"));
      await writeFile(outputFile(args), project(compiler("<release>17</release>")));
      return { status: 0 };
    },
  }), { code: "JAVA_EVIDENCE_STALE" });
});

test("imports are inert and the CLI emits evidence only after draft validation", async (t) => {
  const { root, base } = await fixture(t);
  const imported = await import(`${new URL("../skills/migrate-oracle-app/scripts/java-test-preflight.mjs", import.meta.url).href}?inert-import`);
  assert.equal(typeof imported.detectJavaTestRelease, "function");
  const config = path.join(base, "config.json");
  const output = path.join(base, "evidence.json");
  const options = {
    runtimeJavaMajor: 17,
    effectivePomText: project(compiler("<release>8</release>")),
    files: [{ path: "src\\test\\Draft.java", source: "class Draft {}" }],
  };
  await writeFile(config, JSON.stringify(options));
  const result = spawnSync(process.execPath, [script, root, config, "--output", output], {
    cwd: process.cwd(), encoding: "utf8", timeout: 20_000,
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, "");
  const evidence = JSON.parse(await readFile(output, "utf8"));
  assert.equal(evidence.modules[0].testRelease, 8);
  await writeFile(config, JSON.stringify({
    ...options,
    files: [{ path: "src\\test\\Draft.java", source: "record Draft(int value) {}" }],
  }));
  const failed = spawnSync(process.execPath, [script, root, config], {
    cwd: process.cwd(), encoding: "utf8", timeout: 20_000,
  });
  assert.equal(failed.status, 1);
  assert.equal(failed.stdout, "");
  assert.match(failed.stderr, /requires Java 16/);
  assert.doesNotMatch(failed.stderr, /record Draft/);
});
