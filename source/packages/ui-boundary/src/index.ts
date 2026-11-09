export type ProblemIssue = {
  pointer: string;
  code?: string;
  message: string;
};

export type PlatformProblem = {
  code: string;
  requestId: string | null;
  issues: ProblemIssue[];
};

export class PlatformUiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    public readonly requestId: string | null = null,
    public readonly issues: ProblemIssue[] = [],
    public readonly retryAfterMs: number | null = null,
  ) {
    super(code);
    this.name = "PlatformUiError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function decodeProblem(value: unknown, headerRequestId: string | null): PlatformProblem {
  if (!isRecord(value)) {
    return { code: "HTTP_ERROR", requestId: headerRequestId, issues: [] };
  }
  const code = typeof value.code === "string" && value.code.length > 0
    ? value.code
    : "HTTP_ERROR";
  const embeddedRequestId = typeof value.requestId === "string" ? value.requestId : null;
  const issues = Array.isArray(value.errors)
    ? value.errors.flatMap((entry): ProblemIssue[] => {
        if (!isRecord(entry) || typeof entry.pointer !== "string" || typeof entry.message !== "string") {
          return [];
        }
        return [{
          pointer: entry.pointer,
          message: entry.message,
          ...(typeof entry.code === "string" ? { code: entry.code } : {}),
        }];
      })
    : [];
  return { code, requestId: headerRequestId ?? embeddedRequestId, issues };
}

export function parseRetryAfter(value: string | null, now = Date.now()): number | null {
  if (value === null) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Number(trimmed) * 1000;
  const timestamp = Date.parse(trimmed);
  return Number.isFinite(timestamp) ? Math.max(0, timestamp - now) : null;
}

function requiredScopePart(name: string, value: string): string {
  const result = value.trim();
  if (!result || /[\u0000-\u001f\u007f]/.test(result)) {
    throw new TypeError(`IDENTITY_SCOPE_${name.toUpperCase()}_INVALID`);
  }
  return encodeURIComponent(result);
}

export function identityScope(productId: string, project: string, subject: string): string {
  return [
    requiredScopePart("product", productId),
    requiredScopePart("project", project),
    requiredScopePart("subject", subject),
  ].join("/");
}

export function scopedStorageKey(
  namespace: string,
  productId: string,
  project: string,
  subject: string,
): string {
  return `${namespace}:v1:${identityScope(productId, project, subject)}`;
}

export function newRequestId(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

/**
 * A deliberately small, UI-only diagnostic record.  It is safe to render in
 * an operator-facing diagnostics panel: callers should use operation names
 * rather than URLs and never attach request/response bodies.
 */
export type UiDiagnostic = {
  time: string;
  kind: "HTTP" | "SSE" | "STATE" | "METRIC";
  name: string;
  status?: number;
  elapsedMs?: number;
  requestId?: string | null;
  code?: string;
};

export type DiagnosticListener = (entry: UiDiagnostic) => void;

export type DiagnosticSink = {
  record(entry: UiDiagnostic): void;
  subscribe(listener: DiagnosticListener): () => void;
  snapshot(): UiDiagnostic[];
  clear(): void;
};

/**
 * Keep diagnostics bounded and in memory only.  This is intentionally
 * framework-independent so all three UIs can use the same failure model.
 */
export function createDiagnosticSink(maxEntries = 80): DiagnosticSink {
  const limit = Number.isInteger(maxEntries) && maxEntries > 0 ? maxEntries : 80;
  const entries: UiDiagnostic[] = [];
  const listeners = new Set<DiagnosticListener>();
  return {
    record(entry) {
      const safe: UiDiagnostic = {
        time: entry.time || new Date().toISOString(),
        kind: entry.kind,
        name: entry.name.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 120),
        ...(entry.status === undefined ? {} : { status: entry.status }),
        ...(entry.elapsedMs === undefined ? {} : { elapsedMs: Math.max(0, Math.round(entry.elapsedMs)) }),
        ...(entry.requestId === undefined ? {} : { requestId: entry.requestId }),
        ...(entry.code === undefined ? {} : { code: entry.code.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, 80) }),
      };
      entries.push(safe);
      if (entries.length > limit) entries.splice(0, entries.length - limit);
      listeners.forEach(listener => listener(safe));
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    snapshot() { return entries.slice(); },
    clear() { entries.length = 0; },
  };
}

export type JsonValidator = ((value: unknown) => boolean) & {
  errors?: Array<{ instancePath?: string; keyword?: string; message?: string }> | null;
};

export function requireValidJson(
  validator: JsonValidator | undefined,
  value: unknown,
  kind: "request" | "response",
): void {
  if (validator?.(value)) return;
  const code = kind === "response" ? "API_RESPONSE_INVALID" : "API_REQUEST_INVALID";
  throw new PlatformUiError(0, code, null, (validator?.errors ?? []).map(issue => ({
    pointer: issue.instancePath || "/",
    code: issue.keyword,
    message: issue.message ?? "The JSON value does not match its contract.",
  })));
}

export async function readProblem(response: Response): Promise<PlatformUiError> {
  const requestId = response.headers.get("X-Request-ID");
  let value: unknown = null;
  if (response.headers.get("content-type")?.includes("json")) {
    try {
      value = await response.json();
    } catch {
      value = null;
    }
  }
  const problem = decodeProblem(value, requestId);
  return new PlatformUiError(
    response.status,
    problem.code === "HTTP_ERROR" ? `HTTP_${response.status}` : problem.code,
    problem.requestId,
    problem.issues,
    parseRetryAfter(response.headers.get("Retry-After")),
  );
}

export async function readValidatedJson<T>(
  response: Response,
  validator: JsonValidator | undefined,
): Promise<T> {
  if (!response.ok) throw await readProblem(response);
  if (!response.headers.get("content-type")?.includes("json")) {
    throw new PlatformUiError(response.status, "API_RESPONSE_INVALID", response.headers.get("X-Request-ID"));
  }
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    throw new PlatformUiError(response.status, "API_RESPONSE_INVALID", response.headers.get("X-Request-ID"));
  }
  try {
    requireValidJson(validator, value, "response");
  } catch (error) {
    if (error instanceof PlatformUiError) {
      throw new PlatformUiError(response.status, error.code, response.headers.get("X-Request-ID"), error.issues);
    }
    throw error;
  }
  return value as T;
}
