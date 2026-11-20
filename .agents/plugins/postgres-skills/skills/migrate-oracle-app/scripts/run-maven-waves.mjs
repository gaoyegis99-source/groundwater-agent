import { createHash } from "node:crypto";
import {
  access,
  cp,
  lstat,
  mkdir,
  readFile,
  readdir,
  realpath,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { runProcessCommand } from "./process-command.mjs";
import {
  checkpointMigration,
  digestJson,
  openMigrationRuntime,
  prepareJavaTestGeneration,
  withPrimaryExecution,
} from "./migration-runtime.mjs";
import { validateJavaTestFiles } from "./java-test-preflight.mjs";

export { runProcessCommand } from "./process-command.mjs";

export const MINIMUM_TEST_WAVES = 2;
export const MAXIMUM_TEST_WAVES = 10;

export function validateOptionalWaveValue(evidence) {
  if (
    typeof evidence?.uncoveredBehavior !== "string" ||
    !evidence.uncoveredBehavior.trim() ||
    typeof evidence.reason !== "string" ||
    !evidence.reason.trim() ||
    !Number.isInteger(evidence.expectedNewCoveredLines) ||
    evidence.expectedNewCoveredLines < 0 ||
    !Number.isInteger(evidence.expectedNewCoveredBranches) ||
    evidence.expectedNewCoveredBranches < 0 ||
    evidence.expectedNewCoveredLines + evidence.expectedNewCoveredBranches === 0
  ) {
    throw new TypeError("optional waves require a specific uncovered behavior, expected new line/branch coverage, and a value-based reason");
  }
}

export function buildMavenWavePlan(config, codebase) {
  validateConfig(config);
  const root = path.resolve(codebase);
  const maven = config.mavenExecutable ?? "mvn";
  const java = config.javaExecutable ?? "java";
  const common = ["-B", ...(config.mavenArgs ?? [])];
  const offline = ["-B", "-o", ...(config.mavenArgs ?? [])];
  const jacocoVersion = config.jacocoVersion;
  const surefireVersion = config.surefireVersion;
  const failsafeVersion = config.failsafeVersion ?? surefireVersion;
  const dependencyPluginVersion =
    config.dependencyPluginVersion ?? "3.8.1";
  const artifactRoot = normalizeRelative(
    config.artifactRoot ??
      ".github/migrate-oracle-app/conversions/coverage/waves",
  );
  const cliJar =
    config.jacocoCliJar ??
    path.join(
      config.mavenRepository ?? path.join(os.homedir(), ".m2", "repository"),
      "org",
      "jacoco",
      "org.jacoco.cli",
      jacocoVersion,
      `org.jacoco.cli-${jacocoVersion}-nodeps.jar`,
    );
  const steps = [];
  const sourceInputs = config.compiledInputs ?? ["pom.xml", "src/main", "src/test"];
  const preparation = config.runId
    ? {
        runId: config.runId,
        dependencyInputs: config.dependencyInputs ?? ["pom.xml"],
        sourceInputs,
        outputInputs: config.compiledOutputs ?? [
          config.classFiles ?? "target/classes",
          config.testClassFiles ?? "target/test-classes",
        ],
        settings: {
          maven,
          java,
          mavenArgs: config.mavenArgs ?? [],
          mavenRepository: config.mavenRepository ?? path.join(os.homedir(), ".m2", "repository"),
          jacocoVersion,
          surefireVersion,
          failsafeVersion,
          dependencyPluginVersion,
          warmupArtifacts: config.warmupArtifacts ?? [],
          buildEnvironmentDigest: environmentDigest(),
        },
        testConfigurationDigest: createHash("sha256").update(JSON.stringify({
          unitWaves: config.unitWaves ?? [],
          integrationWaves: config.integrationWaves ?? [],
          fullSuiteRuns: config.fullSuiteRuns ?? null,
          dockerEnvironment: config.dockerEnvironment ?? null,
        })).digest("hex"),
        previous: config.preparedBuild ?? null,
      }
    : null;

  steps.push({
    phase: "dependency-warmup",
    id: "maven-go-offline",
    command: maven,
    args: [...common, "dependency:go-offline"],
  });
  const usesFailsafe = [
    ...(config.integrationWaves ?? []),
    ...(config.fullSuiteRuns ?? []),
  ].some((wave) => wave.runner === "failsafe");
  const warmupArtifacts = [
    `org.jacoco:org.jacoco.cli:${jacocoVersion}:jar:nodeps`,
    `org.jacoco:org.jacoco.agent:${jacocoVersion}:jar:runtime`,
    `org.jacoco:jacoco-maven-plugin:${jacocoVersion}`,
    `org.apache.maven.plugins:maven-surefire-plugin:${surefireVersion}`,
    `org.apache.maven.surefire:surefire-junit-platform:${surefireVersion}`,
    ...(usesFailsafe
      ? [`org.apache.maven.plugins:maven-failsafe-plugin:${failsafeVersion}`]
      : []),
    ...(config.warmupArtifacts ?? []),
  ];
  for (const [index, artifact] of warmupArtifacts.entries()) {
    steps.push({
      phase: "dependency-warmup",
      id: `resolve-plugin-${index + 1}`,
      command: maven,
      args: [
        ...common,
        `${"org.apache.maven.plugins:maven-dependency-plugin"}:${dependencyPluginVersion}:get`,
        `-Dartifact=${artifact}`,
      ],
    });
  }
  steps.push({
    phase: "compile",
    id: "test-compile-once",
    command: maven,
    args: [...offline, "test-compile", "-DskipTests"],
    sourceInputs,
  });

  const waveSteps = [
    ...buildWaveSteps(
      "unit",
      config.unitWaves ?? [],
      config,
      artifactRoot,
      maven,
      offline,
      jacocoVersion,
      surefireVersion,
    ),
    ...buildWaveSteps(
      "integration",
      config.integrationWaves ?? [],
      config,
      artifactRoot,
      maven,
      offline,
      jacocoVersion,
      failsafeVersion,
    ),
  ];
  steps.push(...waveSteps.filter((step) => step.phase === "wave"));
  steps.push(...waveSteps.filter((step) => step.phase === "full-suite"));

  const fullSuiteRuns = configuredFullSuiteRuns(config);
  const creditedRunners = new Set(
    waveSteps.filter((step) => step.phase === "full-suite").map(
      (step) => fullSuiteKey({ runner: step.runner, mavenArgs: step.additionalMavenArgs }),
    ),
  );
  for (const run of fullSuiteRuns) {
    if (creditedRunners.has(fullSuiteKey(run))) continue;
    steps.push(
      buildTestStep({
        phase: "full-suite",
        lane: "full",
        wave: run,
        config,
        artifactRoot,
        maven,
        offline,
        jacocoVersion,
        runnerVersion:
          run.runner === "failsafe" ? failsafeVersion : surefireVersion,
      }),
    );
  }

  const execFiles = steps
    .filter((step) => step.coverageExec)
    .map((step) => step.coverageExec);
  const combinedExec = `${artifactRoot}/combined.exec`;
  const combinedXml = `${artifactRoot}/combined.xml`;
  steps.push({
    phase: "coverage-merge",
    id: "jacoco-merge",
    command: java,
    args: [
      "-jar",
      cliJar,
      "merge",
      ...execFiles,
      "--destfile",
      combinedExec,
    ],
    outputs: [combinedExec],
  });
  const reportArgs = [
    "-jar",
    cliJar,
    "report",
    combinedExec,
    "--classfiles",
    normalizeRelative(config.classFiles ?? "target/classes"),
    "--sourcefiles",
    normalizeRelative(config.sourceFiles ?? "src/main/java"),
    "--xml",
    combinedXml,
  ];
  if (config.finalHtmlDirectory && config.finalize !== false) {
    reportArgs.push("--html", normalizeRelative(config.finalHtmlDirectory));
  }
  steps.push({
    phase: "coverage-report",
    id: "jacoco-final-report",
    command: java,
    args: reportArgs,
    outputs: [combinedXml],
    htmlGenerated: Boolean(config.finalHtmlDirectory && config.finalize !== false),
  });

  for (const check of config.finalize === false ? [] : config.prePackageChecks ?? []) {
    steps.push({
      phase: "pre-package-check",
      id: check.id,
      internal: "file-check",
      check,
    });
  }
  if (config.finalize !== false) steps.push({
    phase: "package",
    id: "final-clean-package",
    command: maven,
    args: [
      ...common,
      "clean",
      "package",
      "-Dmaven.test.skip=true",
      ...(config.finalPackageArgs ?? []),
    ],
    sourceInputs,
  });
  for (const archive of config.finalize === false ? [] : config.archives ?? []) {
    steps.push({
      phase: "archive-inspection",
      id: `inspect-${archive.id}`,
      command: config.jarExecutable ?? "jar",
      args: ["tf", normalizeRelative(archive.path)],
      archive,
    });
  }
  if (config.finalizeOnly) {
    steps.splice(0, steps.length, ...steps.filter(
      (step) => ["coverage-report", "pre-package-check", "package", "archive-inspection"].includes(step.phase),
    ));
  }
  return {
    root,
    cliJar,
    artifactRoot,
    steps,
    preparation,
    execution: {
      runId: config.runId,
      actorId: config.actorId,
      runtimeDirectory: config.runtimeDirectory,
    },
    runtimePhase: config.runtimePhase ?? "conversion",
    javaPreflight: {
      mavenExecutable: maven,
      javaExecutable: java,
      mavenArgs: config.mavenArgs ?? [],
      buildInputs: config.javaPreflight?.buildInputs ?? [],
      testSourceRoots: config.javaPreflight?.testSourceRoots ?? [],
      files: config.javaPreflight?.files ?? [],
      outputDirectory: config.javaPreflight?.outputDirectory ?? "java-preflight",
      externalInputDigest: config.javaPreflight?.externalInputDigest ?? null,
    },
    dockerEnvironment: config.dockerEnvironment ?? null,
    finalizeOnly: config.finalizeOnly === true,
    coverageOutputs: [combinedExec, combinedXml],
  };
}

export async function executeMavenWavePlan(
  plan,
  {
    dryRun = false,
    commandRunner = runProcessCommand,
    runtime: injectedRuntime,
    javaEvidence,
    summaryPath,
  } = {},
) {
  const summary = {
    startedAt: new Date().toISOString(),
    completedAt: null,
    dryRun,
    testCompileCount: 0,
    cleanPackageCount: 0,
    dependencyWarmupCount: 0,
    dependencyWarmupCommandCount: 0,
    htmlGenerationCount: 0,
    compiledInputDigest: null,
    fullSuiteGenerationCount: 0,
    fullSuiteRunnerExecutions: 0,
    preparationInvalidations: [],
    reusedDependencyWarmup: false,
    reusedTestCompile: false,
    measurementStartedAt: null,
    dockerReuse: null,
    executionOwner: null,
    javaPreflight: null,
    waveResults: [],
    steps: [],
  };
  if (dryRun) {
    summary.steps = plan.steps;
    summary.testCompileCount = countSteps(plan, "compile");
    summary.cleanPackageCount = countSteps(plan, "package");
    summary.dependencyWarmupCount =
      countSteps(plan, "dependency-warmup") > 0 ? 1 : 0;
    summary.dependencyWarmupCommandCount = countSteps(
      plan,
      "dependency-warmup",
    );
    summary.htmlGenerationCount = plan.steps.filter(
      (step) => step.htmlGenerated,
    ).length;
    summary.fullSuiteGenerationCount =
      countSteps(plan, "full-suite") > 0 ? 1 : 0;
    summary.fullSuiteRunnerExecutions = countSteps(plan, "full-suite");
    summary.completedAt = new Date().toISOString();
    return summary;
  }

  if (!plan.javaPreflight && (!injectedRuntime || !javaEvidence)) {
    throw new Error("Raw fixture plans require explicit runtime and Java release evidence; production Java preflight cannot be bypassed");
  }
  const runtime = await executionRuntime(plan, injectedRuntime);
  return withPrimaryExecution(runtime, "maven-wave-runner", async (receipt) => {
    summary.executionOwner = receipt;
    try {
      await executeOwnedMavenWavePlan(plan, { summary, runtime, commandRunner, javaEvidence });
    } catch (error) {
      await checkpointMigration(runtime, {
        phase: plan.runtimePhase ?? "conversion",
        operations: [{
          op: "set",
          path: ["validation", "mavenWaveRunner"],
          value: {
            status: "failed",
            executionOwner: receipt,
            errorCode: typeof error.code === "string" ? error.code : "MAVEN_WAVE_RUN_FAILED",
            testCompileCount: summary.testCompileCount,
            cleanPackageCount: summary.cleanPackageCount,
          },
        }],
      });
      throw error;
    }
    const checkpoint = await checkpointMigration(runtime, {
      phase: plan.runtimePhase ?? "conversion",
      operations: [{
        op: "set",
        path: ["validation", "mavenWaveRunner"],
        value: runnerCheckpoint(summary),
      }],
    });
    summary.checkpoint = { sequence: checkpoint.sequence, digest: checkpoint.digest };
    if (summaryPath) {
      const absolute = path.resolve(summaryPath);
      await mkdir(path.dirname(absolute), { recursive: true });
      await writeFile(absolute, `${JSON.stringify(summary, null, 2)}\n`, "utf8");
    }
    return summary;
  });
}

async function executeOwnedMavenWavePlan(plan, { summary, runtime, commandRunner, javaEvidence }) {
  summary.javaPreflight = await preflightJava(plan, runtime, commandRunner, javaEvidence);
  await mkdir(path.join(plan.root, plan.artifactRoot), { recursive: true });
  const preparation = await inspectPreparation(plan, summary.javaPreflight);
  if (plan.finalizeOnly) {
    if (
      !preparation?.reuseCompile ||
      preparation.previous.fullSuitePassed !== true ||
      preparation.previous.testConfigurationDigest !== preparation.testConfigurationDigest ||
      preparation.previous.coverageOutputDigest !== await digestInputs(plan.root, plan.coverageOutputs)
    ) {
      throw new Error("finalize-only requires unchanged, verified build inputs/outputs and passed full-suite/coverage evidence");
    }
    summary.reusedDependencyWarmup = true;
    summary.reusedTestCompile = true;
    summary.compiledInputDigest = preparation.sourceDigest;
  }
  let compiledInputDigest = plan.finalizeOnly ? preparation.sourceDigest : null;
  let dockerRuntime = null;
  for (const step of plan.steps) {
    const startedEpoch = Date.now();
    const startedAt = new Date(startedEpoch).toISOString();
    if (["compile", "package"].includes(step.phase)) {
      await verifyJavaGate(plan, runtime, summary.javaPreflight, commandRunner);
    }
    if (
      (step.phase === "dependency-warmup" && preparation?.reuseWarmup) ||
      (step.phase === "compile" && preparation?.reuseCompile)
    ) {
      if (step.phase === "dependency-warmup") summary.reusedDependencyWarmup = true;
      if (step.phase === "compile") {
        compiledInputDigest = preparation.sourceDigest;
        summary.compiledInputDigest = compiledInputDigest;
        summary.reusedTestCompile = true;
      }
      summary.steps.push({
        id: step.id,
        phase: step.phase,
        startedAt,
        completedAt: new Date().toISOString(),
        elapsedMs: Date.now() - startedEpoch,
        status: "reused",
      });
      continue;
    }
    if (
      compiledInputDigest &&
      ["wave", "full-suite", "package"].includes(step.phase)
    ) {
      const currentDigest = await digestInputs(
        plan.root,
        step.sourceInputs,
      );
      if (currentDigest !== compiledInputDigest) {
        throw new Error(
          `${step.id} cannot use stale compiled classes; source/test/build inputs changed`,
        );
      }
    }
    if (
      plan.dockerEnvironment &&
      ["wave", "full-suite"].includes(step.phase) &&
      (step.lane === "integration" || step.phase === "full-suite")
    ) {
      const current = await inspectSharedPostgres(
        plan.root,
        plan.dockerEnvironment,
        commandRunner,
      );
      if (dockerRuntime && dockerRuntime.containerIdDigest !== current.containerIdDigest) {
        throw new Error("test processes must reuse the same run-owned PostgreSQL container");
      }
      dockerRuntime = current;
      summary.dockerReuse ??= {
        scope: "migration-run",
        containerIdDigest: current.containerIdDigest,
        imageId: current.imageId,
        ownershipVerified: true,
        readinessVerified: true,
        testProcessUses: 0,
        containerStartsByRunner: 0,
      };
      summary.dockerReuse.testProcessUses += 1;
    }
    const compileBefore = step.phase === "compile"
      ? await digestInputs(plan.root, step.sourceInputs)
      : null;
    await prepareStepOutputs(plan.root, step);
    let result;
    if (step.internal === "file-check") {
      result = await runFileCheck(plan.root, step.check);
    } else {
      result = await commandRunner(plan.root, step.command, step.args, {
        env: dockerRuntime?.env,
      });
    }
    if (result.status !== 0) {
      throw new Error(
        `${step.id} failed (${result.status}): ${result.stderr || result.stdout}`,
      );
    }
    if (step.captureNativeReports) {
      await captureNativeReports(plan.root, step);
    }
    if (step.archive) validateArchiveOutput(result.stdout, step.archive);
    await validateStepOutputs(plan.root, step, startedEpoch);
    const completedAt = new Date().toISOString();
    summary.steps.push({
      id: step.id,
      phase: step.phase,
      startedAt,
      completedAt,
      elapsedMs: Date.now() - startedEpoch,
      status: "passed",
    });
    if (step.phase === "compile") {
      summary.testCompileCount += 1;
      compiledInputDigest = await digestInputs(plan.root, step.sourceInputs);
      if (compiledInputDigest !== compileBefore) {
        throw new Error("source/test/build inputs changed during compilation");
      }
      summary.compiledInputDigest = compiledInputDigest;
      if (preparation?.previous) {
        summary.preparationInvalidations.push({
          reason: preparation.reuseWarmup ? "compiled inputs or outputs changed" : "build environment or dependencies changed",
          beforeDigest: preparation.previous.compiledInputDigest,
          afterDigest: compiledInputDigest,
        });
      }
    }
    if (step.phase === "package") summary.cleanPackageCount += 1;
    if (step.phase === "dependency-warmup") {
      summary.dependencyWarmupCommandCount += 1;
      summary.dependencyWarmupCount = 1;
    }
    if (step.htmlGenerated) summary.htmlGenerationCount += 1;
    if (["unit", "integration"].includes(step.lane) && step.waveNumber !== null) {
      summary.measurementStartedAt ??= startedAt;
      summary.waveResults.push({
        lane: step.lane,
        wave: step.waveNumber,
        runner: step.runner,
        tests: step.tests,
        command: step.command,
        args: step.args,
        reportDirectory: step.reportDirectory,
        coverageExec: step.coverageExec,
        kind: step.phase === "full-suite" ? "validation" : step.waveKind,
        fullSuite: step.phase === "full-suite",
        validatedTests: (await readdir(path.join(plan.root, step.reportDirectory)))
          .filter((file) => /^TEST-.+\.xml$/.test(file))
          .map((file) => file.slice(5, -4)),
        startedAt,
        completedAt,
        elapsedMs: Date.now() - startedEpoch,
        status: "passed",
      });
    }
    if (step.phase === "full-suite") {
      summary.fullSuiteGenerationCount = 1;
      summary.fullSuiteRunnerExecutions += 1;
    }
  }
  if (preparation && !plan.finalizeOnly) {
    summary.preparationProof = {
      runId: preparation.runId,
      dependencyInputDigest: preparation.dependencyDigest,
      compiledInputDigest: summary.compiledInputDigest,
      compiledOutputDigest: await optionalOutputDigest(plan.root, preparation.outputInputs),
      fullSuitePassed: summary.fullSuiteGenerationCount === 1,
      testConfigurationDigest: preparation.testConfigurationDigest,
      coverageOutputDigest: await digestInputs(plan.root, plan.coverageOutputs),
    };
  }
  summary.completedAt = new Date().toISOString();
}

function buildWaveSteps(
  lane,
  waves,
  config,
  artifactRoot,
  maven,
  offline,
  jacocoVersion,
  runnerVersion,
) {
  return waves.map((wave) =>
    buildTestStep({
      phase: wave.fullSuite ? "full-suite" : "wave",
      lane,
      wave,
      config,
      artifactRoot,
      maven,
      offline,
      jacocoVersion,
      runnerVersion: wave.runner === "failsafe"
        ? config.failsafeVersion ?? config.surefireVersion
        : config.surefireVersion,
    }),
  );
}

function buildTestStep({
  phase,
  lane,
  wave,
  config,
  artifactRoot,
  maven,
  offline,
  jacocoVersion,
  runnerVersion,
}) {
  const waveId = sanitizeId(wave.id ?? `${lane}-${wave.wave ?? "suite"}`);
  const runner = wave.runner ?? (lane === "integration" ? "surefire" : "surefire");
  const plugin =
    runner === "failsafe"
      ? `org.apache.maven.plugins:maven-failsafe-plugin:${runnerVersion}:integration-test`
      : `org.apache.maven.plugins:maven-surefire-plugin:${runnerVersion}:test`;
  const testProperty = runner === "failsafe" ? "it.test" : "test";
  const reportDirectory = `${artifactRoot}/${waveId}/reports`;
  const coverageExec = `${artifactRoot}/${waveId}/jacoco.exec`;
  const sourceReportDirectory =
    runner === "failsafe" ? "target/failsafe-reports" : "target/surefire-reports";
  const args = [
    ...offline,
    `org.jacoco:jacoco-maven-plugin:${jacocoVersion}:prepare-agent`,
    plugin,
    ...(runner === "failsafe"
      ? [
          `org.apache.maven.plugins:maven-failsafe-plugin:${runnerVersion}:verify`,
        ]
      : []),
    `-Djacoco.destFile=${coverageExec}`,
    "-Djacoco.append=true",
    "-DfailIfNoTests=true",
    `-D${runner}.failIfNoSpecifiedTests=true`,
  ];
  if ((wave.tests ?? []).length > 0) {
    args.push(`-D${testProperty}=${wave.tests.join(",")}`);
  }
  args.push(...(wave.mavenArgs ?? []));
  return {
    phase,
    id: `${phase}-${waveId}`,
    lane,
    waveNumber: wave.wave ?? null,
    waveKind: wave.kind ?? "generation",
    additionalMavenArgs: wave.mavenArgs ?? [],
    runner,
    tests: [...(wave.tests ?? [])],
    command: maven,
    args,
    reportDirectory,
    coverageExec,
    outputs: [coverageExec, reportDirectory],
    requireTestXml: true,
    captureNativeReports: true,
    sourceReportDirectory,
    sourceInputs:
      config.compiledInputs ?? ["pom.xml", "src/main", "src/test"],
  };
}

function validateConfig(config) {
  for (const key of ["jacocoVersion", "surefireVersion"]) {
    if (!config[key]) throw new Error(`${key} is required`);
  }
  validateMavenArgs(config.mavenArgs ?? [], "mavenArgs");
  if (config.runtimePhase && !["assessment", "conversion"].includes(config.runtimePhase)) {
    throw new Error("runtimePhase must be assessment or conversion");
  }
  if (config.javaPreflight !== undefined) {
    const options = config.javaPreflight;
    const allowed = new Set(["buildInputs", "testSourceRoots", "files", "outputDirectory", "externalInputDigest"]);
    if (!options || typeof options !== "object" || Array.isArray(options) ||
      Object.keys(options).some((key) => !allowed.has(key))) {
      throw new Error("javaPreflight accepts only buildInputs, testSourceRoots, files, private outputDirectory, and externalInputDigest; the Java gate cannot be disabled");
    }
    for (const key of ["buildInputs", "testSourceRoots", "files"]) {
      if (options[key] !== undefined &&
        (!Array.isArray(options[key]) || options[key].some(
          (value) => typeof value !== "string" || !value.trim(),
        ))) {
        throw new Error(`javaPreflight.${key} must contain file/directory paths`);
      }
    }
    if (options.outputDirectory !== undefined &&
      (typeof options.outputDirectory !== "string" || !options.outputDirectory.trim())) {
      throw new Error("javaPreflight.outputDirectory must be a private runtime directory path");
    }
    if (options.externalInputDigest !== undefined && options.externalInputDigest !== null &&
      (typeof options.externalInputDigest !== "string" ||
        !/^[a-f0-9]{64}$/.test(options.externalInputDigest))) {
      throw new Error("javaPreflight.externalInputDigest must be a SHA-256 digest");
    }
  }
  const globalIds = new Set();
  const fullSuiteRunners = new Set();
  for (const [lane, enabled] of [
    ["unit", config.createUnitTests !== false],
    ["integration", config.createIntegrationTests !== false],
  ]) {
    const waves = config[`${lane}Waves`] ?? [];
    if (enabled && (waves.length < MINIMUM_TEST_WAVES || waves.length > MAXIMUM_TEST_WAVES)) {
      throw new Error(`${lane} waves must contain 2-10 entries`);
    }
    for (const [index, wave] of waves.entries()) {
      if (wave.wave !== index + 1) throw new Error(`${lane} wave numbers must be sequential`);
      if (!["surefire", "failsafe"].includes(wave.runner ?? "surefire")) {
        throw new Error(`${lane} wave runner must be surefire or failsafe`);
      }
      if (index >= MINIMUM_TEST_WAVES) validateOptionalWaveValue(wave.continuationEvidence);
      if (wave.fullSuite) {
        if (index !== waves.length - 1 || wave.kind !== "validation") {
          throw new Error("only the last validation wave can serve as the full suite");
        }
        const runner = fullSuiteKey(wave);
        if (fullSuiteRunners.has(runner)) {
          throw new Error("a full-suite runner cannot be duplicated across test lanes");
        }
        fullSuiteRunners.add(runner);
      }
      const id = sanitizeId(wave.id ?? "");
      if (globalIds.has(id)) {
        throw new Error("wave/full-suite IDs must be globally unique");
      }
      globalIds.add(id);
      if (!Array.isArray(wave.tests) || (wave.tests.length === 0 && !wave.fullSuite)) {
        throw new Error(`${lane} wave ${index + 1} must list tests`);
      }
      if (wave.fullSuite && wave.tests.length > 0) {
        throw new Error("a full-suite validation wave must not restrict test selectors");
      }
      validateMavenArgs(wave.mavenArgs ?? [], `${lane} wave ${index + 1}`);
    }
  }
  const fullSuiteRuns = configuredFullSuiteRuns(config);
  const configuredRunners = new Set();
  for (const run of fullSuiteRuns) {
    const runner = run.runner ?? "surefire";
    const key = fullSuiteKey(run);
    if (!["surefire", "failsafe"].includes(runner) || configuredRunners.has(key)) {
      throw new Error("full-suite runners must be valid and unique");
    }
    configuredRunners.add(key);
    if (!Array.isArray(run.tests ?? []) || (run.tests ?? []).length !== 0) {
      throw new Error("complete-suite executions must not restrict test selectors");
    }
    const id = sanitizeId(run.id ?? "");
    if (globalIds.has(id)) {
      throw new Error("wave/full-suite IDs must be globally unique");
    }
    globalIds.add(id);
    validateMavenArgs(run.mavenArgs ?? [], `full suite ${id}`);
  }
  if ((config.archives ?? []).some((archive) => !archive.id || !archive.path)) {
    throw new Error("archive id and path are required");
  }
  if ((config.archives ?? []).length > 0 && (config.prePackageChecks ?? []).length === 0) {
    throw new Error("archives require at least one pre-package check");
  }
  if (
    ((config.integrationWaves ?? []).length > 0 ||
      fullSuiteRuns.some((run) => run.runner === "failsafe")) &&
    !config.dockerEnvironment
  ) {
    throw new Error("integration execution requires one run-owned shared Docker PostgreSQL environment");
  }
  if (config.dockerEnvironment && config.dockerEnvironment.runId !== config.runId) {
    throw new Error("the shared Docker environment must belong to the current migration run");
  }
  if (config.preparedBuild && (!config.runId || config.preparedBuild.runId !== config.runId)) {
    throw new Error("prepared build evidence must belong to the current migration run");
  }
  if (config.finalizeOnly && (!config.preparedBuild || config.finalize === false)) {
    throw new Error("finalize-only requires prepared build evidence and finalization enabled");
  }
}

function fullSuiteKey(run) {
  return JSON.stringify([run.runner ?? "surefire", run.mavenArgs ?? []]);
}

function configuredFullSuiteRuns(config) {
  if (config.fullSuiteRuns) return config.fullSuiteRuns;
  const usesFailsafe = (config.integrationWaves ?? []).some(
    (wave) => wave.runner === "failsafe",
  );
  return [
    { id: "full-unit-suite", runner: "surefire", tests: [] },
    ...(usesFailsafe
      ? [{ id: "full-integration-suite", runner: "failsafe", tests: [] }]
      : []),
  ];
}

function validateMavenArgs(args, location) {
  if (!Array.isArray(args) || args.some((argument) =>
    typeof argument !== "string" || !argument.trim())) {
    throw new Error(`${location} must contain nonempty Maven argument strings`);
  }
  const forbidden = [
    /^-DskipTests(?:=|$)/i,
    /^-Dmaven\.test\.skip(?:=|$)/i,
    /^-DskipITs(?:=|$)/i,
    /^-Dmaven\.test\.failure\.ignore(?:=|$)/i,
    /^-Dsurefire\.failIfNoSpecifiedTests=false$/i,
    /^-Dfailsafe\.failIfNoSpecifiedTests=false$/i,
    /^-DfailIfNoTests=false$/i,
  ];
  if (args.some((arg) => forbidden.some((pattern) => pattern.test(arg)))) {
    throw new Error(`${location} contains a forbidden test bypass`);
  }
  if (args.some((arg) =>
    /^(?:clean|compile|test-compile|test|verify|package|install|deploy|dependency:go-offline|dependency:get)$/.test(arg) ||
    /^[^-].*:(?:clean|compile|test-compile|test|verify|package|install|deploy|go-offline|get)$/.test(arg))) {
    throw new Error(`${location} must contain options, not repeated build/dependency lifecycle goals`);
  }
}

function normalizeRelative(value) {
  return String(value).replaceAll("\\", "/").replace(/^\.\/+/, "");
}

function sanitizeId(value) {
  const result = String(value)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (!result) throw new Error("wave ID is invalid");
  return result;
}

async function inspectPreparation(plan, javaPreflight) {
  if (!plan.preparation) return null;
  const preparation = plan.preparation;
  const dependencyDigest = createHash("sha256")
    .update(JSON.stringify({
      ...preparation.settings,
      buildEnvironmentDigest: environmentDigest(),
      javaContextDigest: javaPreflight.contextDigest,
      javaBuildInputDigest: javaPreflight.manifestInputDigest ?? javaPreflight.evidence.inputDigest,
    }))
    .update(await digestInputs(plan.root, preparation.dependencyInputs))
    .digest("hex");
  const sourceDigest = await digestInputs(plan.root, preparation.sourceInputs);
  const outputDigest = await optionalOutputDigest(plan.root, preparation.outputInputs);
  const previous = preparation.previous;
  const reuseWarmup = previous?.runId === preparation.runId &&
    previous.dependencyInputDigest === dependencyDigest;
  const reuseCompile = reuseWarmup &&
    previous.compiledInputDigest === sourceDigest &&
    outputDigest !== null &&
    previous.compiledOutputDigest === outputDigest;
  return { ...preparation, dependencyDigest, sourceDigest, reuseWarmup, reuseCompile };
}

async function optionalOutputDigest(root, inputs) {
  try {
    return await digestInputs(root, inputs);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    return null;
  }
}

async function executionRuntime(plan, injectedRuntime) {
  if (injectedRuntime) {
    if (injectedRuntime.root !== await realpath(plan.root) ||
      (plan.execution?.runId && plan.execution.runId !== injectedRuntime.runId) ||
      (plan.execution?.actorId && plan.execution.actorId !== injectedRuntime.actorId)) {
      throw new Error("Injected runtime must match the codebase, runId, and primary execution actor");
    }
    return injectedRuntime;
  }
  if (!plan.execution?.runId || !plan.execution?.actorId) {
    throw new Error("Real execution requires explicit runId and actorId primary ownership");
  }
  return openMigrationRuntime({
    root: plan.root,
    ...plan.execution,
    runtimeDirectory: plan.execution.runtimeDirectory
      ? path.resolve(plan.root, plan.execution.runtimeDirectory)
      : undefined,
    initialize: true,
  });
}

function environmentDigest() {
  return digestJson(Object.entries(process.env).sort(([left], [right]) => left.localeCompare(right)));
}

function inside(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

function inputPath(root, value) {
  const absolute = path.resolve(root, value.replaceAll("\\", path.sep));
  if (!inside(root, absolute)) throw new Error("Java preflight inputs must remain within the codebase");
  return path.relative(root, absolute) || ".";
}

async function javaTestFiles(plan, evidence) {
  const files = new Set((plan.javaPreflight?.files ?? []).map(
    (value) => inputPath(plan.root, value),
  ));
  const roots = new Set([
    ...evidence.modules.flatMap((module) => [
      path.join(module.modulePath.replaceAll("\\", path.sep), "src", "test"),
      path.join(module.modulePath.replaceAll("\\", path.sep), "target", "generated-test-sources"),
    ]),
    ...(plan.javaPreflight?.testSourceRoots ?? []),
  ].map((value) => inputPath(plan.root, value)));
  let entries = 0;
  async function visit(relative, depth = 0) {
    if (++entries > 50000 || depth > 64) throw new Error("Java test-source scan budget exceeded");
    const absolute = path.resolve(plan.root, relative);
    let metadata;
    try { metadata = await lstat(absolute); } catch (error) {
      if (error.code !== "ENOENT") throw error;
      return;
    }
    if (metadata.isSymbolicLink()) throw new Error("Java test-source paths must not traverse symbolic links");
    if (metadata.isDirectory()) {
      for (const entry of await readdir(absolute)) await visit(path.join(relative, entry), depth + 1);
    } else if (metadata.isFile() && path.extname(relative).toLowerCase() === ".java") {
      files.add(relative);
      if (files.size > 10000) throw new Error("Java test-source file limit exceeded");
    }
  }
  for (const relative of roots) await visit(relative);
  return [...files].sort();
}

async function preflightJava(plan, runtime, commandRunner, injectedEvidence) {
  if (!plan.javaPreflight) {
    if (!injectedEvidence) throw new Error("Raw fixture plans require explicit runtime and Java release evidence; production Java preflight cannot be bypassed");
    const validation = await validateJavaTestFiles({
      root: plan.root, files: await javaTestFiles(plan, injectedEvidence), evidence: injectedEvidence,
    });
    return { status: "passed", cacheStatus: "injected", cacheHit: false, contextDigest: null, evidence: injectedEvidence, files: validation.files };
  }
  if (injectedEvidence) throw new Error("Injected Java evidence is permitted only for explicit raw fixture plans");
  const options = sharedJavaOptions(plan, runtime, commandRunner);
  const snapshot = await prepareJavaTestGeneration(runtime, { ...options, files: [] });
  const validation = await prepareJavaTestGeneration(runtime, {
    ...options, files: await javaTestFiles(plan, snapshot.evidence),
  });
  if (!sameJavaSnapshot(snapshot, validation)) {
    throw new Error("Java preflight build inputs or environment changed during detection");
  }
  return {
    status: "passed",
    cacheStatus: snapshot.cacheHit ? "hit" : "miss",
    cacheHit: snapshot.cacheHit,
    cacheReason: snapshot.cacheHit ? null : "shared-cache-miss",
    contextDigest: snapshot.contextDigest,
    manifestInputDigest: snapshot.inputDigest,
    evidence: snapshot.evidence,
    files: validation.validatedFiles,
  };
}

function sharedJavaOptions(plan, runtime, commandRunner) {
  const options = plan.javaPreflight;
  const outputDirectory = path.resolve(runtime.runDirectory, options.outputDirectory);
  if (!inside(runtime.runDirectory, outputDirectory)) {
    throw new Error("Java preflight outputDirectory must remain within the private run directory");
  }
  return {
    mavenExecutable: options.mavenExecutable,
    javaExecutable: options.javaExecutable,
    mavenArgs: options.mavenArgs,
    inputs: options.buildInputs.length ? options.buildInputs : undefined,
    externalInputDigest: options.externalInputDigest,
    outputDirectory,
    commandRunner,
  };
}

function sameJavaSnapshot(before, after) {
  return after.cacheHit === true &&
    before.inputDigest === after.inputDigest &&
    before.contextDigest === after.contextDigest &&
    before.evidence.inputDigest === after.evidence.inputDigest;
}

async function verifyJavaGate(plan, runtime, preflight, commandRunner) {
  if (plan.javaPreflight) {
    const validation = await prepareJavaTestGeneration(runtime, {
      ...sharedJavaOptions(plan, runtime, commandRunner),
      files: await javaTestFiles(plan, preflight.evidence),
    });
    if (!sameJavaSnapshot({
      inputDigest: preflight.manifestInputDigest,
      contextDigest: preflight.contextDigest,
      evidence: preflight.evidence,
    }, validation)) {
      throw new Error("Java preflight build inputs or environment changed before compilation; rerun preflight");
    }
    preflight.files = validation.validatedFiles;
    return;
  }
  const validation = await validateJavaTestFiles({
    root: plan.root, files: await javaTestFiles(plan, preflight.evidence), evidence: preflight.evidence,
  });
  preflight.files = validation.files;
}

function runnerCheckpoint(summary) {
  return {
    status: "passed",
    executionOwner: summary.executionOwner,
    startedAt: summary.startedAt,
    completedAt: summary.completedAt,
    javaPreflight: {
      status: summary.javaPreflight.status,
      cacheHit: summary.javaPreflight.cacheHit,
      evidenceDigest: summary.javaPreflight.evidence.inputDigest,
      contextDigest: summary.javaPreflight.contextDigest,
      modules: summary.javaPreflight.evidence.modules,
      validatedFileCount: summary.javaPreflight.files.length,
      validatedFileDigest: digestJson(summary.javaPreflight.files),
    },
    testCompileCount: summary.testCompileCount,
    dependencyWarmupCommandCount: summary.dependencyWarmupCommandCount,
    reusedTestCompile: summary.reusedTestCompile,
    reusedDependencyWarmup: summary.reusedDependencyWarmup,
    cleanPackageCount: summary.cleanPackageCount,
    fullSuiteRunnerExecutions: summary.fullSuiteRunnerExecutions,
    compiledInputDigest: summary.compiledInputDigest,
    waveCount: summary.waveResults.length,
    dockerReuse: summary.dockerReuse,
  };
}

export async function resolveSharedPostgres(
  root,
  context,
  commandRunner = runProcessCommand,
  { runtime: injectedRuntime, actorId = context?.actorId, runtimeDirectory = context?.runtimeDirectory } = {},
) {
  const runtime = await executionRuntime({
    root,
    execution: { runId: context?.runId, actorId, runtimeDirectory },
  }, injectedRuntime);
  return withPrimaryExecution(runtime, "docker-inspection", () => inspectSharedPostgres(root, context, commandRunner));
}

async function inspectSharedPostgres(root, context, commandRunner) {
  for (const key of ["runId", "containerId", "imageId", "configurationDigest", "database"]) {
    if (typeof context?.[key] !== "string" || !context[key].trim()) {
      throw new Error(`shared Docker PostgreSQL ${key} is required`);
    }
  }
  const docker = context.dockerExecutable ?? "docker";
  const inspected = await commandRunner(root, docker, [
    "inspect", "--type", "container", context.containerId,
  ]);
  if (inspected.status !== 0) throw new Error("cannot inspect the run-owned PostgreSQL container");
  let containers;
  try {
    containers = JSON.parse(inspected.stdout);
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    throw new Error("shared PostgreSQL Docker inspection must return valid JSON");
  }
  const container = Array.isArray(containers) && containers.length === 1 ? containers[0] : null;
  if (
    !container ||
    container.Id !== context.containerId ||
    container.Image !== context.imageId ||
    container.State?.Running !== true ||
    container.Config?.Labels?.["migrate-oracle-app.run-id"] !== context.runId ||
    container.Config?.Labels?.["migrate-oracle-app.configuration-digest"] !== context.configurationDigest
  ) {
    throw new Error("shared PostgreSQL container ownership, image, configuration, or running state does not match");
  }
  if (!Array.isArray(container.Config.Env) || container.Config.Env.some(
    (entry) => typeof entry !== "string" || !entry.includes("="),
  )) {
    throw new Error("run-owned PostgreSQL environment metadata is invalid");
  }
  const environment = Object.fromEntries(
    container.Config.Env.map((entry) => {
      const separator = entry.indexOf("=");
      return [entry.slice(0, separator), entry.slice(separator + 1)];
    }),
  );
  if (
    environment.POSTGRES_DB !== context.database ||
    !environment.POSTGRES_USER ||
    !environment.POSTGRES_PASSWORD
  ) {
    throw new Error("run-owned PostgreSQL must explicitly configure database, user, and password");
  }
  const bindings = container.NetworkSettings?.Ports?.["5432/tcp"];
  if (
    !Array.isArray(bindings) ||
    bindings.length !== 1 ||
    bindings[0].HostIp !== "127.0.0.1" ||
    !/^\d+$/.test(bindings[0].HostPort) ||
    Number(bindings[0].HostPort) < 1 ||
    Number(bindings[0].HostPort) > 65535
  ) {
    throw new Error("run-owned PostgreSQL requires one explicit loopback port binding");
  }
  const ready = await commandRunner(root, docker, [
    "exec", context.containerId, "pg_isready", "-q",
    "-U", environment.POSTGRES_USER, "-d", context.database,
  ]);
  if (ready.status !== 0) throw new Error("shared PostgreSQL container is not ready");
  return {
    containerIdDigest: createHash("sha256").update(container.Id).digest("hex"),
    imageId: container.Image,
    env: {
      MIGRATION_TEST_PG_JDBC_URL: `jdbc:postgresql://127.0.0.1:${bindings[0].HostPort}/${encodeURIComponent(context.database)}`,
      MIGRATION_TEST_PG_HOST: "127.0.0.1",
      MIGRATION_TEST_PG_PORT: bindings[0].HostPort,
      MIGRATION_TEST_PG_DATABASE: context.database,
      MIGRATION_TEST_PG_USER: environment.POSTGRES_USER,
      MIGRATION_TEST_PG_PASSWORD: environment.POSTGRES_PASSWORD,
    },
  };
}

async function runFileCheck(root, check) {
  const absolute = path.join(root, check.path);
  const exists = await pathExists(absolute);
  if (check.expect === "absent" && exists) {
    return { status: 1, stdout: "", stderr: `${check.path} must be absent` };
  }
  if (check.expect === "present" && !exists) {
    return { status: 1, stdout: "", stderr: `${check.path} must be present` };
  }
  if (check.contentPattern && exists) {
    const content = await readFile(absolute, "utf8");
    const pattern = new RegExp(check.contentPattern, check.flags ?? "");
    if (!pattern.test(content)) {
      return {
        status: 1,
        stdout: "",
        stderr: `${check.path} lacks ${check.contentPattern}`,
      };
    }
  }
  return { status: 0, stdout: "passed", stderr: "" };
}

function validateArchiveOutput(output, archive) {
  const entries = output.split(/\r?\n/).filter(Boolean);
  if (entries.length === 0) {
    throw new Error(`${archive.path} produced no archive entries`);
  }
  for (const pattern of archive.required ?? []) {
    if (!entries.some((entry) => wildcardMatch(entry, pattern))) {
      throw new Error(`${archive.path} lacks required entry ${pattern}`);
    }
  }
  for (const pattern of archive.forbidden ?? []) {
    if (entries.some((entry) => wildcardMatch(entry, pattern))) {
      throw new Error(`${archive.path} contains forbidden entry ${pattern}`);
    }
  }
}

function wildcardMatch(value, pattern) {
  const escaped = String(pattern)
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replaceAll("*", ".*")
    .replaceAll("?", ".");
  return new RegExp(`^${escaped}$`).test(value.replaceAll("\\", "/"));
}

async function prepareStepOutputs(root, step) {
  for (const output of step.outputs ?? []) {
    const absolute = path.resolve(root, output);
    await rm(absolute, { recursive: true, force: true });
    await mkdir(path.dirname(absolute), { recursive: true });
  }
  if (step.captureNativeReports) {
    await rm(path.resolve(root, step.sourceReportDirectory), {
      recursive: true,
      force: true,
    });
  }
}

async function captureNativeReports(root, step) {
  const source = path.resolve(root, step.sourceReportDirectory);
  const destination = path.resolve(root, step.reportDirectory);
  await access(source);
  await mkdir(path.dirname(destination), { recursive: true });
  await cp(source, destination, { recursive: true });
}

async function validateStepOutputs(root, step, startedEpoch) {
  for (const output of step.outputs ?? []) {
    const absolute = path.resolve(root, output);
    const metadata = await stat(absolute);
    if (metadata.mtimeMs < startedEpoch - 2000) {
      throw new Error(`${step.id} produced stale output ${output}`);
    }
  }
  if (step.requireTestXml) {
    const directory = path.resolve(root, step.reportDirectory);
    const files = await readdir(directory);
    const xmlFiles = files.filter((file) => /^TEST-.+\.xml$/.test(file));
    if (xmlFiles.length === 0) {
      throw new Error(`${step.id} produced no TEST-*.xml report`);
    }
    for (const file of xmlFiles) {
      const metadata = await stat(path.join(directory, file));
      if (metadata.mtimeMs < startedEpoch - 2000) {
        throw new Error(`${step.id} reused stale test report ${file}`);
      }
    }
    const execution = await stat(path.resolve(root, step.coverageExec));
    if (execution.size === 0) {
      throw new Error(`${step.id} produced empty JaCoCo execution data`);
    }
  }
}

async function digestInputs(root, inputs) {
  const hash = createHash("sha256");
  for (const relative of [...inputs].sort()) {
    const absolute = path.resolve(root, relative);
    hash.update(relative.replaceAll("\\", "/"));
    await hashPath(hash, absolute, root);
  }
  return hash.digest("hex");
}

async function hashPath(hash, target, root) {
  const metadata = await stat(target);
  if (metadata.isDirectory()) {
    const entries = await readdir(target, { withFileTypes: true });
    for (const entry of entries.sort((left, right) =>
      left.name.localeCompare(right.name),
    )) {
      if (["target", ".git", ".github"].includes(entry.name)) continue;
      const child = path.join(target, entry.name);
      hash.update(path.relative(root, child).replaceAll("\\", "/"));
      await hashPath(hash, child, root);
    }
  } else {
    hash.update(await readFile(target));
  }
}

async function pathExists(target) {
  try {
    await access(target);
    return true;
  } catch {
    return false;
  }
}

function countSteps(plan, phase) {
  return plan.steps.filter((step) => step.phase === phase).length;
}

async function main() {
  const args = process.argv.slice(2);
  const codebase = args[0];
  const configPath = args[1];
  const dryRun = args.includes("--dry-run");
  const summaryPath = valueAfter(args, "--summary");
  if (!codebase || !configPath) {
    throw new Error(
      "Usage: node run-maven-waves.mjs <codebase> <config.json> [--dry-run] [--summary <path>]",
    );
  }
  const config = JSON.parse(await readFile(path.resolve(configPath), "utf8"));
  const plan = buildMavenWavePlan(config, codebase);
  const summary = await executeMavenWavePlan(plan, { dryRun, summaryPath });
  const serialized = `${JSON.stringify(summary, null, 2)}\n`;
  process.stdout.write(serialized);
}

function valueAfter(args, flag) {
  const index = args.indexOf(flag);
  return index >= 0 ? args[index + 1] : null;
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
