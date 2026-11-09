import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { isAbsolute, join, resolve } from "node:path";

function absoluteRoot(value: string, code: string): string {
  if (!isAbsolute(value)) throw new Error(code);
  return resolve(value);
}

export function workspaceBuildRoot(sourceRoot: string): string {
  const configured = process.env.PLATFORM_WEB_BUILD_ROOT;
  if (configured) return absoluteRoot(configured, "WEB_BUILD_ROOT_NOT_ABSOLUTE");
  const cacheHome = absoluteRoot(
    process.env.XDG_CACHE_HOME ?? join(homedir(), ".cache"),
    "XDG_CACHE_HOME_NOT_ABSOLUTE",
  );
  const identity = createHash("sha256")
    .update(resolve(sourceRoot))
    .digest("hex")
    .slice(0, 16);
  return join(
    cacheHome,
    "groundwater-v6",
    "build",
    "web",
    "worktrees",
    identity,
  );
}

export function frontendBuildOutput(
  sourceRoot: string,
  component: "web" | "data" | "knowledge",
): string {
  return join(workspaceBuildRoot(sourceRoot), component);
}

export function validationOutput(sourceRoot: string, name: string): string {
  if (!/^[a-z0-9][a-z0-9-]*$/.test(name)) {
    throw new Error("VALIDATION_OUTPUT_NAME_INVALID");
  }
  return join(workspaceBuildRoot(sourceRoot), "validation", name);
}
