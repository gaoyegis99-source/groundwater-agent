import { spawnSync } from "node:child_process";
import process from "node:process";

export function runProcessCommand(cwd, command, args, { env } = {}) {
  const windows = process.platform === "win32" && !/\.exe$/i.test(command);
  const quote = (value) => {
    const text = String(value);
    return /^[A-Za-z0-9_./:\\=-]+$/.test(text) ? text : `"${text.replaceAll('"', '""')}"`;
  };
  const result = spawnSync(
    windows ? process.env.ComSpec ?? "cmd.exe" : command,
    windows ? ["/d", "/s", "/c", [command, ...args].map(quote).join(" ")] : args,
    {
      cwd,
      encoding: "utf8",
      shell: false,
      windowsVerbatimArguments: windows,
      env: env ? { ...process.env, ...env } : process.env,
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  if (result.error) throw result.error;
  if (result.signal) throw new Error(`${command} terminated by signal ${result.signal}`);
  return {
    status: result.status ?? 1,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
  };
}
