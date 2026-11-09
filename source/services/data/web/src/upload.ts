import { sha256 } from "@noble/hashes/sha2.js";
import {
  decodeProblem,
  newRequestId,
  parseRetryAfter,
  PlatformUiError,
  requireValidJson,
  type JsonValidator,
} from "@platform/ui-boundary";
import { dataDiagnostics, loadDataValidators } from "./api";

export type UploadPhase = "hash" | "upload";

async function hashOnMainThread(
  file: File,
  signal: AbortSignal,
  onProgress: (completed: number, total: number) => void,
): Promise<string> {
  const hash = sha256.create();
  let completed = 0;
  for await (const chunk of file.stream() as unknown as AsyncIterable<Uint8Array>) {
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    hash.update(chunk);
    completed += chunk.byteLength;
    onProgress(completed, file.size);
  }
  return Array.from(hash.digest()).map(value => value.toString(16).padStart(2, "0")).join("");
}

async function hashFile(
  file: File,
  signal: AbortSignal,
  onProgress: (completed: number, total: number) => void,
): Promise<string> {
  if (typeof Worker === "undefined") return hashOnMainThread(file, signal, onProgress);
  return new Promise<string>((resolve, reject) => {
    const worker = new Worker(new URL("./hash.worker.ts", import.meta.url), { type: "module" });
    let settled = false;
    const finish = (task: () => void) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", abort);
      worker.terminate();
      task();
    };
    const abort = () => finish(() => reject(new DOMException("Aborted", "AbortError")));
    signal.addEventListener("abort", abort, { once: true });
    worker.onmessage = (event: MessageEvent<{ type: string; completed?: number; total?: number; digest?: string }>) => {
      if (event.data.type === "progress") onProgress(event.data.completed ?? 0, event.data.total ?? file.size);
      else if (event.data.type === "result" && event.data.digest) finish(() => resolve(event.data.digest!));
      else if (event.data.type === "error") finish(() => reject(new PlatformUiError(0, "HASH_WORKER_FAILED")));
    };
    worker.onerror = () => finish(() => reject(new PlatformUiError(0, "HASH_WORKER_FAILED")));
    if (signal.aborted) abort();
    else worker.postMessage({ file });
  });
}

export async function uploadFile(
  publicationId: string,
  outputName: string,
  file: File,
  signal: AbortSignal,
  onProgress: (phase: UploadPhase, completed: number, total: number) => void,
): Promise<void> {
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  const checksum = await hashFile(file, signal, (completed, total) => onProgress("hash", completed, total));
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  await new Promise<void>((resolve, reject) => {
    const request = new XMLHttpRequest();
    const requestId = newRequestId();
    const started = performance.now();
    let settled = false;
    const finishAbort = () => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", abort);
      request.abort();
      dataDiagnostics.record({ time: new Date().toISOString(), kind: "HTTP", name: "data_upload_publication_item", status: 0, elapsedMs: performance.now() - started, requestId, code: "UPLOAD_CANCELLED" });
      reject(new DOMException("Aborted", "AbortError"));
    };
    const abort = () => finishAbort();
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) {
      finishAbort();
      return;
    }
    request.open(
      "PUT",
      `/data/v3/publications/${encodeURIComponent(publicationId)}/items/${encodeURIComponent(outputName)}`,
    );
    if (settled || signal.aborted) {
      finishAbort();
      return;
    }
    request.setRequestHeader("X-Request-ID", requestId);
    request.setRequestHeader("X-Content-SHA256", `sha256:${checksum}`);
    request.responseType = "json";
    request.upload.onprogress = event => onProgress("upload", event.loaded, event.total || file.size);
    request.onerror = () => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", abort);
      dataDiagnostics.record({ time: new Date().toISOString(), kind: "HTTP", name: "data_upload_publication_item", status: 0, elapsedMs: performance.now() - started, requestId, code: "UPLOAD_UNCERTAIN" });
      reject(new PlatformUiError(0, "UPLOAD_UNCERTAIN", requestId));
    };
    request.onabort = () => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", abort);
      dataDiagnostics.record({ time: new Date().toISOString(), kind: "HTTP", name: "data_upload_publication_item", status: 0, elapsedMs: performance.now() - started, requestId, code: "UPLOAD_CANCELLED" });
      reject(new DOMException("Aborted", "AbortError"));
    };
    request.onload = async () => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", abort);
      const responseRequestId = request.getResponseHeader("X-Request-ID") ?? requestId;
      if (request.status < 200 || request.status >= 300) {
        const problem = decodeProblem(request.response, responseRequestId);
        dataDiagnostics.record({ time: new Date().toISOString(), kind: "HTTP", name: "data_upload_publication_item", status: request.status, elapsedMs: performance.now() - started, requestId: responseRequestId, code: problem.code });
        reject(new PlatformUiError(
          request.status,
          problem.code === "HTTP_ERROR" ? `HTTP_${request.status}` : problem.code,
          problem.requestId ?? responseRequestId,
          problem.issues,
          parseRetryAfter(request.getResponseHeader("Retry-After")),
        ));
        return;
      }
      try {
        const { responseValidators } = await loadDataValidators();
        requireValidJson(
          responseValidators.data_upload_publication_item as JsonValidator,
          request.response,
          "response",
        );
        dataDiagnostics.record({ time: new Date().toISOString(), kind: "HTTP", name: "data_upload_publication_item", status: request.status, elapsedMs: performance.now() - started, requestId: responseRequestId });
        resolve();
      } catch (error) {
        dataDiagnostics.record({ time: new Date().toISOString(), kind: "HTTP", name: "data_upload_publication_item", status: request.status, elapsedMs: performance.now() - started, requestId: responseRequestId, code: error instanceof PlatformUiError ? error.code : "API_RESPONSE_INVALID" });
        reject(error);
      }
    };
    if (signal.aborted) {
      finishAbort();
      return;
    }
    request.send(file);
  });
}
