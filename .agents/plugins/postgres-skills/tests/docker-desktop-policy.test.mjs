import assert from "node:assert/strict";
import test from "node:test";
import {
  finalizeDockerDesktopUse,
  planDockerDesktopUse,
} from "../skills/migrate-oracle-app/scripts/docker-desktop-policy.mjs";
import { resolveOptions } from "../skills/migrate-oracle-app/scripts/target-write-policy.mjs";

test("start-docker-desktop defaults off and tests alias does not enable it", () => {
  assert.equal(resolveOptions()["start-docker-desktop"], false);
  assert.equal(resolveOptions("tests")["start-docker-desktop"], false);
  assert.equal(
    resolveOptions("tests,start-docker-desktop")["start-docker-desktop"],
    true,
  );
});

test("an already-running Docker engine can be used without starting Desktop", () => {
  const plan = planDockerDesktopUse({
    optionEnabled: false,
    engineRunning: true,
  });
  const result = finalizeDockerDesktopUse(plan);

  assert.equal(plan.status, "ALREADY_RUNNING");
  assert.equal(plan.startAllowed, false);
  assert.equal(result.engineUsable, true);
  assert.equal(result.optionSatisfied, true);
});

test("Docker Desktop is not started when the option is off", () => {
  const plan = planDockerDesktopUse({
    optionEnabled: false,
    engineRunning: false,
    desktopInstalled: true,
  });
  const result = finalizeDockerDesktopUse(plan);

  assert.equal(plan.status, "NOT_RUNNING_OPTION_OFF");
  assert.equal(plan.startAllowed, false);
  assert.equal(result.engineUsable, false);
  assert.equal(result.optionSatisfied, false);
});

test("unauthorized Docker Desktop state changes are policy violations", () => {
  const plan = planDockerDesktopUse({
    optionEnabled: false,
    engineRunning: false,
    desktopInstalled: true,
  });
  const result = finalizeDockerDesktopUse(plan, {
    startAttempted: true,
    desktopStateChanged: true,
    engineResponsive: true,
  });

  assert.equal(result.status, "POLICY_VIOLATION");
  assert.match(result.errors.join("\n"), /without explicit/);
});

test("explicit option permits a bounded Docker Desktop start", () => {
  const plan = planDockerDesktopUse({
    optionEnabled: true,
    engineRunning: false,
    desktopInstalled: true,
  });
  const result = finalizeDockerDesktopUse(plan, {
    startAttempted: true,
    desktopStateChanged: true,
    engineResponsive: true,
  });

  assert.equal(plan.status, "START_AUTHORIZED");
  assert.equal(result.status, "STARTED");
  assert.equal(result.engineUsable, true);
  assert.equal(result.optionSatisfied, true);
});

test("the option does not authorize installation or hide start failure", () => {
  const unavailable = planDockerDesktopUse({
    optionEnabled: true,
    engineRunning: false,
    desktopInstalled: false,
  });
  const failed = finalizeDockerDesktopUse(
    planDockerDesktopUse({
      optionEnabled: true,
      engineRunning: false,
      desktopInstalled: true,
    }),
    {
      startAttempted: true,
      desktopStateChanged: false,
      engineResponsive: false,
    },
  );

  assert.equal(unavailable.startAllowed, false);
  assert.equal(unavailable.status, "DESKTOP_NOT_AVAILABLE");
  assert.equal(failed.status, "START_FAILED");
  assert.equal(failed.engineUsable, false);
});
