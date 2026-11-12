import {
  createDiagnosticSink,
  newRequestId,
  PlatformUiError,
  readValidatedJson,
  requireValidJson,
  type DiagnosticSink,
  type JsonValidator,
} from "@platform/ui-boundary";
import { operatorErrorText } from "@platform/operator-ui";
import type {
  components as KnowledgeComponents,
  operations as KnowledgeOperations,
} from "./generated/knowledge-api-v3";
import type {
  components as DataComponents,
  operations as DataOperations,
} from "./generated/data-api-v3";
type Validators = {
  requestValidators: Readonly<Record<string, JsonValidator>>;
  responseValidators: Readonly<Record<string, JsonValidator>>;
};
let knowledgeValidatorsPromise: Promise<Validators> | undefined;
let initialKnowledgeValidatorsPromise: Promise<Validators> | undefined;
let dataValidatorsPromise: Promise<Validators> | undefined;
let initialDataValidatorsPromise: Promise<Validators> | undefined;

async function loadKnowledgeValidators(operationId: string): Promise<Validators> {
  const initial = await (initialKnowledgeValidatorsPromise ??= import("./generated/knowledge-api-v3.validators.initial.js"));
  if (Object.hasOwn(initial.responseValidators, operationId)) return initial;
  return knowledgeValidatorsPromise ??= import("./generated/knowledge-api-v3.validators.js");
}

async function loadDataValidators(operationId: string): Promise<Validators> {
  const initial = await (initialDataValidatorsPromise ??= import("./generated/data-api-v3.validators.initial.js"));
  if (Object.hasOwn(initial.responseValidators, operationId)) return initial;
  return dataValidatorsPromise ??= import("./generated/data-api-v3.validators.js");
}

export { PlatformUiError };
export const knowledgeDiagnostics: DiagnosticSink = createDiagnosticSink();
export type KnowledgeSchema = KnowledgeComponents["schemas"];
export type DataSchema = DataComponents["schemas"];
export type KnowledgeCapabilities = KnowledgeSchema["CapabilitiesResponse"];
export type DataCapabilities = DataSchema["CapabilitiesResponse"];

type ResponseAt<T, Key extends PropertyKey> = Key extends keyof T ? T[Key] : never;
type JsonContent<T> = T extends { content: { "application/json": infer Value } } ? Value : never;
type ApiResponse<Ops, Name extends keyof Ops> = Ops[Name] extends { responses: infer Responses }
  ? JsonContent<ResponseAt<Responses, 200> | ResponseAt<Responses, 201> | ResponseAt<Responses, 202>>
  : never;

async function requestJson<Ops, Name extends keyof Ops>(
  base: string,
  operationId: Name,
  path: string,
  body: unknown,
  signal: AbortSignal | undefined,
  loadValidators: (operationId: string) => Promise<Validators>,
): Promise<ApiResponse<Ops, Name>> {
  const operation = String(operationId);
  const { requestValidators, responseValidators } = await loadValidators(operation);
  const requestValidator = requestValidators[operation];
  if (requestValidator) requireValidJson(requestValidator, body, "request");
  const requestId = newRequestId();
  const started = performance.now();
  let response: Response;
  try {
    const headers = new Headers({ "X-Request-ID": requestId });
    if (body !== undefined) headers.set("Content-Type", "application/json");
    response = await fetch(`${base}/${path}`, {
      method: body === undefined ? "GET" : "POST",
      signal,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    const code = error instanceof DOMException && error.name === "AbortError"
      ? "ABORTED"
      : error instanceof TypeError ? "NETWORK_UNCERTAIN" : "UI_OPERATION_FAILED";
    knowledgeDiagnostics.record({
      time: new Date().toISOString(), kind: "HTTP", name: operation, status: 0,
      elapsedMs: performance.now() - started, requestId, code,
    });
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    if (error instanceof TypeError) throw new PlatformUiError(0, "NETWORK_UNCERTAIN", requestId);
    throw error;
  }
  try {
    const value = await readValidatedJson<ApiResponse<Ops, Name>>(response, responseValidators[operation]);
    knowledgeDiagnostics.record({
      time: new Date().toISOString(), kind: "HTTP", name: operation, status: response.status,
      elapsedMs: performance.now() - started,
      requestId: response.headers.get("X-Request-ID") ?? requestId,
    });
    return value;
  } catch (error) {
    const code = error instanceof PlatformUiError ? error.code : "API_RESPONSE_INVALID";
    knowledgeDiagnostics.record({
      time: new Date().toISOString(), kind: "HTTP", name: operation, status: response.status,
      elapsedMs: performance.now() - started,
      requestId: response.headers.get("X-Request-ID") ?? requestId, code,
    });
    if (error instanceof PlatformUiError && error.requestId === null) {
      throw new PlatformUiError(error.status, error.code, requestId, error.issues, error.retryAfterMs);
    }
    throw error;
  }
}

export function knowledgeApi<Name extends keyof KnowledgeOperations>(
  operationId: Name,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<ApiResponse<KnowledgeOperations, Name>> {
  return requestJson<KnowledgeOperations, Name>(
    "/knowledge/v3",
    operationId,
    path,
    body,
    signal,
    loadKnowledgeValidators,
  );
}

export function dataApi<Name extends keyof DataOperations>(
  operationId: Name,
  path: string,
  signal?: AbortSignal,
): Promise<ApiResponse<DataOperations, Name>> {
  return requestJson<DataOperations, Name>(
    "/data/v3",
    operationId,
    path,
    undefined,
    signal,
    loadDataValidators,
  );
}

const productPattern = /^[a-z][a-z0-9_.-]*$/;
const checksumPattern = /^sha256:[0-9a-f]{64}$/;

function validateIdentity(value: KnowledgeCapabilities | DataCapabilities): void {
  if (
    !productPattern.test(value.productId)
    || !value.project.trim()
    || !value.currentUser.subject.trim()
    || !value.currentUser.displayName.trim()
    || value.currentUser.roles.some(role => !["researcher", "admin", "viewer"].includes(role))
  ) {
    throw new PlatformUiError(0, "API_RESPONSE_INVALID");
  }
}

export function decodeDataVersion(
  value: DataSchema["VersionResponse"],
): DataSchema["VersionResponse"] {
  const descriptor = value.descriptor as { fields?: unknown };
  if (
    !value.versionId.trim()
    || !value.datasetId.trim()
    || !value.semanticId.trim()
    || !Number.isFinite(Date.parse(value.createdAt))
    || !descriptor
    || !Array.isArray(descriptor.fields)
    || !value.objects.every(object => object.objectId.trim() && checksumPattern.test(object.checksum))
  ) throw new PlatformUiError(0, "API_RESPONSE_INVALID");
  return value;
}

export function decodeKnowledgeCapabilities(value: KnowledgeCapabilities): KnowledgeCapabilities {
  validateIdentity(value);
  return value;
}

export function decodeDataCapabilities(value: DataCapabilities): DataCapabilities {
  validateIdentity(value);
  return value;
}

export async function validatePackage(value: unknown): Promise<KnowledgeSchema["KnowledgePackageRequest"]> {
  const { requestValidators } = await loadKnowledgeValidators("knowledge_submit_import");
  requireValidJson(
    requestValidators.knowledge_submit_import,
    value,
    "request",
  );
  return value as KnowledgeSchema["KnowledgePackageRequest"];
}

export function validateKnowledgeVersion(value: KnowledgeSchema["VersionResponse"]): void {
  if (
    !value.versionId.trim()
    || !checksumPattern.test(value.checksum)
    || !Number.isFinite(Date.parse(value.createdAt))
    || (value.verifiedAt !== null && !Number.isFinite(Date.parse(value.verifiedAt)))
  ) throw new PlatformUiError(0, "API_RESPONSE_INVALID");
}

export function errorText(error: unknown): string {
  return operatorErrorText(error);
}

export const pretty = (value: unknown) => JSON.stringify(value, null, 2);
