import {
  createDiagnosticSink,
  newRequestId,
  PlatformUiError,
  readValidatedJson,
  requireValidJson,
  validDatasetProfile,
  validFieldProfile,
  type DatasetProfile,
  type FieldProfileFacts,
  type DiagnosticSink,
  type JsonValidator,
} from "@platform/ui-boundary";
import { operatorErrorText } from "@platform/operator-ui";
import type { components, operations } from "./generated/data-api-v3";
import type { requestValidators, responseValidators } from "./generated/data-api-v3.validators.js";

type DataValidators = { requestValidators: typeof requestValidators; responseValidators: typeof responseValidators };
let initialValidatorsPromise: Promise<DataValidators> | undefined;
let validatorsPromise: Promise<DataValidators> | undefined;
export async function loadDataValidators(operationId?: string): Promise<DataValidators> {
  if (operationId) {
    const initial = await (initialValidatorsPromise ??= import("./generated/data-api-v3.validators.initial.js"));
    if (Object.hasOwn(initial.responseValidators, operationId)) return initial;
  }
  return validatorsPromise ??= import("./generated/data-api-v3.validators.js");
}

export { PlatformUiError };
export const dataDiagnostics: DiagnosticSink = createDiagnosticSink();
export type Schema = components["schemas"];
export type Dataset = Schema["DatasetSummary"];
export type FieldDescriptor = {
  fieldId: string;
  name: string;
  type: string;
  nullable?: boolean;
  nullCount: number;
  profile?: FieldProfileFacts;
};
export type Version = Omit<Schema["VersionResponse"], "descriptor"> & {
  descriptor: {
    policy?: "FILE_ONLY" | "FILE_QUERYABLE" | "FILE_AND_METRICS";
    fields: FieldDescriptor[];
    rowCount: number | null;
    quality: unknown;
    profile?: DatasetProfile;
  };
};
export type QueryResult = Schema["QueryResponse"];
export type Publication = Omit<Schema["PublicationResponse"], "manifest"> & {
  manifest: { outputs: Schema["Output"][] };
};
export type DataCapabilities = Schema["CapabilitiesResponse"];

type ResponseAt<T, Key extends PropertyKey> = Key extends keyof T ? T[Key] : never;
type JsonContent<T> = T extends { content: { "application/json": infer Value } } ? Value : never;
type ApiResponse<Name extends keyof operations> = operations[Name] extends { responses: infer Responses }
  ? JsonContent<ResponseAt<Responses, 200> | ResponseAt<Responses, 201> | ResponseAt<Responses, 202>>
  : never;

const idPattern = /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/;
const checksumPattern = /^sha256:[0-9a-f]{64}$/;

function validDate(value: string): boolean {
  return Number.isFinite(Date.parse(value));
}

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function count(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) >= 0;
}

function validField(
  value: unknown,
  profile: DatasetProfile | undefined,
  rowCount: number | null,
): value is FieldDescriptor {
  if (!record(value)
    || typeof value.fieldId !== "string"
    || !idPattern.test(value.fieldId)
    || typeof value.name !== "string"
    || !value.name
    || typeof value.type !== "string"
    || !value.type
    || !count(value.nullCount)
    || (rowCount !== null && value.nullCount > rowCount)
    || (value.nullable !== undefined && typeof value.nullable !== "boolean")) return false;
  if (value.profile === undefined) return profile === undefined;
  return profile !== undefined
    && validFieldProfile(value.profile, profile.sampleLimitPerField, rowCount, value.nullCount);
}

export function decodeVersion(value: Schema["VersionResponse"]): Version {
  const descriptor = value.descriptor as Record<string, unknown>;
  const rowCount = descriptor?.rowCount;
  const profile = descriptor?.profile;
  const decodedProfile = profile === undefined
    ? undefined
    : validDatasetProfile(profile) ? profile : null;
  if (
    !idPattern.test(value.versionId)
    || !idPattern.test(value.datasetId)
    || !idPattern.test(value.semanticId)
    || !validDate(value.createdAt)
    || !record(descriptor)
    || !(rowCount === null || count(rowCount))
    || decodedProfile === null
    || !Array.isArray(descriptor.fields)
    || !descriptor.fields.every(field => validField(field, decodedProfile, rowCount))
    || !value.objects.every(object => idPattern.test(object.objectId) && checksumPattern.test(object.checksum))
  ) {
    throw new PlatformUiError(0, "API_RESPONSE_INVALID");
  }
  return value as Version;
}

export function decodePublication(value: Schema["PublicationResponse"]): Publication {
  if (
    !idPattern.test(value.publicationId)
    || !value.manifest
    || typeof value.manifest !== "object"
    || !Array.isArray((value.manifest as { outputs?: unknown }).outputs)
    || !Array.isArray(value.operations)
  ) {
    throw new PlatformUiError(0, "API_RESPONSE_INVALID");
  }
  return value as Publication;
}

export function decodeCapabilities(value: Schema["CapabilitiesResponse"]): DataCapabilities {
  if (
    !/^[a-z][a-z0-9_.-]*$/.test(value.productId)
    || !value.project.trim()
    || !value.currentUser.subject.trim()
    || !value.currentUser.displayName.trim()
    || value.currentUser.roles.some(role => !["researcher", "admin", "viewer"].includes(role))
  ) {
    throw new PlatformUiError(0, "API_RESPONSE_INVALID");
  }
  return value;
}

export async function validateSemanticDraft(expectedSemanticId: string, semantics: Schema["Semantics"]): Promise<void> {
  const { requestValidators } = await loadDataValidators();
  requireValidJson(
    requestValidators.data_create_semantic_revision as JsonValidator,
    { expectedSemanticId, semantics },
    "request",
  );
}

export type ApiOptions = {
  signal?: AbortSignal;
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
};

export async function api<Name extends keyof operations>(
  operationId: Name,
  path: string,
  body?: unknown,
  options: ApiOptions = {},
): Promise<ApiResponse<Name>> {
  const { requestValidators, responseValidators } = await loadDataValidators(String(operationId));
  const requestValidator = requestValidators[operationId] as JsonValidator | undefined;
  if (requestValidator) requireValidJson(requestValidator, body, "request");
  const requestId = newRequestId();
  const started = performance.now();
  let response: Response;
  try {
    const headers = new Headers({ "X-Request-ID": requestId });
    if (body !== undefined) headers.set("Content-Type", "application/json");
    response = await fetch("/data/v3/" + path, {
      method: options.method ?? (body === undefined ? "GET" : "POST"),
      signal: options.signal,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    const code = error instanceof DOMException && error.name === "AbortError"
      ? "ABORTED"
      : error instanceof TypeError ? "NETWORK_UNCERTAIN" : "UI_OPERATION_FAILED";
    dataDiagnostics.record({
      time: new Date().toISOString(), kind: "HTTP", name: String(operationId), status: 0,
      elapsedMs: performance.now() - started, requestId, code,
    });
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    if (error instanceof TypeError) throw new PlatformUiError(0, "NETWORK_UNCERTAIN", requestId);
    throw error;
  }
  try {
    const value = await readValidatedJson<ApiResponse<Name>>(
      response,
      responseValidators[operationId] as JsonValidator | undefined,
    );
    dataDiagnostics.record({
      time: new Date().toISOString(), kind: "HTTP", name: String(operationId), status: response.status,
      elapsedMs: performance.now() - started,
      requestId: response.headers.get("X-Request-ID") ?? requestId,
    });
    return value;
  } catch (error) {
    const code = error instanceof PlatformUiError ? error.code : "API_RESPONSE_INVALID";
    dataDiagnostics.record({
      time: new Date().toISOString(), kind: "HTTP", name: String(operationId), status: response.status,
      elapsedMs: performance.now() - started,
      requestId: response.headers.get("X-Request-ID") ?? requestId, code,
    });
    if (error instanceof PlatformUiError && error.requestId === null) {
      throw new PlatformUiError(error.status, error.code, requestId, error.issues, error.retryAfterMs);
    }
    throw error;
  }
}

export function errorText(value: unknown): string {
  return operatorErrorText(value);
}

export const pretty = (value: unknown) => JSON.stringify(value, null, 2);

export type RunAction = (task: () => Promise<void>) => Promise<void>;
