import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

function timestampMs(value, label = "timestamp") {
  if (
    typeof value !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
  ) {
    throw new TypeError(`${label} must be an ISO 8601 timestamp with a time-zone offset`);
  }
  const milliseconds = Date.parse(value);
  const calendar = new Date(`${value.slice(0, 19)}Z`);
  if (
    !Number.isFinite(milliseconds) ||
    !Number.isFinite(calendar.getTime()) ||
    calendar.toISOString().slice(0, 19) !== value.slice(0, 19)
  ) {
    throw new RangeError(`${label} must be a valid calendar timestamp`);
  }
  return milliseconds;
}

function timestampFormatter(timeZone) {
  if (typeof timeZone !== "string" || timeZone.trim() === "") {
    throw new TypeError("runMetrics.timeZone must record the machine's local time zone");
  }
  return new Intl.DateTimeFormat("en-US", {
    timeZone,
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

function readableTimestamp(milliseconds, formatter) {
  const parts = Object.fromEntries(
    formatter.formatToParts(milliseconds).map(({ type, value }) => [type, value]),
  );
  return `${parts.hour}:${parts.minute}${parts.dayPeriod.toLowerCase()}, ${parts.month} ${parts.day}, ${parts.year}`;
}

export function formatReportTimestamp(timestamp, timeZone) {
  return readableTimestamp(timestampMs(timestamp), timestampFormatter(timeZone));
}

export function formatWallClockDuration(milliseconds) {
  if (!Number.isSafeInteger(milliseconds) || milliseconds < 0) {
    throw new RangeError("wallClockElapsedMs must be a nonnegative safe integer");
  }
  const roundedSeconds = Math.round(milliseconds / 1000);
  const hours = Math.floor(roundedSeconds / 3600);
  const minutes = Math.floor((roundedSeconds % 3600) / 60);
  const seconds = roundedSeconds % 60;
  const unit = (count, name) => `${count} ${name}${count === 1 ? "" : "s"}`;
  return [
    ...(hours > 0 ? [unit(hours, "hour")] : []),
    unit(minutes, "minute"),
    unit(seconds, "second"),
  ].join(", ");
}

function phaseRow(phase, label, formatter) {
  if (
    !phase ||
    !["completed", "not-run", "in-progress", "interrupted"].includes(phase.status)
  ) {
    throw new TypeError(`${label} timing must declare completed, not-run, in-progress, or interrupted`);
  }
  if (phase.status === "not-run") {
    for (const key of ["startedAt", "completedAt", "observedAt", "wallClockElapsedMs"]) {
      if (phase[key] != null) {
        throw new TypeError(`${label} not-run timing must not contain ${key}`);
      }
    }
    return `| ${label} | Not run | Not run | Not run |`;
  }

  const started = timestampMs(phase.startedAt, `${label}.startedAt`);
  const completed = phase.status === "completed";
  if (!completed && phase.completedAt != null) {
    throw new TypeError(`${label} unfinished timing must not contain completedAt`);
  }
  const end = timestampMs(
    completed ? phase.completedAt : phase.observedAt,
    `${label}.${completed ? "completedAt" : "observedAt"}`,
  );
  if (end < started) {
    throw new RangeError(`${label} timing ends before it starts`);
  }
  const duration = formatWallClockDuration(phase.wallClockElapsedMs);
  if (phase.wallClockElapsedMs !== end - started) {
    throw new RangeError(`${label}.wallClockElapsedMs does not match its timestamps`);
  }
  const statusLabel = phase.status === "interrupted" ? "Interrupted" : "In progress";
  const endLabel = completed ? readableTimestamp(end, formatter) : statusLabel;
  const durationLabel = completed ? duration : `${statusLabel}: ${duration}`;
  return `| ${label} | ${readableTimestamp(started, formatter)} | ${endLabel} | ${durationLabel} |`;
}

export function renderRunMetrics(metrics) {
  if (!metrics || typeof metrics !== "object") {
    throw new TypeError("runMetrics evidence is required");
  }
  const formatter = timestampFormatter(metrics.timeZone);
  if (metrics.total?.status === "not-run") {
    throw new TypeError("Total Runs timing must record the run clock");
  }
  const rows = [
    phaseRow(metrics.assessment, "Assessment", formatter),
    phaseRow(metrics.conversion, "Conversion", formatter),
    phaseRow(metrics.total, "Total Runs", formatter),
  ];
  return `### Run Metrics

**Time zone:** \`${metrics.timeZone}\`

| Phase | Start | Completed | Wall Clock Duration |
|---|---|---|---|
${rows.join("\n")}`;
}

async function main() {
  if (process.argv.length !== 3) {
    console.error("Usage: node format-run-metrics.mjs <assessment-data.json|conversion-state.json>");
    process.exitCode = 2;
    return;
  }
  let state;
  try {
    state = JSON.parse(await readFile(path.resolve(process.argv[2]), "utf8"));
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    throw new SyntaxError("Run metrics evidence must be valid JSON");
  }
  console.log(renderRunMetrics(state.runMetrics));
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) {
  await main();
}
