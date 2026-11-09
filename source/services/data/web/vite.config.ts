import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";
import { frontendBuildOutput } from "../../../packages/ui-boundary/build-path.ts";

const sourceRoot = fileURLToPath(new URL("../../..", import.meta.url));

type BuildLogLevel = "info" | "debug" | "warn" | "error";
type BuildLog = { code?: string; message: string };
type BuildLogOrString = BuildLog | string;
function onLog(level: Exclude<BuildLogLevel, "error">, log: BuildLog, defaultHandler: (level: BuildLogLevel, log: BuildLogOrString) => void) {
  if (
    level === "warn"
    && log.code === "MODULE_LEVEL_DIRECTIVE"
    && log.message?.includes("@tanstack/react-query")
    && log.message.includes('"use client"')
  ) return;
  defaultHandler(level, log);
}

export default defineConfig({
  base: "/data/",
  build: {
    outDir: frontendBuildOutput(sourceRoot, "data"),
    emptyOutDir: true,
    rolldownOptions: {
      onLog,
      output: {
        codeSplitting: {
          groups: [
            { name: "react", test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
          ],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test-setup.ts",
    include: ["src/**/*.test.{ts,tsx}"],
  },
  server: {
    proxy: {
      "/data/v3": "http://127.0.0.1:8110",
    },
  },
});
