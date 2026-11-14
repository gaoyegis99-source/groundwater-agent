import type { DataPreviewResponse, DataQueryResponse, DebugEntry, ResourceDetail, ResourceReference, Result } from './types';
import {
  PlatformUiError,
  recoverDataQuerySubmission,
  readProblem,
  readValidatedJson,
  requireValidJson,
  type JsonValidator,
  type ProblemIssue,
} from '@platform/ui-boundary';
import { decodeDataPreviewResponse, decodeDataQueryResponse, decodeResourceDetail, decodeResults } from './api-contract';
import { newRequestId } from './message-routing';
import type { operations as PublicApiOperations, paths as PublicApiPaths } from './generated/public-api-v12';
import { loadRequestValidator, loadResponseValidator } from './generated/public-api-v12.validators.lazy.js';

export type PublicApiPath = keyof PublicApiPaths;
export type PublicApiMethod<P extends PublicApiPath> = Extract<
  keyof PublicApiPaths[P], 'get' | 'post' | 'put' | 'delete' | 'patch'
>;

// This list is compile-time checked against the generated Gateway contract. Dynamic
// identifiers and query strings are substituted only after the route is selected.
export const webContractPaths = [
  '/api/v12/ready',
  '/api/v12/capabilities',
  '/api/v12/sessions',
  '/api/v12/sessions/{sessionId}/messages',
  '/api/v12/sessions/{sessionId}/stop',
  '/api/v12/sessions/{sessionId}/confirm',
  '/api/v12/sessions/{sessionId}/reject',
  '/api/v12/sessions/{sessionId}/results',
  '/api/v12/sessions/{sessionId}/workflow-drafts/{draftId}',
  '/api/v12/sessions/{sessionId}/confirmations/{confirmationId}/revisions',
  '/api/v12/sessions/{sessionId}/control-proposals/{proposalId}/confirm',
  '/api/v12/sessions/{sessionId}/control-proposals/{proposalId}/dismiss',
  '/api/v12/sessions/{sessionId}/workflow-runs/{workflowRunId}/recoveries',
  '/api/v12/workflow-runs/{runId}/nodes',
  '/api/v12/workflow-runs/{runId}/graph',
  '/api/v12/workflow-runs/{runId}/publication',
  '/api/v12/agent-runs/{agentRunId}/trace',
  '/api/v12/data/datasets',
  '/api/v12/data/capabilities',
  '/api/v12/data/datasets/{datasetId}/versions',
  '/api/v12/data/versions/{versionId}',
  '/api/v12/data/versions/{versionId}/semantics/{semanticId}',
  '/api/v12/data/versions/{versionId}/objects/{objectId}/content',
  '/api/v12/data/queries',
  '/api/v12/data/queries/{queryId}',
  '/api/v12/data/queries/{queryId}/cancel',
  '/api/v12/data/previews',
  '/api/v12/data/previews/{previewId}',
  '/api/v12/data/previews/{previewId}/cancel',
  '/api/v12/data/publications',
  '/api/v12/data/publications/{publicationId}',
  '/api/v12/data/publications/{publicationId}/items/{name}',
  '/api/v12/data/publications/{publicationId}/prepare',
  '/api/v12/data/publications/{publicationId}/commit',
  '/api/v12/data/publications/{publicationId}/abort',
  '/api/v12/knowledge/documents',
  '/api/v12/knowledge/evidence/read',
  '/api/v12/knowledge/lookup',
] as const satisfies readonly PublicApiPath[];

export interface ReadinessSnapshot { status: string; components: Record<string, { status: string; error?: string }> }
export async function readReadiness(signal?: AbortSignal): Promise<ReadinessSnapshot> {
  const started = performance.now();
  const requestId = newRequestId();
  let response: Response;
  try { response = await fetch('/api/v12/ready', { signal, cache: 'no-store', headers: { 'X-Request-ID': requestId } }); }
  catch (error) { if (error instanceof TypeError) { reportApi('GET', '/ready', 0, performance.now() - started, requestId); throw new ApiError(0, 'NETWORK_UNCERTAIN', [], requestId); } throw error; }
  reportApi('GET', '/ready', response.status, performance.now() - started, response.headers.get('X-Request-ID') ?? requestId);
  if (![200, 503].includes(response.status) || !response.headers.get('content-type')?.includes('json')) throw new ApiError(response.status, `HTTP_${response.status}`, [], response.headers.get('X-Request-ID') ?? requestId);
  let raw: unknown;
  try { raw = await response.json(); } catch { throw new ApiError(response.status, 'API_RESPONSE_INVALID', [], response.headers.get('X-Request-ID') ?? requestId); }
  const readinessValidator = await loadResponseValidator('kernel_public_readiness');
  try { requireValidJson(readinessValidator as JsonValidator, raw, 'response'); }
  catch (caught) { throw asApiError(caught, response.status, response.headers.get('X-Request-ID')); }
  const componentValue = (raw as { components?: unknown }).components;
  if (!componentValue || typeof componentValue !== 'object' || Array.isArray(componentValue) || Object.keys(componentValue).length === 0) {
    throw new ApiError(response.status, 'API_RESPONSE_INVALID', [], response.headers.get('X-Request-ID') ?? requestId);
  }
  const components = Object.fromEntries(Object.entries(componentValue).map(([name, value]) => {
    if (!value || typeof value !== 'object' || typeof (value as { status?: unknown }).status !== 'string') return [name, { status: 'UNKNOWN' }];
    const component = value as { status: string; error?: unknown };
    return [name, { status: component.status, ...(typeof component.error === 'string' ? { error: component.error } : {}) }];
  }));
  return { status: (raw as { status: string }).status, components };
}

export class ApiError extends PlatformUiError {
  public readonly retryAfter: number | null;
  constructor(
    status: number,
    code: string,
    issues: ProblemIssue[] | undefined = [],
    requestId: string | null = null,
    retryAfter: number | null = null,
  ) {
    super(status, code, requestId, issues ?? [], retryAfter === null ? null : retryAfter * 1000);
    this.name = 'ApiError';
    this.retryAfter = retryAfter;
  }
}

const CSRF_STORAGE_KEY = 'platform.v12.auth.csrf';

export function researchCsrfToken() {
  try {
    const sharedToken = window.localStorage.getItem(CSRF_STORAGE_KEY);
    if (sharedToken !== null) return sharedToken;
  } catch {
    // Some browser privacy settings only permit storage for the active tab.
  }
  try { return window.sessionStorage.getItem(CSRF_STORAGE_KEY) ?? ''; }
  catch { return ''; }
}

function storeResearchCsrfToken(token: string) {
  // An empty shared entry prevents older tabs from restoring a signed-out token.
  try { window.localStorage.setItem(CSRF_STORAGE_KEY, token); }
  catch { /* Use the per-tab fallback when shared storage is disabled. */ }
  try {
    if (token) window.sessionStorage.setItem(CSRF_STORAGE_KEY, token);
    else window.sessionStorage.removeItem(CSRF_STORAGE_KEY);
  } catch { /* The server cookie remains the authentication authority. */ }
}

export async function loginResearch(username: string, password: string) {
  const result = await callApi(
    'auth_login_api_v12_auth_login_post',
    '/auth/login',
    { username, password },
  );
  storeResearchCsrfToken(result.csrfToken);
  return result.currentUser;
}

export async function logoutResearch() {
  const headers = new Headers();
  const csrf = researchCsrfToken();
  if (csrf) headers.set('X-CSRF-Token', csrf);
  const response = await fetch('/api/v12/auth/logout', { method: 'POST', headers });
  if (!response.ok) throw new ApiError(response.status, `HTTP_${response.status}`);
  if (researchCsrfToken() === csrf) storeResearchCsrfToken('');
}

function asApiError(error: unknown, status = 0, requestId: string | null = null): ApiError {
  if (error instanceof ApiError) return error;
  if (error instanceof PlatformUiError) {
    return new ApiError(
      status || error.status,
      error.code,
      error.issues,
      requestId ?? error.requestId,
      error.retryAfterMs === null ? null : Math.ceil(error.retryAfterMs / 1000),
    );
  }
  throw error;
}

let observer: ((entry: DebugEntry) => void) | undefined;
export function observeApi(listener: (entry: DebugEntry) => void) { observer = listener; return () => { observer = undefined; }; }
export function reportApi(method: string, path: string, status: number, elapsedMs: number, requestId: string | null, code?: string) {
  observer?.({ time: new Date().toLocaleTimeString(), kind: 'HTTP', name: `${method} ${path.split('?')[0]}`, status, elapsedMs: Math.round(elapsedMs), requestId, ...(code ? { code } : {}) });
}
type ResponseAt<Value, Key extends PropertyKey> = Key extends keyof Value ? Value[Key] : never;
type JsonContent<Value> = Value extends { content: { 'application/json': infer Body } } ? Body : never;
type ApiResponse<Name extends keyof PublicApiOperations> = PublicApiOperations[Name] extends { responses: infer Responses }
  ? JsonContent<ResponseAt<Responses, 200> | ResponseAt<Responses, 201> | ResponseAt<Responses, 202>>
  : never;

export async function callApi<Name extends keyof PublicApiOperations, Decoded = ApiResponse<Name>>(
  operationId: Name,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
  decode?: (value: unknown) => Decoded,
  methodOverride?: PublicApiMethod<PublicApiPath>,
): Promise<Decoded> {
  const method = (methodOverride ?? (body === undefined ? 'GET' : 'POST')).toUpperCase();
  const requestValidator = await loadRequestValidator(String(operationId)) as JsonValidator | undefined;
  if (requestValidator) {
    try { requireValidJson(requestValidator, body, 'request'); }
    catch (caught) { throw asApiError(caught); }
  }
  const started = performance.now();
  const transportRequestId = newRequestId();
  let response: Response;
  try {
    const headers = new Headers({ 'X-Request-ID': transportRequestId });
    if (body !== undefined) headers.set('Content-Type', 'application/json');
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const csrf = researchCsrfToken();
      if (csrf) headers.set('X-CSRF-Token', csrf);
    }
    response = await fetch(`/api/v12${path}`, {
      method, signal, headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if (error instanceof TypeError) {
      reportApi(method, path, 0, performance.now() - started, transportRequestId, 'NETWORK_UNCERTAIN');
      throw new ApiError(0, 'NETWORK_UNCERTAIN', [], transportRequestId);
    }
    throw error;
  }
  const responseRequestId = response.headers.get('X-Request-ID') ?? transportRequestId;
  if (!response.ok) {
    const problem = await readProblem(response);
    reportApi(method, path, response.status, performance.now() - started, responseRequestId, problem.code);
    throw asApiError(problem, response.status, responseRequestId);
  }
  let value: unknown;
  try {
    value = await readValidatedJson(
      response,
      await loadResponseValidator(String(operationId)) as JsonValidator | undefined,
    );
  } catch (caught) {
    const error = asApiError(caught, response.status, responseRequestId);
    reportApi(method, path, response.status, performance.now() - started, responseRequestId, error.code);
    throw error;
  }
  reportApi(method, path, response.status, performance.now() - started, responseRequestId);
  if (!decode) return value as Decoded;
  try {
    return decode(value);
  } catch (error) {
    if (error instanceof TypeError && error.message === 'API_RESPONSE_INVALID') {
      throw new ApiError(response.status, 'API_RESPONSE_INVALID');
    }
    throw error;
  }
}

export function projectQuery(project: string, extra: Record<string, string | number | null> = {}) {
  const query = new URLSearchParams({ projectId: project });
  Object.entries(extra).forEach(([key, value]) => { if (value !== null) query.set(key, String(value)); });
  return query.toString();
}
export function contentUrl(_project: string, versionId: string, objectId?: string) {
  if (!objectId) return `/api/v12/data/versions/${encodeURIComponent(versionId)}`;
  return `/api/v12/data/versions/${encodeURIComponent(versionId)}/objects/${encodeURIComponent(objectId)}/content`;
}

export async function readDataVersion(versionId: string, signal: AbortSignal, reference?: ResourceReference): Promise<ResourceDetail> {
  const path = `/data/versions/${encodeURIComponent(versionId)}`;
  const version = await callApi('data_get_version', path, undefined, signal, decodeResourceDetail);
  if (version.versionId !== versionId) throw new ApiError(409, 'DATA_VERSION_IDENTITY_MISMATCH');
  if (!reference || reference.versionId !== versionId) return version;
  const object = version.objects.find(item => item.objectId === reference.objectId);
  if (!object || version.datasetId !== reference.datasetId || object.checksum !== reference.checksum
    || object.format !== reference.format || object.mediaType !== reference.mediaType || object.size !== reference.sizeBytes) {
    throw new ApiError(409, 'DATA_VERSION_IDENTITY_MISMATCH');
  }
  return readDataVersionSemantic(version, reference.semanticId, signal);
}

export async function readDataVersionSemantic(version: ResourceDetail, semanticId: string, signal: AbortSignal): Promise<ResourceDetail> {
  if (version.semanticId === semanticId) return version;
  signal.throwIfAborted();
  const path = `/data/versions/${encodeURIComponent(version.versionId)}/semantics/${encodeURIComponent(semanticId)}`;
  const semantic = await callApi('data_get_semantic_revision', path, undefined, signal);
  if (semantic.versionId !== version.versionId || semantic.semanticId !== semanticId) {
    throw new ApiError(409, 'DATA_VERSION_IDENTITY_MISMATCH');
  }
  return decodeResourceDetail({ ...version, semanticId: semantic.semanticId, semantics: semantic.body });
}

export interface DataQueryRequest {
  versionId: string;
  semanticId: string;
  fields?: string[];
  filters?: { field: string; op: 'eq' | 'ne' | 'lt' | 'le' | 'gt' | 'ge' | 'contains' | 'is_null'; value: unknown }[];
  sort?: { field: string; direction: 'asc' | 'desc' }[];
}

function pollDelay(milliseconds: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
    const abort = () => {
      window.clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    const timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', abort);
      resolve();
    }, milliseconds);
    signal?.addEventListener('abort', abort, { once: true });
  });
}

function dataQueryPath(queryId: string) {
  return `/data/queries/${encodeURIComponent(queryId)}`;
}

function cancelDataQuery(path: string) {
  void callApi('data_cancel_query', `${path}/cancel`, {}).catch(() => undefined);
}

async function waitForDataQueryReceipt(body: unknown, signal?: AbortSignal) {
  signal?.throwIfAborted();
  // Keep an in-flight submission recoverable after the viewer has gone away.
  const submission = recoverDataQuerySubmission(
    requestSignal => callApi('data_create_query', '/data/queries', body, requestSignal),
  ).catch(error => { throw asApiError(error); });
  if (!signal) return submission;
  let onAbort: () => void = () => {};
  const aborted = new Promise<never>((_, reject) => {
    onAbort = () => reject(signal.reason);
    signal.addEventListener('abort', onAbort, { once: true });
  });
  try {
    return await Promise.race([submission, aborted]);
  } catch (error) {
    if (signal.aborted) {
      void submission.then(receipt => cancelDataQuery(dataQueryPath(receipt.queryId)), () => undefined);
      signal.throwIfAborted();
    }
    throw error;
  } finally {
    signal.removeEventListener('abort', onAbort);
  }
}

export async function runDataQuery(query: DataQueryRequest, offset: number, limit: number, signal?: AbortSignal): Promise<DataQueryResponse> {
  signal?.throwIfAborted();
  const accepted = await waitForDataQueryReceipt(structuredClone({
    idempotencyKey: `web-${newRequestId()}`,
    kind: 'table',
    versionIds: [query.versionId],
    semanticIds: { [query.versionId]: query.semanticId },
    fields: query.fields ?? [],
    filters: query.filters ?? [],
    groupBy: [],
    aggregates: [],
    sort: query.sort ?? [],
  }), signal);
  const path = dataQueryPath(accepted.queryId);
  try {
    while (true) {
      signal?.throwIfAborted();
      const result = await callApi('data_get_query', `${path}?offset=${offset}&limit=${limit}`, undefined, signal, decodeDataQueryResponse);
      signal?.throwIfAborted();
      if (result.state === 'SUCCEEDED') {
        if (!result.resultAvailable || !result.result || !result.receipt) throw new ApiError(410, 'QUERY_RESULT_UNAVAILABLE');
        return result;
      }
      if (result.state === 'FAILED' || result.state === 'CANCELLED') throw new ApiError(422, result.error?.code ?? `QUERY_${result.state}`);
      await pollDelay(250, signal);
    }
  } catch (error) {
    if (signal?.aborted) {
      cancelDataQuery(path);
      signal.throwIfAborted();
    }
    throw error;
  }
}
export type DataPreviewRequest = {
  kind: 'xlsx_table' | 'geopackage_layer';
  versionId: string;
  semanticId: string;
  objectId: string;
  sheetName?: string;
  layerName?: string;
};
export async function startDataPreview(preview: DataPreviewRequest, signal?: AbortSignal) {
  return callApi('data_create_preview', '/data/previews', {
    idempotencyKey: `web-${newRequestId()}`,
    kind: preview.kind,
    versionId: preview.versionId,
    semanticId: preview.semanticId,
    objectId: preview.objectId,
    ...(preview.sheetName ? { sheetName: preview.sheetName } : {}),
    ...(preview.layerName ? { layerName: preview.layerName } : {}),
  }, signal);
}
export function getDataPreview(previewId: string, offset: number, limit: number, signal?: AbortSignal) {
  return callApi('data_get_preview', `/data/previews/${encodeURIComponent(previewId)}?offset=${offset}&limit=${limit}`, undefined, signal, decodeDataPreviewResponse);
}
export async function waitForDataPreview(previewId: string, offset: number, limit: number, signal?: AbortSignal): Promise<DataPreviewResponse> {
  while (true) {
    const result = await getDataPreview(previewId, offset, limit, signal);
    if (result.state === 'SUCCEEDED') {
      if (!result.resultAvailable || !result.result) throw new ApiError(410, 'PREVIEW_RESULT_UNAVAILABLE');
      return result;
    }
    if (result.state === 'FAILED' || result.state === 'CANCELLED') throw new ApiError(422, result.error?.code ?? `PREVIEW_${result.state}`);
    await pollDelay(250, signal);
  }
}
export function cancelDataPreview(previewId: string) {
  return callApi('data_cancel_preview', `/data/previews/${encodeURIComponent(previewId)}/cancel`, {});
}
export async function allResults(sessionId: string, signal?: AbortSignal): Promise<Result[]> {
  const items: Result[] = [];
  let after = 0;
  do {
    const page = await callApi('kernel_get_session_results',
      `/sessions/${sessionId}/results?cursor=${after}`, undefined, signal,
      decodeResults,
    );
    items.push(...page.items);
    if (page.nextCursor === null) return items;
    after = page.nextCursor;
  } while (true);
}
export function errorMessage(error: unknown) {
  if (!(error instanceof ApiError)) throw error;
  if (error.code in errorDescriptions) return errorDescriptions[error.code];
  if (error.status === 409) return '当前状态或所选资料已变化，请核对最新会话和资料后继续。';
  if (error.status === 401) return '登录状态无效或已过期，请重新登录后再继续。';
  if (error.status === 403) return '当前账号没有执行这项操作的权限。';
  if (error.status === 429) return error.retryAfter ? `当前用户已有操作正在进行，请在 ${error.retryAfter} 秒后重试。` : '当前用户已有操作正在进行，请稍后重试。';
  if (error.status === 0) return '连接中断，请先查看会话或数据库确认结果。请求没有自动重发。';
  if (error.status === 404) return '没有找到所选内容，请返回列表重新选择。';
  if (error.status === 422) return '提交内容未通过检查，请核对填写的信息。技术详情中保留了具体错误代码。';
  if (error.status === 503) return '所需服务暂时不可用，请保留当前内容，查看技术详情以便排查。';
  return errorCodeMessage(error.code);
}

const errorDescriptions: Record<string, string> = {
  MESSAGE_REQUEST_CONFLICT: '这条消息的编号已对应另一次提交，请刷新对话核对已接收的内容。',
  FILE_READ_FAILED: '无法读取本地文件，请重新选择可读取的文件。',
  HASH_WORKER_FAILED: '文件校验未完成，尚未上传。请重新选择文件后重试。',
  QUERY_SUBMISSION_UNCERTAIN: '连接中断，恢复查询回执后仍无法确认提交结果。请先查看数据库任务状态。',
  NETWORK_UNCERTAIN: '连接中断，请先查看会话或数据库确认结果。请求没有自动重发。',
  AUTHENTICATION_REQUIRED: '登录状态无效或已过期，请重新登录后再继续。',
  IDENTITY_INVALID: '登录身份未通过校验，请重新登录或联系项目管理员。',
  ROLE_FORBIDDEN: '当前账号没有执行这项操作的权限。',
  PROJECT_ACCESS_FORBIDDEN: '当前账号未获准访问这个项目。',
  USER_CONCURRENCY_LIMIT: '当前账号已有一次问答或执行正在进行，请等待完成后再提交。',
  USER_EXECUTION_LIMIT: '当前账号已有一个执行批次正在进行，请等待完成后再确认。',
  USER_UPLOAD_LIMIT: '当前账号已有一个文件正在上传，请等待完成或取消后重试。',
  UPLOAD_BUSY: '当前上传容量已满，请按提示稍后重试。',
  UPLOAD_CANCELLED: '上传已取消，未发布任何新版本。',
  API_RESPONSE_INVALID: '业务接口没有返回可用的 JSON 响应，请检查网关（Gateway）和后端服务。',
  AGENT_PROVIDER_UNAVAILABLE: '智能助手服务暂时不可用，本轮回复未完成。请保留问题，待服务恢复后重新提交。',
  CALCULATION_REJECTED: '本批计算未获批准，请调整后续工作或修改要求。',
  AGENT_STOPPED: '已停止当前处理，已发布成果保留。',
  AGENT_TASK_UPDATE_INVALID: '任务要求未通过一致性检查，本轮修改未保存。请明确需要补充或替换的要求。',
  AGENT_DEADLINE_EXCEEDED: '本轮处理时间已达到上限。请缩小问题范围或分步提问。',
  AGENT_CONTEXT_TOO_LARGE: '本轮需要处理的信息过多。请缩小资料范围，或在新对话中分步分析。',
  AGENT_MODEL_OUTPUT_INVALID: '智能助手返回的内容未通过检查，本轮未能形成有效回复。可调整问题后重新提交。',
  AGENT_BUDGET_EXCEEDED: '本轮已达到处理预算，尚未形成有效回复。请缩小问题范围或分步提问。',
  AGENT_OBSERVATION_LOOP: '本轮重复查阅了相同资料，系统已停止继续处理。请调整问题后重新提交。',
  AGENT_INTENT_ROUTING_INVALID: '未能可靠判断这条消息的目标。请补充希望分析现有资料、比较可行方法，还是整理可复用流程。',
  AGENT_SDK_CONFIGURATION_INVALID: '智能助手服务配置有误，需要维护人员检查设置后才能继续。',
  OBSERVATION_RESOURCE_NOT_FOUND: '需要查阅的资料或知识内容不存在，请返回目录重新核对。',
  OBSERVATION_ARGUMENTS_INVALID: '查阅资料时使用的参数未通过检查，请核对资料和查询范围。',
  OBSERVATION_SERVICE_UNAVAILABLE: '资料或知识查询服务暂时不可用，本轮处理未完成。',
  OBSERVATION_RESPONSE_INVALID: '资料或知识查询服务返回了未通过检查的响应，需要排查服务状态。',
  DATA_VERSION_IDENTITY_MISMATCH: '返回的数据版本与所选资料不一致，或建议版本已被替代。请重新核对资料版本。',
  CONFIRMATION_DATA_VERSION_INVALID: '所选数据的版本、语义或原始对象未通过核验。请关闭参数编辑器，再打开并重新选择数据。',
  WORKFLOW_PORT_INCOMPATIBLE: '编排步骤的输入与上游输出不兼容，请核对端口和文件格式。',
  WORKFLOW_INPUT_BINDING_INVALID: '编排步骤的输入绑定不完整或无效，请核对所需资料和连接。',
  WORKFLOW_PARAMETER_BINDING_INVALID: '编排步骤的参数绑定无效，请核对参数名称和引用。',
  WORKFLOW_OPERATOR_UNKNOWN: '草稿引用的算子不在当前已安装目录中，请重新选择可用算子。',
  WORKFLOW_NODE_DIAGNOSTICS_UNAVAILABLE: '暂时无法读取节点状态，请重试。',
  WORKFLOW_DRAFT_IDENTITY_MISMATCH: '返回的草稿与本轮记录不一致，请重试读取。',
  WORKFLOW_CYCLE: '草稿的步骤形成了循环依赖，请调整执行顺序与连接。',
  WORKFLOW_NODE_UNREACHABLE: '草稿中有步骤不通向任何预期输出，请调整连接或移除该步骤。',
  WORKFLOW_OUTPUT_INVALID: '草稿的预期输出引用无效，请核对输出步骤与端口。',
  WORKFLOW_DRAFT_EVIDENCE_INVALID: '草稿引用的资料或知识未通过本轮核实，请重新查阅并核对版本。',
  WORKFLOW_DRAFT_OPERATOR_READ_REQUIRED: '草稿使用的算子尚未在本轮读取完整契约，请补充核实。',
  WORKFLOW_DRAFT_PARAMETER_SOURCE_INVALID: '草稿参数缺少对应的资料或知识依据，请补充来源。',
  DRAFT_PARAMETER_INVALID: '草稿参数值不符合算子要求，请核对取值及参数组合。',
  DRAFT_PARAMETER_BINDING_REQUIRED: '草稿缺少算子要求的参数绑定，请补充声明。',
  DRAFT_PARAMETER_UNKNOWN: '草稿填写了未声明的参数，请核对参数名称。',
  DRAFT_OPERATOR_DEFAULT_INVALID: '标记为算子默认值的参数与实际默认值不同，请核对取值与来源。',
  DRAFT_RESOURCE_FORMAT_INCOMPATIBLE: '绑定资料的格式不符合草稿输入要求，请选择兼容资料。',
  WORKFLOW_DRAFT_VALUE_INVALID: '草稿包含无效的结构化取值，请核对参数内容。',
  CRS_MISMATCH: '栅格坐标参考系不同，请提供坐标参考系一致的栅格版本。',
  GRID_MISMATCH: '栅格网格不对齐，请核对行列数和仿射变换，包括像元大小、原点和旋转。',
  GRID_REQUIRED: '栅格缺少有效的仿射网格，请核对源栅格和参考栅格的地理定位。',
  RESAMPLING_UNSUPPORTED: '请指定支持的重采样方法：最近邻、双线性或三次卷积。',
  RASTER_WARP_FAILED: '栅格网格转换失败，请检查源栅格和参考栅格。',
  COMPOSITION_FACT_REQUIRED: '缺少可核实的输入元数据。请核对文件和诊断位置，旧栅格版本可重新发布以提取元数据。',
  BAND_INVALID: '所选波段超出栅格的波段范围，请核对输入和参数。',
  NODATA_MASK_UNSUPPORTED: '所选波段的 NoData 掩膜或数据类型不符合算子要求。',
  NODATA_VALUE_INVALID: '输出数据类型无法精确表示指定的 NoData 值。',
  RASTER_MINIMUM_VALID_INPUTS_EXCEEDS_COLLECTION: '每像元要求的有效输入数不能超过栅格输入总数。',
  VALIDATION_ERROR: '提交内容未通过检查，请核对填写的信息。',
  WORKER_LOST: '计算进程中断，本次任务未能完成。请先检查运行环境。',
  ALGORITHM_FAILED: '分析计算未成功，请核对输入资料和参数，并查看技术详情。',
  RESOURCE_CHECKSUM_MISMATCH: '资料内容与记录的版本不一致，不能继续使用。请重新核对资料。',
  PREVIEW_INPUT_TOO_LARGE: '文件超过在线预览上限，请查看元数据或下载原文件。',
  PREVIEW_ARCHIVE_TOO_LARGE: '工作簿展开后的内容过大，已停止在线预览。',
  PREVIEW_FILE_INVALID: '文件结构无法安全解析，请核对格式或下载原文件。',
  PREVIEW_MACRO_FORBIDDEN: '工作簿包含宏，系统不会执行或在线解析宏内容。',
  PREVIEW_EXTERNAL_LINK_FORBIDDEN: '工作簿包含外部链接，系统已停止在线预览。',
  PREVIEW_SELECTOR_NOT_FOUND: '所选工作表或图层不存在，请重新选择。',
  PREVIEW_RESULT_UNAVAILABLE: '预览结果已过期，请重新打开该数据。',
  PREVIEW_RESULT_BUDGET: '预览内容超过响应上限，请缩小工作表或图层范围。',
};

export function errorCodeMessage(code: string) {
  return errorDescriptions[code] ?? '本次操作未完成。请查看技术详情，保留错误代码以便排查。';
}
