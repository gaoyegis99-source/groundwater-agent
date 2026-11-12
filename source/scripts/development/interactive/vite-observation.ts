import { createHash, randomUUID } from "node:crypto";
import { readFile, rename, writeFile } from "node:fs/promises";
import { isAbsolute, relative } from "node:path";
import type { Plugin } from "vite";

const CLIENT_ID = "virtual:gw-dev-hmr";
const CLIENT_MODULE = `
const pending = new Map();
const hot = import.meta.hot;
const reloadKey = "gw-dev-hmr-reload";
try {
  const previous = JSON.parse(sessionStorage.getItem(reloadKey) || "null");
  sessionStorage.removeItem(reloadKey);
  if (previous?.url === location.href && Date.now() - previous.time < 10000) {
    for (const id of previous.ids) hot?.send("gw:hmr-browser-reloaded", { id });
  }
} catch {}
hot?.on("gw:hmr-candidate", ({ id, timestamp, path }) => {
  if (pending.size === 64) pending.delete(pending.keys().next().value);
  pending.set(id, { timestamp, path });
});
hot?.on("vite:error", () => pending.clear());
hot?.on("vite:beforeFullReload", ({ triggeredBy }) => {
  if (!triggeredBy) return;
  const file = triggeredBy.replaceAll(String.fromCharCode(92), "/");
  const ids = [...pending].filter(([, candidate]) =>
    file.endsWith("/" + candidate.path) && Date.now() - candidate.timestamp < 5000
  ).map(([id]) => id);
  if (ids.length) {
    try {
      sessionStorage.setItem(reloadKey, JSON.stringify({ ids, url: location.href, time: Date.now() }));
    } catch {}
  }
  pending.clear();
});
hot?.on("vite:afterUpdate", ({ updates }) => {
  const timestamps = new Set(updates.map(update => update.timestamp));
  for (const [id, candidate] of pending) {
    if (timestamps.has(candidate.timestamp)) {
      hot.send("gw:hmr-after-update", { id });
      pending.delete(id);
    }
  }
});
`;

type Observation = {
  path: string;
  sourceDigest: string;
  timestamp: number;
  status: "VITE_DETECTED" | "BROWSER_AFTER_UPDATE" | "BROWSER_RELOADED";
};

export function observeDevelopmentHmr(): Plugin {
  const source = process.env.GW_DEV_SOURCE_ROOT;
  const receipt = process.env.GW_DEV_HMR_RECEIPT;
  const observations = new Map<string, Observation>();
  let base = "/";
  let writeQueue = Promise.resolve();

  function persist() {
    if (!receipt) return;
    const document = JSON.stringify(Object.fromEntries(
      [...observations.values()].map(observation => [observation.path, observation]),
    ));
    const temporary = receipt + ".tmp";
    writeQueue = writeQueue.then(async () => {
      await writeFile(temporary, document, { mode: 0o600 });
      await rename(temporary, receipt);
    }).catch(error => {
      console.error("Unable to record Vite HMR observation", error);
    });
  }

  return {
    name: "platform-dev-hmr-observation",
    apply: "serve",
    config() {
      const cacheDir = process.env.GW_DEV_VITE_CACHE;
      if (cacheDir) return { cacheDir };
    },
    configResolved(config) {
      base = config.base;
    },
    resolveId(id) {
      if (id === CLIENT_ID) return "\0" + CLIENT_ID;
    },
    load(id) {
      if (id === "\0" + CLIENT_ID) return CLIENT_MODULE;
    },
    transformIndexHtml() {
      if (!source || !receipt) return;
      return [{
        tag: "script",
        attrs: { type: "module", src: base + "@id/__x00__" + CLIENT_ID },
        injectTo: "body",
      }];
    },
    configureServer(server) {
      for (const [event, status] of [
        ["gw:hmr-after-update", "BROWSER_AFTER_UPDATE"],
        ["gw:hmr-browser-reloaded", "BROWSER_RELOADED"],
      ] as const) {
        server.ws.on(event, (data: { id?: string } | null) => {
          const observation = data?.id ? observations.get(data.id) : undefined;
          if (!observation || observation.status !== "VITE_DETECTED") return;
          observation.status = status;
          persist();
        });
      }
    },
    async handleHotUpdate(context) {
      if (!source || !receipt) return;
      const path = relative(source, context.file).replaceAll("\\", "/");
      if (isAbsolute(path) || path.startsWith("../")) return;
      const bytes = await readFile(context.file).catch(() => undefined);
      if (!bytes) return;
      const id = randomUUID();
      if (observations.size >= 64) {
        const oldest = observations.keys().next().value;
        if (oldest !== undefined) observations.delete(oldest);
      }
      observations.set(id, {
        path,
        sourceDigest: "sha256:" + createHash("sha256").update(bytes).digest("hex"),
        timestamp: context.timestamp,
        status: "VITE_DETECTED",
      });
      persist();
      context.server.ws.send("gw:hmr-candidate", { id, timestamp: context.timestamp, path });
    },
  };
}
