import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { omittedCodingNotes } from "../skills/migrate-oracle-app/scripts/coding-notes.mjs";
import {
  assembleMigrationReport, checkpointMigration, digestJson, finalizeMigrationArtifacts,
  fingerprintInputs, getManifest, openMigrationRuntime, putManifest,
  prepareJavaTestGeneration, replayMigrationState, verifyMigrationBoundary, withPrimaryExecution,
} from "../skills/migrate-oracle-app/scripts/migration-runtime.mjs";

async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "migration-runtime-test-"));
  await mkdir(path.join(root, "src"), { recursive: true });
  await writeFile(path.join(root, "pom.xml"), "<project />");
  await writeFile(path.join(root, "src", "App.java"), "class App {}");
  const runtime = await openMigrationRuntime({
    root, runId: "runtime-test", actorId: "primary",
    runtimeDirectory: path.join(root, "private-runtime"), initialize: true,
  });
  await withPrimaryExecution(runtime, "notes-input", async () => {
    await checkpointMigration(runtime, {
      phase: "assessment",
      operations: [{ op: "set", path: ["codingNotes"], value: omittedCodingNotes() }],
    });
  });
  return { root, runtime };
}

test("only the primary can own execution and simultaneous setup is rejected", async () => {
  const { root, runtime } = await fixture();
  try {
    await assert.rejects(openMigrationRuntime({
      root, runId: runtime.runId, actorId: "test-child", runtimeDirectory: runtime.directory,
    }), /registered primary/);
    await withPrimaryExecution(runtime, "maven", async (receipt) => {
      assert.equal(receipt.actorId, "primary");
      await assert.rejects(withPrimaryExecution(runtime, "docker", async () => {}), /already held/);
    });
    await withPrimaryExecution(runtime, "docker", async () => {});
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("execution lease is released after failure and mutations need a live lease", async () => {
  const { root, runtime } = await fixture();
  try {
    await assert.rejects(checkpointMigration(runtime, {
      phase: "assessment", operations: [],
    }), /live primary/);
    await assert.rejects(withPrimaryExecution(runtime, "compile", async () => {
      throw new Error("compile failed");
    }), /compile failed/);
    await withPrimaryExecution(runtime, "compile", async () => {});
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("source manifest hits reuse payloads and content/addition changes invalidate them", async () => {
  const { root, runtime } = await fixture();
  try {
    const request = { kind: "source-findings", key: "oracle", inputs: ["pom.xml", "src"] };
    assert.equal((await getManifest(runtime, request)).status, "miss");
    await withPrimaryExecution(runtime, "cache", async () => {
      await putManifest(runtime, {
        ...request, expectedInputDigest: (await getManifest(runtime, request)).inputDigest,
        data: { findings: [{ path: "src/App.java", kind: "query" }] },
      });
    });
    assert.equal((await getManifest(runtime, request)).status, "hit");
    await writeFile(path.join(root, "src", "App.java"), "class App { int changed; }");
    assert.equal((await getManifest(runtime, request)).status, "miss");
    await withPrimaryExecution(runtime, "cache", async () => {
      await putManifest(runtime, {
        ...request, expectedInputDigest: (await getManifest(runtime, request)).inputDigest,
        data: { findings: [] },
      });
    });
    await writeFile(path.join(root, "src", "New.java"), "class New {}");
    assert.equal((await getManifest(runtime, request)).status, "miss");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("a manifest cannot be stored against inputs changed since its reviewed lookup", async () => {
  const { root, runtime } = await fixture();
  try {
    const request = { kind: "source-findings", key: "race", inputs: ["src"] };
    const lookup = await getManifest(runtime, request);
    await writeFile(path.join(root, "src", "App.java"), "class App { int newer; }");
    await withPrimaryExecution(runtime, "cache", async () => {
      await assert.rejects(putManifest(runtime, {
        ...request, expectedInputDigest: lookup.inputDigest, data: { findings: [] },
      }), /changed after lookup/);
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("code-derived target manifests require fresh context and never cache write safety", async () => {
  const { root, runtime } = await fixture();
  try {
    const request = { kind: "target-objects", key: "contracts", inputs: ["src"] };
    await assert.rejects(getManifest(runtime, request), /fresh target context/);
    await withPrimaryExecution(runtime, "cache", async () => {
      await putManifest(runtime, {
        ...request, contextDigest: "context-a",
        expectedInputDigest: (await getManifest(runtime, { ...request, contextDigest: "context-a" })).inputDigest,
        data: { objects: ["app.task"] },
      });
    });
    assert.equal((await getManifest(runtime, { ...request, contextDigest: "context-a" })).status, "hit");
    assert.equal((await getManifest(runtime, { ...request, contextDigest: "context-b" })).status, "miss");
    await assert.rejects(getManifest(runtime, { ...request, kind: "write-safety" }), /Only source/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("fingerprints check actual bytes, reject escapes, and detect cache corruption", async () => {
  const { root, runtime } = await fixture();
  try {
    await assert.rejects(fingerprintInputs(root, [".."]), /within their explicit workspace/);
    await withPrimaryExecution(runtime, "cache", async () => {
      const request = { kind: "source-findings", key: "source", inputs: ["src"] };
      await putManifest(runtime, {
        ...request, expectedInputDigest: (await getManifest(runtime, request)).inputDigest, data: { count: 1 },
      });
    });
    const cache = path.join(runtime.directory, "cache", `${digestJson(["source-findings", "source"])}.json`);
    const entry = JSON.parse(await readFile(cache, "utf8"));
    entry.data.count = 2;
    await writeFile(cache, JSON.stringify(entry));
    await assert.rejects(getManifest(runtime, {
      kind: "source-findings", key: "source", inputs: ["src"],
    }), /integrity/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("checkpoints append bounded changes without writing full state or reports", async () => {
  const { root, runtime } = await fixture();
  try {
    await withPrimaryExecution(runtime, "checkpoint", async () => {
      await checkpointMigration(runtime, {
        phase: "conversion",
        operations: [{ op: "set", path: ["coverage", "unitWaves"], value: [] }],
        sections: { summary: "Initial measured result." },
      });
      await checkpointMigration(runtime, {
        phase: "conversion",
        operations: [{ op: "append", path: ["coverage", "unitWaves"], values: [{ wave: 1 }] }],
        sections: { summary: "Updated measured result." },
      });
    });
    const recovered = await replayMigrationState(runtime);
    assert.deepEqual(recovered.conversion.state.coverage.unitWaves, [{ wave: 1 }]);
    assert.equal(recovered.conversion.sections.summary, "Updated measured result.");
    await assert.rejects(readFile(path.join(root, ".github", "migrate-oracle-app", "conversions", "conversion-state.json")));
    await withPrimaryExecution(runtime, "checkpoint", async () => {
      await assert.rejects(checkpointMigration(runtime, {
        phase: "conversion", operations: [{ op: "set", path: ["__proto__", "polluted"], value: true }],
      }), /safe path/);
      await assert.rejects(checkpointMigration(runtime, {
        phase: "conversion", operations: [{ op: "set", path: ["target"], value: { password: "private" } }],
      }), /sanitized/);
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("corrupt or incomplete checkpoints never become a successful recovery", async () => {
  const { root, runtime } = await fixture();
  try {
    await withPrimaryExecution(runtime, "checkpoint", async () => {
      await checkpointMigration(runtime, { phase: "assessment", operations: [] });
    });
    const journal = path.join(runtime.runDirectory, "checkpoints.jsonl");
    const text = await readFile(journal, "utf8");
    await writeFile(journal, text.trimEnd());
    await assert.rejects(replayMigrationState(runtime), /Incomplete runtime checkpoint/);
    await writeFile(journal, text.replace('"assessment"', '"conversion"'));
    await assert.rejects(replayMigrationState(runtime), /integrity/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

const template = `<a id="top"></a>
# Oracle to PostgreSQL Application Code Conversion

**Run:** \`<run-id>\`

## Table of Contents
- [Summary](#summary)
- [Coding Notes Processing](#coding-notes-processing)
- [Appendix](#appendix)

<a id="summary"></a>
## Summary

\`<summary>\`

[Back to Top](#top)

<a id="coding-notes-processing"></a>
## Coding Notes Processing

Generated notes.

[Back to Top](#top)

<a id="appendix"></a>
## Appendix / Execution Evidence

\`<commands>\`

[Back to Top](#top)
`;

function runMetrics() {
  const phase = {
    status: "completed", startedAt: "2026-06-10T23:10:00.000Z",
    completedAt: "2026-06-10T23:11:00.000Z", wallClockElapsedMs: 60000,
  };
  return { timeZone: "America/Los_Angeles", assessment: phase, conversion: phase, total: phase };
}

test("the final boundary assembles templates and JSON once with replacement ownership checks", async () => {
  const { root, runtime } = await fixture();
  try {
    const templatePath = path.join(root, "conversion-template.md");
    await writeFile(templatePath, template);
    await withPrimaryExecution(runtime, "checkpoint", async () => {
      await checkpointMigration(runtime, {
        phase: "conversion",
        operations: [
          { op: "set", path: ["status"], value: "PARTIAL" },
          { op: "set", path: ["runMetrics"], value: runMetrics() },
          { op: "set", path: ["codingNotes"], value: omittedCodingNotes() },
        ],
        sections: { summary: "Measured conversion result.", appendix: "Commands and evidence." },
        headerBindings: { "<run-id>": runtime.runId },
      });
    });
    let receipt;
    await withPrimaryExecution(runtime, "finalize", async () => {
      receipt = await finalizeMigrationArtifacts(runtime, {
        phases: ["conversion"], templates: { conversion: templatePath }, validateArtifacts: false,
      });
    });
    assert.equal(receipt.artifacts.length, 2);
    const report = await readFile(path.join(root, receipt.artifacts[1].path), "utf8");
    assert.match(report, /Measured conversion result/);
    assert.match(report, /\| Total Runs \|/);
    assert.doesNotMatch(report, /`<summary>`/);
    const state = JSON.parse(await readFile(path.join(root, receipt.artifacts[0].path), "utf8"));
    assert.equal(state.runtimeExecution.primaryActorId, "primary");
    assert.equal((await verifyMigrationBoundary({
      root, phase: "conversion", state, report, runtimeDirectory: runtime.directory,
    })).status, "verified");
    const recorded = structuredClone(state);
    recorded.validation = { artifactValidation: { status: "passed" } };
    assert.equal((await verifyMigrationBoundary({
      root, phase: "conversion", state: recorded, report, runtimeDirectory: runtime.directory,
    })).status, "verified");
    await assert.rejects(verifyMigrationBoundary({
      root, phase: "conversion", state, report: `${report}\nchanged`, runtimeDirectory: runtime.directory,
    }), /assembly receipt/);
    await withPrimaryExecution(runtime, "finalize", async () => {
      await assert.rejects(finalizeMigrationArtifacts(runtime, {
        phases: ["conversion"], templates: { conversion: templatePath }, validateArtifacts: false,
      }), /ownership\/digest approval/);
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("final assembly rejects missing sections, duplicate headings, and manual timing prose", () => {
  const model = {
    state: { runMetrics: runMetrics(), codingNotes: omittedCodingNotes() },
    headerBindings: { "<run-id>": "run" },
    sections: { summary: "Result.", appendix: "Evidence." },
  };
  assert.throws(() => assembleMigrationReport(template, { ...model, sections: {} }), /missing/);
  assert.throws(() => assembleMigrationReport(template, {
    ...model, sections: { ...model.sections, summary: "## Duplicate" },
  }), /duplicate top-level/);
  assert.throws(() => assembleMigrationReport(template, {
    ...model, sections: { ...model.sections, appendix: "### Run Metrics\nFake timing." },
  }), /generated centrally/);
});

test("Java generation uses one verified release snapshot and validates drafts on cache hits", async () => {
  const { root, runtime } = await fixture();
  try {
    const effectivePomText = `<project>
<groupId>example</groupId><artifactId>app</artifactId><version>1</version>
<properties><maven.compiler.release>8</maven.compiler.release></properties>
</project>`;
    const config = { effectivePomText, runtimeJavaMajor: 17 };
    await withPrimaryExecution(runtime, "java-preflight", async () => {
      const first = await prepareJavaTestGeneration(runtime, config);
      assert.equal(first.cacheHit, false);
      assert.equal(first.evidence.modules[0].testRelease, 8);
      const second = await prepareJavaTestGeneration(runtime, config);
      assert.equal(second.cacheHit, true);
      await assert.rejects(prepareJavaTestGeneration(runtime, {
        ...config,
        files: [{ path: "src/GeneratedTest.java", source: 'class GeneratedTest { String sql = """select 1"""; }' }],
      }), /text\s*blocks?/i);
      const third = await prepareJavaTestGeneration(runtime, {
        ...config, effectivePomText: effectivePomText.replace(">8<", ">17<"),
      });
      assert.equal(third.cacheHit, false);
      assert.equal(third.evidence.modules[0].testRelease, 17);
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("finalization performs exactly one runtime-aware record and independent verification", async () => {
  const { root, runtime } = await fixture();
  try {
    const templatePath = path.join(root, "conversion-template.md");
    await writeFile(templatePath, template);
    await withPrimaryExecution(runtime, "checkpoint", async () => {
      await checkpointMigration(runtime, {
        phase: "conversion",
        operations: [
          { op: "set", path: ["runMetrics"], value: runMetrics() },
          { op: "set", path: ["codingNotes"], value: omittedCodingNotes() },
        ],
        sections: { summary: "Measured result.", appendix: "Evidence." },
        headerBindings: { "<run-id>": runtime.runId },
      });
    });
    const calls = [];
    await withPrimaryExecution(runtime, "finalize", async () => {
      await finalizeMigrationArtifacts(runtime, {
        phases: ["conversion"], templates: { conversion: templatePath },
        commandRunner: (_cwd, command, args) => {
          calls.push({ command, args });
          return { status: 0, stdout: "validated", stderr: "" };
        },
      });
    });
    assert.equal(calls.length, 2);
    assert.ok(calls.every(({ args }) => args.includes("--require-runtime")));
    assert.ok(calls[0].args.includes("--record"));
    assert.ok(!calls[1].args.includes("--record"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("the skill wires every generation/cache/ownership/publication gate and its tools", async () => {
  const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const skillRoot = path.join(repoRoot, "skills", "migrate-oracle-app");
  const documents = await Promise.all([
    readFile(path.join(skillRoot, "SKILL.md"), "utf8"),
    readFile(path.join(skillRoot, "references", "runtime-coordinator.md"), "utf8"),
    readFile(path.join(skillRoot, "references", "state-and-convergence.md"), "utf8"),
    readFile(path.join(skillRoot, "references", "performance-and-execution.md"), "utf8"),
  ]);
  assert.match(documents[0], /runtime-coordinator\.md/);
  assert.match(documents[0], /Before any Java test generation/);
  assert.match(documents[0], /--require-runtime --record/);
  assert.match(documents[1], /expectedInputDigest/);
  assert.match(documents[1], /exclusive.*execution\s+lease/);
  assert.match(documents[1], /never cache|does not authorize caching/i);
  assert.match(documents[2], /do not rewrite full JSON\/Markdown/);
  assert.match(documents[3], /Mandatory Runtime Gates/);
  const config = JSON.parse(await readFile(
    path.join(skillRoot, "references", "maven-wave-config-template.json"), "utf8",
  ));
  assert.equal(config.actorId, "primary");
  assert.ok(config.javaPreflight.testSourceRoots.includes("src/test/java"));
  const manifest = JSON.parse(await readFile(path.join(repoRoot, "package.json"), "utf8"));
  assert.match(manifest.scripts.test, /java-test-preflight\.test\.mjs/);
  assert.match(manifest.scripts.test, /migration-runtime\.test\.mjs/);
  assert.match(manifest.scripts.runtime, /migration-runtime\.mjs/);
});
