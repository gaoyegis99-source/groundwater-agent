import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";

const emptyActivity = { items: [], generatedAt: "2026-11-01T00:00:00Z" };
export const server = setupServer(
  http.get("*/data/v3/activity", () => HttpResponse.json(emptyActivity)),
  http.get("*/knowledge/v3/activity", () => HttpResponse.json(emptyActivity)),
);
const nativeFetch = globalThis.fetch;

beforeAll(() => {
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const resolved = input instanceof Request
      ? input
      : new URL(String(input), window.location.origin);
    return nativeFetch(resolved, init);
  }) as typeof fetch;
  server.listen();
});
afterEach(() => {
  cleanup();
  server.resetHandlers();
  localStorage.clear();
  sessionStorage.clear();
  window.history.replaceState({}, "", "/knowledge/");
});
afterAll(() => {
  server.close();
  globalThis.fetch = nativeFetch;
});
