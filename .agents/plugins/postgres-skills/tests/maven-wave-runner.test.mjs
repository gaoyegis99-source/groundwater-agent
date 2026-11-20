import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { lstat, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import {
  buildMavenWavePlan,
  executeMavenWavePlan,
  resolveSharedPostgres,
  runProcessCommand,
} from "../skills/migrate-oracle-app/scripts/run-maven-waves.mjs";
import { runProcessCommand as sharedProcessCommand } from "../skills/migrate-oracle-app/scripts/process-command.mjs";
import { detectJavaTestRelease } from "../skills/migrate-oracle-app/scripts/java-test-preflight.mjs";
import { fingerprintJavaBuildContext } from "../skills/migrate-oracle-app/scripts/java-build-context.mjs";
import { openMigrationRuntime, prepareJavaTestGeneration, replayMigrationState, withPrimaryExecution } from "../skills/migrate-oracle-app/scripts/migration-runtime.mjs";

test("Maven wave plan compiles once and executes four direct offline waves", async () => {
  const plan = buildMavenWavePlan(validConfig(), "C:\\work\\app");
  const summary = await executeMavenWavePlan(plan, { dryRun: true });
  const waves = plan.steps.filter((step) => step.phase === "wave");

  assert.equal(summary.testCompileCount, 1);
  assert.equal(summary.cleanPackageCount, 1);
  assert.equal(waves.length, 4);
  assert.equal(
    new Set(waves.map((wave) => wave.reportDirectory)).size,
    4,
  );
  assert.equal(new Set(waves.map((wave) => wave.coverageExec)).size, 4);
  for (const wave of waves) {
    assert.ok(wave.args.includes("-o"));
    assert.match(wave.args.join(" "), /maven-surefire-plugin:3\.5\.5:test/);
    assert.doesNotMatch(wave.args.join(" "), /\btest-compile\b|\bclean\b|\bpackage\b/);
    assert.match(wave.args.join(" "), /jacoco\.destFile=/);
    assert.doesNotMatch(wave.args.join(" "), /reportsDirectory=/);
    assert.equal(wave.sourceReportDirectory, "target/surefire-reports");
    assert.equal(wave.captureNativeReports, true);
  }
});

test("Maven wave plan warms dependencies and uses JaCoCo CLI once", () => {
  const plan = buildMavenWavePlan(validConfig(), "C:\\work\\app");
  const phases = plan.steps.map((step) => step.phase);
  const compileIndex = phases.indexOf("compile");
  const firstWaveIndex = phases.indexOf("wave");
  const packageSteps = plan.steps.filter((step) => step.phase === "package");
  const reportSteps = plan.steps.filter(
    (step) => step.phase === "coverage-report",
  );

  assert.ok(phases.filter((phase) => phase === "dependency-warmup").length >= 6);
  assert.ok(compileIndex > 0);
  assert.ok(firstWaveIndex > compileIndex);
  assert.equal(packageSteps.length, 1);
  assert.deepEqual(
    packageSteps[0].args.filter((arg) =>
      ["clean", "package", "-Dmaven.test.skip=true"].includes(arg),
    ),
    ["clean", "package", "-Dmaven.test.skip=true"],
  );
  assert.equal(reportSteps.length, 1);
  assert.equal(reportSteps[0].htmlGenerated, true);
  assert.match(
    plan.steps.find((step) => step.phase === "coverage-merge").args.join(" "),
    /org\.jacoco\.cli.*merge/,
  );
});

test("Failsafe waves execute verify before reports are captured", () => {
  const config = validConfig();
  config.integrationWaves = config.integrationWaves.map((wave) => ({
    ...wave,
    runner: "failsafe",
  }));
  config.fullSuiteRuns = [
    { id: "unit-suite", runner: "surefire", tests: [] },
    { id: "integration-suite", runner: "failsafe", tests: [] },
  ];
  const plan = buildMavenWavePlan(config, "C:\\work\\app");
  const integration = plan.steps.find(
    (step) => step.phase === "wave" && step.lane === "integration",
  );

  assert.match(
    integration.args.join(" "),
    /maven-failsafe-plugin:3\.5\.5:integration-test .*maven-failsafe-plugin:3\.5\.5:verify/,
  );
  assert.equal(integration.sourceReportDirectory, "target/failsafe-reports");
});

test("Maven wave plan runs one precompiled full suite and archive checks", () => {
  const plan = buildMavenWavePlan(validConfig(), "C:\\work\\app");
  const fullSuite = plan.steps.filter((step) => step.phase === "full-suite");
  const prePackage = plan.steps.filter(
    (step) => step.phase === "pre-package-check",
  );
  const archive = plan.steps.filter(
    (step) => step.phase === "archive-inspection",
  );

  assert.equal(fullSuite.length, 1);
  assert.ok(fullSuite[0].args.includes("-o"));
  assert.match(fullSuite[0].args.join(" "), /maven-surefire-plugin:3\.5\.5:test/);
  assert.equal(prePackage.length, 1);
  assert.equal(archive.length, 2);
  assert.deepEqual(archive[0].args.slice(0, 1), ["tf"]);
});

test("Maven wave plan rejects fewer than two waves in either lane", () => {
  const unitShort = validConfig();
  unitShort.unitWaves.pop();
  assert.throws(
    () => buildMavenWavePlan(unitShort, "C:\\work\\app"),
    /unit waves must contain 2-10 entries/,
  );

  const integrationShort = validConfig();
  integrationShort.integrationWaves.pop();
  assert.throws(
    () => buildMavenWavePlan(integrationShort, "C:\\work\\app"),
    /integration waves must contain 2-10 entries/,
  );
});

test("Maven wave plan rejects bypass arguments and cross-lane ID collisions", () => {
  const bypass = validConfig();
  bypass.integrationWaves[0].mavenArgs = ["-DskipTests"];
  assert.throws(
    () => buildMavenWavePlan(bypass, "C:\\work\\app"),
    /forbidden test bypass/,
  );

  const collision = validConfig();
  collision.integrationWaves[0].id = collision.unitWaves[0].id;
  assert.throws(
    () => buildMavenWavePlan(collision, "C:\\work\\app"),
    /globally unique/,
  );
});

test("Maven wave plan supports disabled lanes", () => {
  const config = validConfig();
  config.createUnitTests = false;
  delete config.unitWaves;
  const plan = buildMavenWavePlan(config, "C:\\work\\app");

  assert.equal(
    plan.steps.filter(
      (step) => step.phase === "wave" && step.lane === "unit",
    ).length,
    0,
  );
  assert.equal(
    plan.steps.filter(
      (step) => step.phase === "wave" && step.lane === "integration",
    ).length,
    2,
  );
});

test("process runner executes PATH commands including Windows command shims", () => {
  assert.equal(runProcessCommand, sharedProcessCommand);
  const result = runProcessCommand(process.cwd(), "node", [
    "-e",
    "process.stdout.write('ok')",
  ]);

  assert.equal(result.status, 0);
  assert.equal(result.stdout, "ok");
});

test("live executor captures fresh reports and validates compiled inputs", async () => {
  const root = await mkdtemp(path.join(process.cwd(), ".maven-wave-executor-"));
  try {
    await mkdir(path.join(root, "src", "main"), { recursive: true });
    await mkdir(path.join(root, "src", "test"), { recursive: true });
    await writeFile(path.join(root, "pom.xml"), javaPom());
    await writeFile(path.join(root, "src", "main", "App.java"), "class App {}");
    await writeFile(path.join(root, "src", "test", "AppTest.java"), "class AppTest {}");
    const nativeReports = path.join(root, "target", "surefire-reports");
    const coverage = path.join(root, "artifacts", "unit-1", "jacoco.exec");
    const command = [
      "const fs=require('fs');",
      `fs.mkdirSync(${JSON.stringify(nativeReports)},{recursive:true});`,
      `fs.mkdirSync(${JSON.stringify(path.dirname(coverage))},{recursive:true});`,
      `fs.writeFileSync(${JSON.stringify(path.join(nativeReports, "TEST-AppTest.xml"))},'<testsuite tests=\"1\"><testcase classname=\"AppTest\" name=\"works\"/></testsuite>');`,
      `fs.writeFileSync(${JSON.stringify(coverage)},'coverage');`,
    ].join("");
    const plan = {
      root,
      artifactRoot: "artifacts",
      steps: [
        {
          phase: "compile",
          id: "compile",
          command: process.execPath,
          args: ["-e", ""],
          sourceInputs: ["pom.xml", "src/main", "src/test"],
        },
        {
          phase: "wave",
          id: "unit-1",
          lane: "unit",
          waveNumber: 1,
          command: process.execPath,
          args: ["-e", command],
          reportDirectory: "artifacts/unit-1/reports",
          coverageExec: "artifacts/unit-1/jacoco.exec",
          outputs: [
            "artifacts/unit-1/jacoco.exec",
            "artifacts/unit-1/reports",
          ],
          requireTestXml: true,
          captureNativeReports: true,
          sourceReportDirectory: "target/surefire-reports",
          sourceInputs: ["pom.xml", "src/main", "src/test"],
        },
      ],
    };

    const runtime = await fixtureRuntime(root);
    const javaEvidence = await detectJavaTestRelease({
      root, effectivePomText: javaPom(), runtimeJavaMajor: 17,
    });
    const summary = await executeMavenWavePlan(plan, { runtime, javaEvidence });

    assert.equal(summary.testCompileCount, 1);
    assert.match(summary.compiledInputDigest, /^[a-f0-9]{64}$/);
    assert.equal(summary.waveResults.length, 1);
    assert.equal(summary.waveResults[0].status, "passed");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

function validConfig() {
  return {
    runId: "wave-test-run",
    actorId: "primary",
    runtimeDirectory: ".private-runtime",
    dockerEnvironment: dockerContext(),
    jacocoVersion: "0.8.13",
    surefireVersion: "3.5.5",
    createUnitTests: true,
    createIntegrationTests: true,
    unitWaves: waves("unit"),
    integrationWaves: waves("integration"),
    finalHtmlDirectory:
      ".github/migrate-oracle-app/conversions/coverage/html",
    prePackageChecks: [
      {
        id: "security-shim-classification",
        path: "src/main/java/com/example/TemporaryShim.java",
        expect: "absent",
      },
    ],
    archives: [
      {
        id: "war",
        path: "target/app.war",
        required: ["WEB-INF/lib/postgresql-*.jar"],
        forbidden: ["WEB-INF/classes/com/example/TemporaryShim.class"],
      },
      {
        id: "zip",
        path: "target/app.zip",
        required: ["server/resources/lib/postgresql.jar"],
      },
    ],
  };
}

function waves(lane) {
  return Array.from({ length: 2 }, (_, index) => ({
    id: `${lane}-${index + 1}`,
    wave: index + 1,
    runner: "surefire",
    tests: [`com.example.${lane}.Wave${index + 1}Test`],
  }));
}

test("last validation waves can serve as the only complete suites without duplicate launches", async () => {
  const config = validConfig();
  config.integrationWaves = config.integrationWaves.map((wave) => ({
    ...wave,
    runner: "failsafe",
  }));
  for (const lane of ["unit", "integration"]) {
    config[`${lane}Waves`][1] = {
      ...config[`${lane}Waves`][1],
      kind: "validation",
      fullSuite: true,
      tests: [],
    };
  }
  const plan = buildMavenWavePlan(config, "C:\\work\\app");
  const summary = await executeMavenWavePlan(plan, { dryRun: true });
  assert.equal(plan.steps.filter((step) => step.phase === "wave").length, 2);
  assert.equal(plan.steps.filter((step) => step.phase === "full-suite").length, 2);
  assert.equal(summary.fullSuiteGenerationCount, 1);
  assert.equal(summary.fullSuiteRunnerExecutions, 2);
  assert.equal(new Set(plan.steps.filter((step) => step.coverageExec).map((step) => step.coverageExec)).size, 4);
});

test("optional waves require specific additional coverage value and remain capped at ten", () => {
  const config = validConfig();
  config.unitWaves.push({
    id: "unit-3",
    wave: 3,
    runner: "surefire",
    tests: ["com.example.ErrorPathTest"],
  });
  assert.throws(() => buildMavenWavePlan(config, "C:\\work\\app"), /optional waves require/);
  config.unitWaves[2].continuationEvidence = {
    uncoveredBehavior: "Repository.rollback after a write failure",
    expectedNewCoveredLines: 2,
    expectedNewCoveredBranches: 1,
    reason: "The first two waves leave a high-risk rollback branch untested.",
  };
  assert.equal(buildMavenWavePlan(config, "C:\\work\\app").steps.filter((step) => step.phase === "wave").length, 5);
  config.unitWaves[2].continuationEvidence.expectedNewCoveredLines = 0;
  config.unitWaves[2].continuationEvidence.expectedNewCoveredBranches = 0;
  assert.throws(() => buildMavenWavePlan(config, "C:\\work\\app"), /optional waves require/);
  config.unitWaves = Array.from({ length: 11 }, (_, index) => ({
    id: `unit-${index + 1}`,
    wave: index + 1,
    tests: ["com.example.UnitTest"],
  }));
  assert.throws(() => buildMavenWavePlan(config, "C:\\work\\app"), /2-10 entries/);
});

test("wave arguments cannot reintroduce clean, compile, or dependency setup", () => {
  for (const goal of ["clean", "test", "package", "test-compile", "dependency:go-offline", "dependency:get"]) {
    const config = validConfig();
    config.unitWaves[0].mavenArgs = [goal];
    assert.throws(() => buildMavenWavePlan(config, "C:\\work\\app"), /repeated build\/dependency lifecycle goals/);
  }
});

test("shared PostgreSQL verifies run ownership, readiness, and a private endpoint without starting containers", async () => {
  const root = await executorFixture();
  try {
    const runtime = await fixtureRuntime(root);
    const context = dockerContext();
    const commands = [];
    const runner = (_root, command, args) => {
      commands.push([command, ...args]);
      return {
        status: 0,
        stderr: "",
        stdout: args[0] === "inspect" ? JSON.stringify([dockerInspection()]) : "",
      };
    };
    const environment = await resolveSharedPostgres(root, context, runner, { runtime });
    assert.equal(environment.env.MIGRATION_TEST_PG_JDBC_URL, "jdbc:postgresql://127.0.0.1:55432/migration_test");
    assert.equal(environment.env.MIGRATION_TEST_PG_PASSWORD, "synthetic-test-password");
    assert.deepEqual(commands.map((command) => command[1]), ["inspect", "exec"]);

    for (const mutate of [
      (container) => { container.Config.Labels["migrate-oracle-app.run-id"] = "another-run"; },
      (container) => { container.Image = "another-image"; },
      (container) => { container.State.Running = false; },
      (container) => { container.NetworkSettings.Ports["5432/tcp"][0].HostIp = "0.0.0.0"; },
    ]) {
      const container = dockerInspection();
      mutate(container);
      await assert.rejects(resolveSharedPostgres(root, context, (_root, _command, args) => ({
        status: 0,
        stderr: "",
        stdout: args[0] === "inspect" ? JSON.stringify([container]) : "",
      }), { runtime }));
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("same-run preparation reuses Maven setup and recompiles only changed inputs", async () => {
  const root = await executorFixture();
  try {
    const config = executionConfig();
    const calls = [];
    const firstPlan = buildMavenWavePlan(config, root);
    const first = await executeMavenWavePlan(firstPlan, {
      commandRunner: fakeExecutor(firstPlan, calls),
    });
    assert.equal(first.testCompileCount, 1);
    assert.equal(first.dependencyWarmupCount, 1);
    assert.equal(first.javaPreflight.cacheHit, false);
    assert.ok(first.steps.every((step) => Number.isInteger(step.elapsedMs) && step.elapsedMs >= 0));

    config.preparedBuild = first.preparationProof;
    const secondPlan = buildMavenWavePlan(config, root);
    const second = await executeMavenWavePlan(secondPlan, {
      commandRunner: fakeExecutor(secondPlan, []),
    });
    assert.equal(second.testCompileCount, 0);
    assert.equal(second.dependencyWarmupCommandCount, 0);
    assert.equal(second.reusedDependencyWarmup, true);
    assert.equal(second.reusedTestCompile, true);
    assert.equal(second.javaPreflight.cacheHit, true);

    await writeFile(path.join(root, "src", "test", "AppTest.java"), "class AppTest { void newCase() {} }");
    config.preparedBuild = second.preparationProof;
    const thirdPlan = buildMavenWavePlan(config, root);
    const third = await executeMavenWavePlan(thirdPlan, {
      commandRunner: fakeExecutor(thirdPlan, []),
    });
    assert.equal(third.dependencyWarmupCommandCount, 0);
    assert.equal(third.testCompileCount, 1);
    assert.equal(third.javaPreflight.cacheHit, true);
    assert.equal(third.preparationInvalidations.length, 1);
    assert.notEqual(
      third.preparationInvalidations[0].beforeDigest,
      third.preparationInvalidations[0].afterDigest,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("finalization reuses verified full-suite evidence instead of rerunning tests", async () => {
  const root = await executorFixture();
  try {
    const config = executionConfig();
    const plan = buildMavenWavePlan(config, root);
    const summary = await executeMavenWavePlan(plan, {
      commandRunner: fakeExecutor(plan, []),
    });
    config.preparedBuild = summary.preparationProof;
    config.finalize = true;
    config.finalizeOnly = true;
    const finalPlan = buildMavenWavePlan(config, root);
    const calls = [];
    const final = await executeMavenWavePlan(finalPlan, {
      commandRunner: fakeExecutor(finalPlan, calls),
    });
    assert.equal(final.cleanPackageCount, 1);
    assert.equal(final.fullSuiteRunnerExecutions, 0);
    assert.equal(final.waveResults.length, 0);
    assert.equal(final.testCompileCount, 0);
    assert.equal(calls.length, 2);

    await writeFile(path.join(root, "src", "main", "App.java"), "class App { int changed; }");
    await assert.rejects(
      executeMavenWavePlan(buildMavenWavePlan(config, root), { commandRunner: fakeExecutor(finalPlan, []) }),
      /finalize-only requires unchanged/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("all integration test processes receive the same shared endpoint and no container setup commands", async () => {
  const root = await executorFixture();
  try {
    const config = executionConfig();
    config.createUnitTests = false;
    config.unitWaves = [];
    config.createIntegrationTests = true;
    config.integrationWaves = waves("integration");
    config.dockerEnvironment = dockerContext();
    const plan = buildMavenWavePlan(config, root);
    const calls = [];
    const execute = fakeExecutor(plan, calls);
    const endpoints = [];
    const summary = await executeMavenWavePlan(plan, {
      commandRunner: async (cwd, command, args, options) => {
        if (command !== "docker" && args.some((arg) => /^org\.apache\.maven\.plugins:maven-surefire-plugin:.*:test$/.test(arg))) {
          endpoints.push(options.env.MIGRATION_TEST_PG_JDBC_URL);
        }
        return execute(cwd, command, args, options);
      },
    });
    assert.equal(endpoints.length, 3);
    assert.equal(new Set(endpoints).size, 1);
    assert.equal(summary.dockerReuse.testProcessUses, 3);
    assert.equal(summary.dockerReuse.containerStartsByRunner, 0);
    assert.ok(calls.filter((call) => call.command === "docker").every(
      (call) => ["inspect", "exec"].includes(call.args[0]),
    ));
    assert.ok(!JSON.stringify(summary).includes("synthetic-test-password"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("real Maven and Docker execution refuse missing or child ownership before commands", async () => {
  const root = await executorFixture();
  try {
    const calls = [];
    for (const actorId of [undefined, "test-child"]) {
      const config = { ...executionConfig(), actorId };
      const plan = buildMavenWavePlan(config, root);
      await assert.rejects(
        executeMavenWavePlan(plan, { commandRunner: fakeExecutor(plan, calls) }),
        /primary/,
      );
    }
    const runtime = await fixtureRuntime(root);
    const plan = buildMavenWavePlan({ ...executionConfig(), actorId: "test-child" }, root);
    await assert.rejects(
      executeMavenWavePlan(plan, {
        runtime: { ...runtime, actorId: "test-child" },
        commandRunner: fakeExecutor(plan, calls),
      }),
      /Only the primary/,
    );
    await assert.rejects(
      resolveSharedPostgres(root, dockerContext(), () => { calls.push("docker"); }),
      /explicit runId and actorId/,
    );
    assert.equal(calls.length, 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("the workspace lease refuses overlapping runners and shared Docker inspection", async () => {
  const root = await executorFixture();
  let release;
  let executing;
  try {
    const runtime = await fixtureRuntime(root);
    const plan = buildMavenWavePlan(executionConfig(), root);
    let notify;
    const started = new Promise((resolve) => { notify = resolve; });
    const waiting = new Promise((resolve) => { release = resolve; });
    const fake = fakeExecutor(plan, []);
    executing = executeMavenWavePlan(plan, {
      runtime,
      commandRunner: async (...args) => {
        if (args[2][0] === "-version") {
          notify();
          await waiting;
        }
        return fake(...args);
      },
    });
    await started;
    const secondCalls = [];
    await assert.rejects(
      executeMavenWavePlan(plan, { commandRunner: fakeExecutor(plan, secondCalls) }),
      /execution lease is already held/,
    );
    await assert.rejects(
      resolveSharedPostgres(root, dockerContext(), () => { secondCalls.push("docker"); }, { runtime }),
      /execution lease is already held/,
    );
    assert.equal(secondCalls.length, 0);
    release();
    const summary = await executing;
    assert.equal(summary.executionOwner.actorId, "primary");
    await withPrimaryExecution(runtime, "lease-released", async () => {});
  } finally {
    release?.();
    await executing?.catch(() => {});
    await rm(root, { recursive: true, force: true });
  }
});

test("runner receipts and concise checkpoints cover preflight, preparation, waves, and finalization", async () => {
  const root = await executorFixture();
  try {
    const config = executionConfig();
    config.createIntegrationTests = true;
    config.integrationWaves = waves("integration");
    config.dockerEnvironment = dockerContext();
    config.finalize = true;
    const plan = buildMavenWavePlan(config, root);
    const calls = [];
    const fake = fakeExecutor(plan, calls);
    const summary = await executeMavenWavePlan(plan, {
      commandRunner: async (...args) => {
        const lease = JSON.parse(await readFile(path.join(root, ".private-runtime", "execution.lock"), "utf8"));
        assert.equal(lease.actorId, "primary");
        assert.equal(lease.runId, config.runId);
        assert.equal(lease.kind, "maven-wave-runner");
        return fake(...args);
      },
      summaryPath: path.join(root, "runner-summary.json"),
    });
    assert.equal(summary.executionOwner.actorId, "primary");
    assert.equal(summary.executionOwner.runId, config.runId);
    assert.match(summary.executionOwner.leaseDigest, /^[a-f0-9]{64}$/);
    assert.equal(summary.javaPreflight.status, "passed");
    assert.equal(summary.javaPreflight.evidence.modules[0].testRelease, 11);
    assert.equal(summary.javaPreflight.files.length, 1);
    assert.equal(summary.cleanPackageCount, 1);
    const runtime = await fixtureRuntime(root);
    const replayed = await replayMigrationState(runtime);
    const evidence = replayed.conversion.state.validation.mavenWaveRunner;
    assert.deepEqual(evidence.executionOwner, summary.executionOwner);
    assert.equal(evidence.javaPreflight.evidenceDigest, summary.javaPreflight.evidence.inputDigest);
    assert.equal(evidence.javaPreflight.validatedFileCount, 1);
    assert.equal(evidence.cleanPackageCount, 1);
    assert.equal(evidence.waveCount, 4);
    assert.equal(replayed.sequence, summary.checkpoint.sequence);
    assert.equal(replayed.journalDigest, summary.checkpoint.digest);
    assert.ok(!Object.hasOwn(evidence, "steps"));
    assert.ok(!JSON.stringify(evidence).includes("synthetic-test-password"));
    assert.deepEqual(JSON.parse(await readFile(path.join(root, "runner-summary.json"), "utf8")), summary);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

for (const explicitInputs of [false, true]) {
  test(`SDK pre-generation and Maven execution share one Java snapshot with ${explicitInputs ? "explicit" : "automatic"} inputs`, async () => {
    const root = await executorFixture();
    try {
      const runtime = await fixtureRuntime(root);
      const config = executionConfig();
      config.mavenExecutable = "fixture-mvn";
      config.javaExecutable = "fixture-java";
      config.mavenArgs = ["-Pfixture", "-Dfixture.enabled=true"];
      if (explicitInputs) {
        config.javaPreflight = {
          buildInputs: ["pom.xml"],
          externalInputDigest: "d".repeat(64),
          testSourceRoots: ["src\\test"],
          files: ["src\\test\\AppTest.java"],
          outputDirectory: "fixture-java-preflight",
        };
      }
      const plan = buildMavenWavePlan(config, root);
      const calls = [];
      const commandRunner = fakeExecutor(plan, calls);
      const snapshot = await withPrimaryExecution(runtime, "sdk-java-pre-generation", () =>
        prepareJavaTestGeneration(runtime, {
          mavenExecutable: config.mavenExecutable,
          javaExecutable: config.javaExecutable,
          mavenArgs: config.mavenArgs,
          inputs: config.javaPreflight?.buildInputs,
          externalInputDigest: config.javaPreflight?.externalInputDigest,
          outputDirectory: explicitInputs
            ? path.join(runtime.runDirectory, config.javaPreflight.outputDirectory)
            : undefined,
          files: ["src\\test\\AppTest.java"],
          commandRunner,
        }));
      assert.equal(snapshot.cacheHit, false);
      assert.match(snapshot.contextDigest, /^[a-f0-9]{64}$/);
      assert.equal(calls.filter((call) => call.args.includes("help:effective-pom")).length, 1);
      assert.equal(snapshot.validatedFiles.length, 1);
      await writeFile(path.join(root, "src", "test", "GeneratedTest.java"), "class GeneratedTest { void works() {} }");
      const summary = await executeMavenWavePlan(plan, { runtime, commandRunner });
      assert.equal(summary.javaPreflight.cacheHit, true);
      assert.equal(summary.javaPreflight.cacheStatus, "hit");
      assert.deepEqual(summary.javaPreflight.evidence, snapshot.evidence);
      assert.equal(summary.javaPreflight.manifestInputDigest, snapshot.inputDigest);
      assert.equal(summary.javaPreflight.contextDigest, snapshot.contextDigest);
      assert.equal(summary.javaPreflight.files.length, 2);
      assert.equal(summary.executionOwner.actorId, "primary");
      assert.equal(summary.testCompileCount, 1);
      assert.equal(calls.filter((call) => call.args.includes("help:effective-pom")).length, 1);
      assert.equal(calls.filter((call) => call.args[0] === "-version").length, 1);
      assert.equal((await readdir(path.join(runtime.directory, "cache"))).length, 1);
      if (explicitInputs) {
        assert.ok((await lstat(path.join(runtime.runDirectory, config.javaPreflight.outputDirectory))).isDirectory());
      }
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
}

test("shared Java build fingerprints include flag inputs, external settings/toolchains, and JDK contents without executing commands", async () => {
  const root = await executorFixture();
  const externalRoot = await executorFixture();
  const names = ["MAVEN_USER_HOME", "MAVEN_HOME", "M2_HOME", "JAVA_HOME", "JDK_HOME", "JRE_HOME"];
  const previous = new Map(names.map((name) => [name, process.env[name]]));
  try {
    process.env.MAVEN_USER_HOME = path.join(externalRoot, "m2");
    process.env.MAVEN_HOME = path.join(externalRoot, "maven");
    process.env.JAVA_HOME = path.join(externalRoot, "jdk");
    for (const name of ["M2_HOME", "JDK_HOME", "JRE_HOME"]) delete process.env[name];
    const mavenExecutable = path.join(externalRoot, "maven", "bin", "mvn.cmd");
    const javaExecutable = path.join(externalRoot, "jdk", "bin", "java.exe");
    const localSettings = path.join(root, "local-settings.xml");
    const outsideSettings = path.join(externalRoot, "settings.xml");
    const outsideToolchains = path.join(externalRoot, "toolchains.xml");
    const outsideInputs = [
      outsideSettings, outsideToolchains,
      path.join(process.env.MAVEN_USER_HOME, "settings.xml"),
      path.join(process.env.MAVEN_USER_HOME, "toolchains.xml"),
      path.join(process.env.MAVEN_HOME, "conf", "settings.xml"),
      path.join(process.env.MAVEN_HOME, "conf", "toolchains.xml"),
      path.join(process.env.JAVA_HOME, "release"),
      mavenExecutable, javaExecutable,
    ];
    await writeFile(localSettings, "<settings />");
    for (const [index, file] of outsideInputs.entries()) {
      await mkdir(path.dirname(file), { recursive: true });
      await writeFile(file, `PRIVATE_CONTEXT_MARKER_${index}`);
    }
    const options = {
      root, mavenExecutable, javaExecutable,
      mavenArgs: [
        "-fae", "-ff", "--file=.", `-gs${localSettings}`,
        "-s", outsideSettings, `--toolchains=${outsideToolchains}`,
      ],
      externalInputDigest: "a".repeat(64),
      commandRunner: () => { throw new Error("fingerprinting must not run Java or Maven"); },
    };
    const first = await fingerprintJavaBuildContext(options);
    assert.match(first.contextDigest, /^[a-f0-9]{64}$/);
    assert.match(first.externalInputDigest, /^[a-f0-9]{64}$/);
    assert.deepEqual(first.buildInputs, ["local-settings.xml", "pom.xml"]);
    assert.deepEqual(await fingerprintJavaBuildContext(options), first);
    assert.ok(!JSON.stringify(first).includes("PRIVATE_CONTEXT_MARKER"));
    assert.ok(!JSON.stringify(first).includes(externalRoot));
    let before = first;
    for (const file of outsideInputs) {
      await writeFile(file, `${await readFile(file, "utf8")}-changed`);
      const changed = await fingerprintJavaBuildContext(options);
      assert.notEqual(changed.contextDigest, before.contextDigest);
      assert.notEqual(changed.externalInputDigest, before.externalInputDigest);
      assert.deepEqual(changed.buildInputs, before.buildInputs);
      before = changed;
    }
    const suppliedChanged = await fingerprintJavaBuildContext({
      ...options, externalInputDigest: "b".repeat(64),
    });
    assert.notEqual(suppliedChanged.contextDigest, before.contextDigest);
    assert.notEqual(suppliedChanged.externalInputDigest, before.externalInputDigest);
    await writeFile(localSettings, "<settings><!-- profile changed --></settings>");
    const localChanged = await fingerprintJavaBuildContext(options);
    assert.notEqual(localChanged.contextDigest, before.contextDigest);
    assert.notEqual((await fingerprintJavaBuildContext({
      ...options, runtimeJavaMajor: 21,
    })).contextDigest, localChanged.contextDigest);
    assert.notEqual((await fingerprintJavaBuildContext({
      ...options, effectivePomText: javaPom(8),
    })).contextDigest, localChanged.contextDigest);
    await assert.rejects(
      fingerprintJavaBuildContext({ ...options, mavenArgs: ["-s"] }),
      /Maven build file option/,
    );
  } finally {
    for (const [name, value] of previous) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
    await rm(root, { recursive: true, force: true });
    await rm(externalRoot, { recursive: true, force: true });
  }
});

test("Java release cache invalidates changed POM, profile, toolchain, and JDK environment inputs", async () => {
  const root = await executorFixture();
  const originalJavaOptions = process.env.JAVA_TOOL_OPTIONS;
  try {
    const config = executionConfig();
    const run = async () => {
      const plan = buildMavenWavePlan(config, root);
      const calls = [];
      const summary = await executeMavenWavePlan(plan, { commandRunner: fakeExecutor(plan, calls) });
      return { summary, preflightCalls: calls.filter((call) =>
        call.args[0] === "-version" || call.args.includes("help:effective-pom"),
      ) };
    };
    const first = await run();
    assert.equal(first.summary.javaPreflight.cacheHit, false);
    assert.equal(first.preflightCalls.length, 2);
    const unchanged = await run();
    assert.equal(unchanged.summary.javaPreflight.cacheHit, true);
    assert.equal(unchanged.preflightCalls.length, 0);

    await writeFile(path.join(root, "pom.xml"), javaPom(8));
    const changedPom = await run();
    assert.equal(changedPom.summary.javaPreflight.cacheHit, false);
    assert.equal(changedPom.summary.javaPreflight.evidence.modules[0].testRelease, 8);
    assert.notEqual(changedPom.summary.javaPreflight.manifestInputDigest, first.summary.javaPreflight.manifestInputDigest);

    config.mavenArgs = ["-Pcompat"];
    const changedProfile = await run();
    assert.equal(changedProfile.summary.javaPreflight.cacheHit, false);
    assert.equal(changedProfile.preflightCalls.length, 2);

    await writeFile(path.join(root, "toolchains.xml"), "<toolchains />");
    config.mavenArgs = ["-Pcompat", "-t", "toolchains.xml"];
    assert.equal((await run()).summary.javaPreflight.cacheHit, false);
    assert.equal((await run()).summary.javaPreflight.cacheHit, true);
    await writeFile(path.join(root, "toolchains.xml"), "<toolchains><!-- changed JDK --></toolchains>");
    assert.equal((await run()).summary.javaPreflight.cacheHit, false);

    await mkdir(path.join(root, ".mvn"));
    await writeFile(path.join(root, ".mvn", "jvm.config"), "-Xmx512m");
    assert.equal((await run()).summary.javaPreflight.cacheHit, false);
    await writeFile(path.join(root, ".mvn", "jvm.config"), "-Xmx768m");
    assert.equal((await run()).summary.javaPreflight.cacheHit, false);

    process.env.JAVA_TOOL_OPTIONS = "-Dfixture.java.runtime=changed";
    assert.equal((await run()).summary.javaPreflight.cacheHit, false);
    assert.equal((await run()).summary.javaPreflight.cacheHit, true);
    config.runId = "a-different-run";
    assert.equal((await run()).summary.javaPreflight.cacheHit, false);
  } finally {
    if (originalJavaOptions === undefined) delete process.env.JAVA_TOOL_OPTIONS;
    else process.env.JAVA_TOOL_OPTIONS = originalJavaOptions;
    await rm(root, { recursive: true, force: true });
  }
});

test("changed Maven/JDK configuration bytes invalidate both Java and prepared compilation", async () => {
  const root = await executorFixture();
  try {
    await mkdir(path.join(root, ".mvn"));
    await writeFile(path.join(root, ".mvn", "jvm.config"), "-Xmx512m");
    const config = executionConfig();
    const run = async () => {
      const plan = buildMavenWavePlan(config, root);
      const summary = await executeMavenWavePlan(plan, { commandRunner: fakeExecutor(plan, []) });
      config.preparedBuild = summary.preparationProof;
      return summary;
    };
    assert.equal((await run()).testCompileCount, 1);
    const reused = await run();
    assert.equal(reused.reusedTestCompile, true);
    assert.equal(reused.javaPreflight.cacheHit, true);
    await writeFile(path.join(root, ".mvn", "jvm.config"), "-Xmx768m");
    const invalidated = await run();
    assert.equal(invalidated.javaPreflight.cacheHit, false);
    assert.equal(invalidated.reusedTestCompile, false);
    assert.equal(invalidated.testCompileCount, 1);
    assert.equal(invalidated.dependencyWarmupCount, 1);
    assert.equal(invalidated.preparationInvalidations.length, 1);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("incompatible generated Java features fail before dependency warmup or compilation", async () => {
  const root = await executorFixture();
  try {
    await writeFile(path.join(root, "src", "test", "AppTest.java"), `class AppTest {
      String query = """
          select id from app_task
          """;
    }`);
    const plan = buildMavenWavePlan(executionConfig(), root);
    const calls = [];
    await assert.rejects(
      executeMavenWavePlan(plan, { commandRunner: fakeExecutor(plan, calls) }),
      /textBlocks requires Java 15.*test release is 11/,
    );
    assert.ok(!calls.some((call) => call.args.includes("test-compile")));
    assert.ok(!calls.some((call) => call.args.includes("dependency:go-offline")));
    const replayed = await replayMigrationState(await fixtureRuntime(root));
    assert.equal(replayed.conversion.state.validation.mavenWaveRunner.status, "failed");
    assert.equal(replayed.conversion.state.validation.mavenWaveRunner.testCompileCount, 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("cached Java evidence does not exempt new or custom-layout test sources from the gate", async () => {
  const root = await executorFixture();
  try {
    const config = executionConfig();
    config.javaPreflight = { testSourceRoots: ["custom-tests"] };
    let plan = buildMavenWavePlan(config, root);
    await executeMavenWavePlan(plan, { commandRunner: fakeExecutor(plan, []) });
    await mkdir(path.join(root, "custom-tests"));
    await writeFile(path.join(root, "custom-tests", "NewTest.java"), "record NewTest(int id) {}");
    plan = buildMavenWavePlan(config, root);
    const calls = [];
    await assert.rejects(
      executeMavenWavePlan(plan, { commandRunner: fakeExecutor(plan, calls) }),
      /records requires Java 16.*test release is 11/,
    );
    assert.equal(calls.length, 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Java gate rechecks files after warmup before using compilation", async () => {
  const root = await executorFixture();
  try {
    const plan = buildMavenWavePlan(executionConfig(), root);
    const calls = [];
    const fake = fakeExecutor(plan, calls);
    await assert.rejects(
      executeMavenWavePlan(plan, {
        commandRunner: async (...args) => {
          if (args[2].includes("dependency:go-offline")) {
            await writeFile(path.join(root, "src", "test", "AppTest.java"), "record AppTest(int id) {}");
          }
          return fake(...args);
        },
      }),
      /records requires Java 16/,
    );
    assert.ok(!calls.some((call) => call.args.includes("test-compile")));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("final packaging refuses source changes after the validated full suite", async () => {
  const root = await executorFixture();
  try {
    const config = executionConfig();
    config.finalize = true;
    const plan = buildMavenWavePlan(config, root);
    const calls = [];
    const fake = fakeExecutor(plan, calls);
    await assert.rejects(
      executeMavenWavePlan(plan, {
        commandRunner: async (...args) => {
          if (args[2].includes("report")) {
            await writeFile(path.join(root, "src", "main", "App.java"), "class App { int changed; }");
          }
          return fake(...args);
        },
      }),
      /final-clean-package cannot use stale compiled classes/,
    );
    assert.ok(!calls.some((call) => call.args.includes("package")));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("production plans cannot disable Java gates or inject fixture-only release evidence", async () => {
  assert.throws(
    () => buildMavenWavePlan({ ...executionConfig(), javaPreflight: { enabled: false } }, process.cwd()),
    /Java gate cannot be disabled/,
  );
  const root = await executorFixture();
  try {
    const plan = buildMavenWavePlan(executionConfig(), root);
    const javaEvidence = await detectJavaTestRelease({
      root, effectivePomText: javaPom(), runtimeJavaMajor: 17,
    });
    const calls = [];
    await assert.rejects(
      executeMavenWavePlan(plan, { javaEvidence, commandRunner: fakeExecutor(plan, calls) }),
      /only for explicit raw fixture plans/,
    );
    const raw = { root, artifactRoot: "artifacts", steps: [] };
    await assert.rejects(executeMavenWavePlan(raw), /explicit runtime and Java release evidence/);
    await assert.rejects(
      executeMavenWavePlan(raw, { javaEvidence }),
      /explicit runtime and Java release evidence/,
    );
    assert.equal(calls.length, 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("dry-run CLI writes no runtime, outputs, or requested summary file", async () => {
  const root = await executorFixture();
  try {
    const configPath = path.join(root, "wave-config.json");
    const summaryPath = path.join(root, "dry-summary.json");
    await writeFile(configPath, JSON.stringify(executionConfig()));
    const result = spawnSync(process.execPath, [
      path.resolve("skills/migrate-oracle-app/scripts", "run-maven-waves.mjs"), root, configPath,
      "--dry-run", "--summary", summaryPath,
    ], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    const summary = JSON.parse(result.stdout);
    assert.equal(summary.dryRun, true);
    assert.equal(summary.executionOwner, null);
    assert.equal(summary.javaPreflight, null);
    for (const file of [summaryPath, path.join(root, ".private-runtime", "execution.lock")]) {
      await assert.rejects(readFile(file), { code: "ENOENT" });
    }
    for (const directory of [
      path.join(root, ".private-runtime"),
      path.join(root, ".github", "migrate-oracle-app", "conversions", "coverage", "waves"),
    ]) {
      await assert.rejects(lstat(directory), { code: "ENOENT" });
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

function dockerContext() {
  return {
    runId: "wave-test-run",
    containerId: "a".repeat(64),
    imageId: `sha256:${"b".repeat(64)}`,
    configurationDigest: "c".repeat(64),
    database: "migration_test",
  };
}

function dockerInspection() {
  const context = dockerContext();
  return {
    Id: context.containerId,
    Image: context.imageId,
    State: { Running: true },
    Config: {
      Labels: {
        "migrate-oracle-app.run-id": context.runId,
        "migrate-oracle-app.configuration-digest": context.configurationDigest,
      },
      Env: ["POSTGRES_DB=migration_test", "POSTGRES_USER=migration_test", "POSTGRES_PASSWORD=synthetic-test-password"],
    },
    NetworkSettings: { Ports: { "5432/tcp": [{ HostIp: "127.0.0.1", HostPort: "55432" }] } },
  };
}

async function executorFixture() {
  const root = await mkdtemp(path.join(process.cwd(), ".maven-preparation-test-"));
  await mkdir(path.join(root, "src", "main"), { recursive: true });
  await mkdir(path.join(root, "src", "test"), { recursive: true });
  await writeFile(path.join(root, "pom.xml"), javaPom());
  await writeFile(path.join(root, "src", "main", "App.java"), "class App {}");
  await writeFile(path.join(root, "src", "test", "AppTest.java"), "class AppTest {}");
  return root;
}

function executionConfig() {
  const config = validConfig();
  config.createIntegrationTests = false;
  config.integrationWaves = [];
  delete config.dockerEnvironment;
  delete config.finalHtmlDirectory;
  config.finalize = false;
  config.prePackageChecks = [];
  config.archives = [];
  return config;
}

function fakeExecutor(plan, calls) {
  return async (_cwd, command, args) => {
    calls.push({ command, args });
    if (command === (plan.javaPreflight?.javaExecutable ?? "java") && args[0] === "-version") {
      return { status: 0, stdout: "", stderr: 'openjdk version "17.0.12"' };
    }
    if (args.includes("help:effective-pom")) {
      const output = args.find((argument) => argument.startsWith("-Doutput=")).slice("-Doutput=".length);
      await writeFile(output, await readFile(path.join(plan.root, "pom.xml"), "utf8"));
      return { status: 0, stdout: "", stderr: "" };
    }
    if (command === "docker") {
      return { status: 0, stderr: "", stdout: args[0] === "inspect" ? JSON.stringify([dockerInspection()]) : "" };
    }
    const step = plan.steps.find((entry) => JSON.stringify(entry.args) === JSON.stringify(args));
    if (!step) throw new Error("unknown test command");
    if (step.phase === "compile") {
      for (const directory of ["target/classes", "target/test-classes"]) {
        const target = path.join(plan.root, ...directory.split("/"));
        await mkdir(target, { recursive: true });
        await writeFile(path.join(target, "App.class"), "compiled");
      }
    }
    if (step.captureNativeReports) {
      const reports = path.join(plan.root, ...step.sourceReportDirectory.split("/"));
      await mkdir(reports, { recursive: true });
      await writeFile(path.join(reports, "TEST-AppTest.xml"), '<testsuite tests="1"><testcase classname="AppTest" name="works"/></testsuite>');
      const execution = path.join(plan.root, ...step.coverageExec.split("/"));
      await mkdir(path.dirname(execution), { recursive: true });
      await writeFile(execution, "execution");
    } else {
      for (const output of step.outputs ?? []) {
        const target = path.join(plan.root, ...output.split("/"));
        await mkdir(path.dirname(target), { recursive: true });
        await writeFile(target, "coverage");
      }
    }
    return { status: 0, stdout: "", stderr: "" };
  };
}

function javaPom(release = 11) {
  return `<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId><artifactId>app</artifactId><version>1.0</version>
  <properties><maven.compiler.release>${release}</maven.compiler.release></properties>
</project>`;
}

function fixtureRuntime(root) {
  return openMigrationRuntime({
    root, runId: "wave-test-run", actorId: "primary",
    runtimeDirectory: path.join(root, ".private-runtime"), initialize: true,
  });
}
