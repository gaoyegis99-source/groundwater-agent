export function planDockerDesktopUse({
  optionEnabled = false,
  engineRunning = false,
  desktopInstalled = false,
} = {}) {
  if (engineRunning) {
    return {
      status: "ALREADY_RUNNING",
      engineUsable: true,
      startAllowed: false,
      startRequired: false,
      reason: "Docker engine was already running; no desktop state change is authorized or needed.",
    };
  }
  if (!optionEnabled) {
    return {
      status: "NOT_RUNNING_OPTION_OFF",
      engineUsable: false,
      startAllowed: false,
      startRequired: false,
      reason: "Docker engine is not running and start-docker-desktop is off.",
    };
  }
  if (!desktopInstalled) {
    return {
      status: "DESKTOP_NOT_AVAILABLE",
      engineUsable: false,
      startAllowed: false,
      startRequired: false,
      reason: "start-docker-desktop is on, but Docker Desktop was not detected.",
    };
  }
  return {
    status: "START_AUTHORIZED",
    engineUsable: false,
    startAllowed: true,
    startRequired: true,
    reason: "The current request explicitly authorizes starting Docker Desktop.",
  };
}

export function finalizeDockerDesktopUse(plan, execution = {}) {
  if (!plan.startAllowed) {
    if (execution.startAttempted || execution.desktopStateChanged) {
      return {
        status: "POLICY_VIOLATION",
        engineUsable: false,
        optionSatisfied: false,
        errors: [
          "Docker Desktop was started or resumed without explicit start-docker-desktop authorization.",
        ],
      };
    }
    return {
      status: plan.status,
      engineUsable: plan.engineUsable,
      optionSatisfied: plan.status === "ALREADY_RUNNING",
      errors: [],
    };
  }
  if (!execution.startAttempted) {
    return {
      status: "NOT_STARTED",
      engineUsable: false,
      optionSatisfied: false,
      errors: ["Docker Desktop start was authorized but not attempted."],
    };
  }
  if (!execution.desktopStateChanged || !execution.engineResponsive) {
    return {
      status: "START_FAILED",
      engineUsable: false,
      optionSatisfied: false,
      errors: ["Docker Desktop did not reach a responsive engine state."],
    };
  }
  return {
    status: "STARTED",
    engineUsable: true,
    optionSatisfied: true,
    errors: [],
  };
}
