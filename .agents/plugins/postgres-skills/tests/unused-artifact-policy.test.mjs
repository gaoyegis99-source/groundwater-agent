import assert from "node:assert/strict";
import test from "node:test";
import {
  classifyUnusedArtifact,
  finalizeUnusedArtifactRemoval,
} from "../skills/migrate-oracle-app/scripts/unused-artifact-policy.mjs";
import { resolveOptions } from "../skills/migrate-oracle-app/scripts/target-write-policy.mjs";

test("remove-unused-artifacts defaults off and tests alias does not enable it", () => {
  assert.equal(resolveOptions()["remove-unused-artifacts"], false);
  assert.equal(resolveOptions("tests")["remove-unused-artifacts"], false);
  assert.equal(
    resolveOptions("tests,remove-unused-artifacts")["remove-unused-artifacts"],
    true,
  );
});

test("unused JAR is retained and classified when option is off", () => {
  const result = classifyUnusedArtifact(validCandidate(), false);

  assert.equal(result.status, "RETAINED_UNUSED");
  assert.equal(result.removalAuthorized, false);
  assert.equal(result.action, "retained");
});

test("active artifact remains retained even when removal option is on", () => {
  const candidate = validCandidate();
  candidate.activeReferences = 1;

  const result = classifyUnusedArtifact(candidate, true);

  assert.equal(result.status, "RETAINED_REQUIRED");
  assert.equal(result.removalAuthorized, false);
});

test("option authorizes only evidence-backed unused artifact removal", () => {
  const result = classifyUnusedArtifact(validCandidate(), true);

  assert.equal(result.status, "REMOVAL_AUTHORIZED");
  assert.equal(result.removalAuthorized, true);
});

test("completed removal requires build, tests, package, archive, and rollback proof", () => {
  const candidate = validCandidate();
  const result = finalizeUnusedArtifactRemoval(candidate, {
    removed: true,
    beforeDigest: candidate.beforeDigest,
    rollback: `git restore -- ${candidate.path}`,
    buildPassed: true,
    testsPassed: true,
    packagePassed: true,
    archiveInspectionPassed: true,
  });

  assert.equal(result.status, "REMOVED");
  assert.deepEqual(result.errors, []);
});

test("removal cannot pass without post-removal validation", () => {
  const candidate = validCandidate();
  const result = finalizeUnusedArtifactRemoval(candidate, {
    removed: true,
    beforeDigest: candidate.beforeDigest,
    rollback: `git restore -- ${candidate.path}`,
    buildPassed: true,
    testsPassed: false,
    packagePassed: true,
    archiveInspectionPassed: true,
  });

  assert.equal(result.status, "FAILED");
  assert.match(result.errors.join("\n"), /tests did not pass/);
});

function validCandidate() {
  return {
    path: "lib/DealOutlookUI.jar",
    kind: "jar",
    beforeDigest: "abc123",
    unusedEvidence: [
      "No active source references",
      "Not present in final runtime classpath",
    ],
    activeReferences: 0,
    requiredByBuild: false,
    requiredByPackage: false,
  };
}
