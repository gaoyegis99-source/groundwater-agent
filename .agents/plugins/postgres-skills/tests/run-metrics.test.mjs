import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  formatReportTimestamp,
  formatWallClockDuration,
  renderRunMetrics,
} from "../skills/migrate-oracle-app/scripts/format-run-metrics.mjs";

test("timestamps use readable local time with lowercase am/pm and full dates", () => {
  const cases = [
    ["2026-06-10T23:10:00.000Z", "America/Los_Angeles", "4:10pm, June 10, 2026"],
    ["2026-06-11T01:07:30.000Z", "America/Los_Angeles", "6:07pm, June 10, 2026"],
    ["2026-01-01T00:00:00.000Z", "UTC", "12:00am, January 1, 2026"],
    ["2026-01-01T12:00:00.000Z", "UTC", "12:00pm, January 1, 2026"],
    ["2026-06-10T18:10:00+02:00", "UTC", "4:10pm, June 10, 2026"],
    ["2026-01-01T00:30:00.000Z", "America/Los_Angeles", "4:30pm, December 31, 2025"],
  ];
  for (const [timestamp, timeZone, expected] of cases) {
    assert.equal(formatReportTimestamp(timestamp, timeZone), expected);
  }
});

test("durations always spell out minutes and seconds and include nonzero hours", () => {
  const cases = [
    [0, "0 minutes, 0 seconds"],
    [499, "0 minutes, 0 seconds"],
    [500, "0 minutes, 1 second"],
    [45_000, "0 minutes, 45 seconds"],
    [60_000, "1 minute, 0 seconds"],
    [61_000, "1 minute, 1 second"],
    [59_999, "1 minute, 0 seconds"],
    [3_599_999, "1 hour, 0 minutes, 0 seconds"],
    [3_661_000, "1 hour, 1 minute, 1 second"],
    [6_870_000, "1 hour, 54 minutes, 30 seconds"],
    [90_062_000, "25 hours, 1 minute, 2 seconds"],
  ];
  for (const [milliseconds, expected] of cases) {
    assert.equal(formatWallClockDuration(milliseconds), expected);
  }
});

test("invalid timestamps, time zones, and elapsed values fail explicitly", () => {
  for (const value of [
    null,
    "June 10, 2026",
    "2026-06-10T16:10:00",
    "2026-02-30T16:10:00Z",
    "2026-06-10T24:10:00Z",
    "2026-06-10T16:10:00+99:00",
  ]) {
    assert.throws(() => formatReportTimestamp(value, "UTC"), /timestamp/);
  }
  for (const timeZone of [undefined, "", "Moon/Base"]) {
    assert.throws(() => formatReportTimestamp("2026-06-10T23:10:00Z", timeZone));
  }
  for (const value of [-1, 0.5, "60000", NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => formatWallClockDuration(value), /wallClockElapsedMs/);
  }
});

test("both reports share the exact three-row Run Metrics format", () => {
  assert.equal(renderRunMetrics(timingEvidence()), `### Run Metrics

**Time zone:** \`America/Los_Angeles\`

| Phase | Start | Completed | Wall Clock Duration |
|---|---|---|---|
| Assessment | 4:10pm, June 10, 2026 | 4:13pm, June 10, 2026 | 3 minutes, 0 seconds |
| Conversion | 4:13pm, June 10, 2026 | 6:07pm, June 10, 2026 | 1 hour, 54 minutes, 30 seconds |
| Total Runs | 4:10pm, June 10, 2026 | 6:07pm, June 10, 2026 | 1 hour, 57 minutes, 30 seconds |`);
});

test("assessment-only reports explicitly mark conversion as not run", () => {
  const metrics = timingEvidence();
  metrics.conversion = { status: "not-run" };
  metrics.total = { ...metrics.assessment };
  const section = renderRunMetrics(metrics);
  assert.match(section, /\| Conversion \| Not run \| Not run \| Not run \|/);
  assert.match(section, /\| Total Runs .* \| 3 minutes, 0 seconds \|/);
});

test("unfinished phases show observed durations without inventing completion timestamps", () => {
  for (const [status, label] of [
    ["in-progress", "In progress"],
    ["interrupted", "Interrupted"],
  ]) {
    const metrics = timingEvidence();
    metrics.conversion = {
      status,
      startedAt: "2026-06-10T23:13:00.000Z",
      observedAt: "2026-06-10T23:14:02.000Z",
      wallClockElapsedMs: 62_000,
    };
    metrics.total = {
      status,
      startedAt: "2026-06-10T23:10:00.000Z",
      observedAt: "2026-06-10T23:14:02.000Z",
      wallClockElapsedMs: 242_000,
    };
    assert.ok(renderRunMetrics(metrics).includes(
      `| Conversion | 4:13pm, June 10, 2026 | ${label} | ${label}: 1 minute, 2 seconds |`,
    ));
  }
});

test("wall-clock timing uses instants rather than local clock arithmetic across DST", () => {
  const phase = completedTiming(
    "2026-03-08T09:59:00.000Z",
    "2026-03-08T10:01:00.000Z",
  );
  const section = renderRunMetrics({
    timeZone: "America/Los_Angeles",
    assessment: phase,
    conversion: { status: "not-run" },
    total: phase,
  });
  assert.ok(section.includes(
    "| Assessment | 1:59am, March 8, 2026 | 3:01am, March 8, 2026 | 2 minutes, 0 seconds |",
  ));
});

test("total timing measures the run span rather than summing phase durations", () => {
  const metrics = timingEvidence();
  metrics.conversion.startedAt = "2026-06-10T23:14:00.000Z";
  metrics.conversion.wallClockElapsedMs -= 60_000;
  assert.match(renderRunMetrics(metrics), /\| Total Runs .* \| 1 hour, 57 minutes, 30 seconds \|/);
});

test("missing, inconsistent, or falsely completed timing evidence is rejected", () => {
  const mutations = [
    (metrics) => { delete metrics.timeZone; },
    (metrics) => { delete metrics.assessment; },
    (metrics) => { delete metrics.conversion.status; },
    (metrics) => { metrics.conversion.wallClockElapsedMs += 1; },
    (metrics) => { metrics.conversion.completedAt = "2026-06-10T23:12:00.000Z"; },
    (metrics) => { metrics.conversion.status = "not-run"; },
    (metrics) => { metrics.conversion.status = "interrupted"; },
    (metrics) => { metrics.total = { status: "not-run" }; },
  ];
  for (const mutate of mutations) {
    const metrics = timingEvidence();
    mutate(metrics);
    assert.throws(() => renderRunMetrics(metrics));
  }
  assert.throws(() => renderRunMetrics(undefined), /runMetrics evidence is required/);
});

test("formatter CLI reads persisted state and emits the canonical table", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "run-metrics-test-"));
  const statePath = path.join(root, "conversion-state.json");
  const formatter = path.resolve("skills/migrate-oracle-app/scripts/format-run-metrics.mjs");
  try {
    const runMetrics = timingEvidence();
    await writeFile(statePath, JSON.stringify({ runMetrics }));
    const result = spawnSync(process.execPath, [formatter, statePath], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trimEnd(), renderRunMetrics(runMetrics));

    await writeFile(statePath, "{invalid JSON");
    const invalid = spawnSync(process.execPath, [formatter, statePath], { encoding: "utf8" });
    assert.notEqual(invalid.status, 0);
    assert.match(invalid.stderr, /Run metrics evidence must be valid JSON/);

    const missing = spawnSync(process.execPath, [formatter], { encoding: "utf8" });
    assert.equal(missing.status, 2);
    assert.match(missing.stderr, /Usage:/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

function completedTiming(startedAt, completedAt) {
  return {
    status: "completed",
    startedAt,
    completedAt,
    wallClockElapsedMs: Date.parse(completedAt) - Date.parse(startedAt),
  };
}

function timingEvidence() {
  return {
    timeZone: "America/Los_Angeles",
    assessment: completedTiming("2026-06-10T23:10:00.000Z", "2026-06-10T23:13:00.000Z"),
    conversion: completedTiming("2026-06-10T23:13:00.000Z", "2026-06-11T01:07:30.000Z"),
    total: completedTiming("2026-06-10T23:10:00.000Z", "2026-06-11T01:07:30.000Z"),
  };
}
