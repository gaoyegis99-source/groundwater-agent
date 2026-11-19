import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { ApiError, callApi, readDataVersion } from './api';
import { argumentGroup, argumentLabel, modelName, parameterPresentation } from './card-presentation';
import { DataFieldQuality } from './DataFieldQuality';
import { ErrorNotice } from './ErrorNotice';
import type { Capabilities, Confirmation, ConfirmationRevisionAction, JsonSchemaProperty, ResourceDetail, ResourceReference } from './types';

type CatalogResource = { name: string; datasetId: string; versionId: string };

function numericParameter(schema: JsonSchemaProperty) {
  const types = Array.isArray(schema.type) ? schema.type : [schema.type];
  const kind = types.find(type => type === 'number' || type === 'integer');
  return kind && types.every(type => type === kind || type === 'null')
    ? { kind, nullable: types.includes('null') } : null;
}

function numericError(schema: JsonSchemaProperty, value: unknown, required: boolean): string | null {
  const numeric = numericParameter(schema);
  if (!numeric) return null;
  if (value === undefined) return required ? '请输入必填数值。' : null;
  if (value === null && numeric.nullable) return null;
  if (typeof value !== 'number' || !Number.isFinite(value)) return '请输入有限数值。';
  if (numeric.kind === 'integer' && !Number.isInteger(value)) return '请输入整数。';
  if (schema.minimum !== undefined && value < schema.minimum) return `数值不能小于 ${schema.minimum}。`;
  if (schema.maximum !== undefined && value > schema.maximum) return `数值不能大于 ${schema.maximum}。`;
  if (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum) return `数值必须大于 ${schema.exclusiveMinimum}。`;
  if (schema.exclusiveMaximum !== undefined && value >= schema.exclusiveMaximum) return `数值必须小于 ${schema.exclusiveMaximum}。`;
  return null;
}

function selectableReference(version: ResourceDetail): ResourceReference | null {
  const raw = version.objects.filter(item => item.role === 'raw');
  if (raw.length !== 1) return null;
  const [object] = raw;
  return {
    kind: 'data_version', service: 'data-primary', datasetId: version.datasetId,
    versionId: version.versionId, semanticId: version.semanticId, objectId: object.objectId,
    checksum: object.checksum, mediaType: object.mediaType, format: object.format, sizeBytes: object.size,
  };
}

type CatalogRequest = { query: string; cursors: string[] };

type ResourceLoadState = {
  request: CatalogRequest;
  status: 'loading' | 'ready' | 'error';
  resources: CatalogResource[];
  nextCursor: string | null;
  error: ApiError | string | null;
  missingCurrentVersions: number;
};

function loadingResources(request: CatalogRequest): ResourceLoadState {
  return {
    request, status: 'loading', resources: [], nextCursor: null, error: null,
    missingCurrentVersions: 0,
  };
}

function resourceError(error: unknown): ApiError | string {
  return error instanceof ApiError ? error : '读取可选数据版本时发生未知错误。';
}

type ConfirmationEditorProps = {
  confirmation: Confirmation; capabilities: Capabilities; project: string; busy: boolean;
  focusField?: string | null;
  stale?: boolean;
  suggestedValue?: string;
  onSave: (actions: ConfirmationRevisionAction[]) => void; onClose: () => void;
};

export function ConfirmationEditor(props: ConfirmationEditorProps) {
  return <ConfirmationEditorForm key={`${props.project}:${props.confirmation.confirmationId}`} {...props} />;
}

function ConfirmationEditorForm({ confirmation, capabilities, busy, focusField, suggestedValue, onSave, onClose, stale }: ConfirmationEditorProps) {
  const [values, setValues] = useState(() => confirmation.actions.map(action => structuredClone(action.arguments)));
  const [expectedReferences, setExpectedReferences] = useState<Record<string, ResourceReference>[]>(
    () => confirmation.actions.map(() => ({})),
  );
  const requiresResources = confirmation.actions.some(action => (
    capabilities.models.find(item => item.modelId === action.modelId)?.dataInputParameters?.length ?? 0
  ) > 0);
  const [resourceRetry, setResourceRetry] = useState(0);
  const [catalogRequest, setCatalogRequest] = useState<CatalogRequest>({ query: '', cursors: [''] });
  const [draftErrors, setDraftErrors] = useState<Record<string, string>>({});
  const [resourceLoad, setResourceLoad] = useState<ResourceLoadState>(() => (
    requiresResources ? loadingResources(catalogRequest) : { ...loadingResources(catalogRequest), status: 'ready' }
  ));
  const currentResourceLoad = resourceLoad.request === catalogRequest
    ? resourceLoad
    : loadingResources(catalogRequest);
  const nextCursor = currentResourceLoad.nextCursor;
  useEffect(() => {
    if (!requiresResources) {
      setResourceLoad({ ...loadingResources(catalogRequest), status: 'ready' });
      return;
    }
    const controller = new AbortController();
    const cursor = catalogRequest.cursors.at(-1);
    const params = new URLSearchParams({ limit: '50' });
    if (catalogRequest.query) params.set('query', catalogRequest.query);
    if (cursor) params.set('cursor', cursor);
    setResourceLoad(loadingResources(catalogRequest));
    void (async () => {
      try {
        const page = await callApi(
          'data_list_datasets', `/data/datasets?${params}`, undefined, controller.signal,
        );
        if (controller.signal.aborted) return;
        const resources = page.items.flatMap(item => item.currentVersion
          ? [{ datasetId: item.datasetId, versionId: item.currentVersion, name: item.title }]
          : []);
        setResourceLoad({
          request: catalogRequest, status: 'ready', resources, nextCursor: page.nextCursor, error: null,
          missingCurrentVersions: page.items.length - resources.length,
        });
      } catch (caught) {
        if (controller.signal.aborted || caught instanceof DOMException && caught.name === 'AbortError') return;
        setResourceLoad({
          request: catalogRequest, status: 'error', resources: [], nextCursor: null, error: resourceError(caught),
          missingCurrentVersions: 0,
        });
      }
    })();
    return () => controller.abort();
  }, [catalogRequest, requiresResources, resourceRetry]);
  useEffect(() => {
    if (!focusField) return;
    if (suggestedValue !== undefined) {
      setValues(previous => previous.map((action, index) => (
        Object.hasOwn(confirmation.actions[index].arguments, focusField) &&
        !capabilities.models.find(model => model.modelId === confirmation.actions[index].modelId)?.dataInputParameters?.includes(focusField)
          ? { ...action, [focusField]: suggestedValue }
          : action
      )));
    }
    requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-parameter-field="${CSS.escape(focusField)}"] input,[data-parameter-field="${CSS.escape(focusField)}"] select,[data-parameter-field="${CSS.escape(focusField)}"] textarea`)?.focus());
  }, [capabilities.models, confirmation, focusField, suggestedValue]);
  const changes = useMemo(() => confirmation.actions.flatMap((action, actionIndex) => Object.keys(values[actionIndex]).filter(name => JSON.stringify(values[actionIndex][name]) !== JSON.stringify(action.arguments[name])).map(name => ({ actionIndex, name, before: action.arguments[name], after: values[actionIndex][name] }))), [confirmation, values]);
  const updateDraftError = useCallback((field: string, error: string | null) => {
    setDraftErrors(previous => {
      if (error) return previous[field] === error ? previous : { ...previous, [field]: error };
      if (!Object.hasOwn(previous, field)) return previous;
      const next = { ...previous };
      delete next[field];
      return next;
    });
  }, []);
  const numericErrors = new Map<string, string>();
  confirmation.actions.forEach((action, index) => {
    const model = capabilities.models.find(item => item.modelId === action.modelId);
    for (const [name, schema] of Object.entries(model?.inputSchema?.properties ?? {})) {
      if (model?.dataInputParameters?.includes(name)) continue;
      const error = numericError(schema, values[index][name], model?.inputSchema?.required?.includes(name) ?? false);
      if (error) numericErrors.set(`${action.toolCallId}:${name}`, error);
    }
  });
  const hasUnresolvedSelection = changes.some(({ actionIndex, name }) => (
    capabilities.models.find(model => model.modelId === confirmation.actions[actionIndex].modelId)
      ?.dataInputParameters?.includes(name) && !Object.hasOwn(expectedReferences[actionIndex], name)
  ));
  const canSave = !busy && !stale && changes.length > 0 && !Object.keys(draftErrors).length
    && !numericErrors.size && !hasUnresolvedSelection;
  function update(actionIndex: number, name: string, value: unknown, reference?: ResourceReference | null) {
    setValues(previous => previous.map((action, index) => index === actionIndex ? { ...action, [name]: value } : action));
    if (reference === undefined) return;
    setExpectedReferences(previous => previous.map((references, index) => {
      if (index !== actionIndex) return references;
      const next = { ...references };
      if (reference === null) delete next[name];
      else next[name] = reference;
      return next;
    }));
  }
  function saveRevision() {
    if (!canSave) return;
    onSave(confirmation.actions.map((action, index) => ({
      toolCallId: action.toolCallId,
      arguments: values[index],
      ...(Object.keys(expectedReferences[index]).length > 0 && { expectedInputReferences: expectedReferences[index] }),
    })));
  }
  return <section className="confirmation-editor" data-testid="confirmation-editor"><div className="workspace-section-heading"><div><span className="workspace-kicker">确认修订 {(confirmation.revision ?? 1) + 1}</span><h3>{confirmation.kind === 'RECOVERY' ? '编辑恢复批次参数' : '编辑执行参数'}</h3></div><button className="text-button" onClick={onClose}>关闭</button></div>
    {stale && <p className="limitations" role="status">此批次已更新。当前编辑仍保留，请核对最新确认批次。</p>}
    <p className="muted">保存会创建不可变的新修订。执行动作和顺序保持不变。</p>
    {requiresResources && <>
      <form className="artifact-toolbar" onSubmit={event => {
        event.preventDefault();
        const query = String(new FormData(event.currentTarget).get('datasetQuery') ?? '').trim();
        setCatalogRequest({ query, cursors: [''] });
      }}>
        <label>搜索数据集<input type="search" name="datasetQuery" maxLength={500} /></label>
        <button type="submit">搜索</button>
      </form>
      <ResourceLoadStatus state={currentResourceLoad} onRetry={() => setResourceRetry(value => value + 1)} />
      {(catalogRequest.cursors.length > 1 || nextCursor) && <div className="artifact-toolbar">
        {catalogRequest.cursors.length > 1 && <button type="button" onClick={() => setCatalogRequest({ ...catalogRequest, cursors: catalogRequest.cursors.slice(0, -1) })}>上一页数据集</button>}
        {nextCursor && <button type="button" onClick={() => setCatalogRequest({ ...catalogRequest, cursors: [...catalogRequest.cursors, nextCursor] })}>下一页数据集</button>}
      </div>}
    </>}
    {confirmation.actions.map((action, actionIndex) => {
      const model = capabilities.models.find(item => item.modelId === action.modelId);
      const properties = model?.inputSchema?.properties ?? {};
      const required = new Set(model?.inputSchema?.required ?? []);
      const groups = Object.entries(properties).reduce<Record<string, [string, JsonSchemaProperty][]>>((all, entry) => {
        const group = argumentGroup(entry[0], action.modelId);
        (all[group] ??= []).push(entry);
        return all;
      }, {});
      return <article className="editor-action" key={action.toolCallId}>
        <h4>{actionIndex + 1}. {modelName(action.modelId)}</h4>
        {Object.entries(groups).map(([group, fields]) => <details key={group} open={group === '基本参数' || fields.some(([name]) => name === focusField)}>
          <summary>{group}</summary>
          <div className="schema-fields">{fields.map(([name, schema]) => <SchemaField
            key={name} fieldKey={`${action.toolCallId}:${name}`} name={name} modelId={action.modelId}
            schema={schema} value={values[actionIndex][name]} originalValue={action.arguments[name]}
            required={required.has(name)} resource={model?.dataInputParameters?.includes(name) ?? false}
            resources={currentResourceLoad.resources} frozenReference={action.inputReferences?.[name]} resourceDisabled={currentResourceLoad.status !== 'ready'}
            fieldError={draftErrors[`${action.toolCallId}:${name}`] ?? numericErrors.get(`${action.toolCallId}:${name}`)}
            onDraftError={updateDraftError} onChange={(value, reference) => update(actionIndex, name, value, reference)}
          />)}</div>
        </details>)}
      </article>;
    })}
    <section className="diff-preview"><h4>字段变化 · {changes.length}</h4>{changes.length ? <dl>{changes.map(change => <div key={`${change.actionIndex}:${change.name}`}><dt>{argumentLabel(change.name, confirmation.actions[change.actionIndex].modelId)}</dt><dd><del>{String(change.before ?? '∅')}</del><ins>{String(change.after ?? '∅')}</ins></dd></div>)}</dl> : <p className="muted">尚未修改参数。</p>}</section>
    <div className="actions"><button className="primary" disabled={!canSave} onClick={saveRevision}>保存为新修订</button><button onClick={onClose}>取消</button></div>
  </section>;
}

function ResourceLoadStatus({ state, onRetry }: { state: ResourceLoadState; onRetry: () => void }) {
  if (state.status === 'loading') return <p className="resource-load-state muted" role="status">正在加载可选数据版本…</p>;
  if (state.status === 'error') return <div className="resource-load-state error-banner">
    <ErrorNotice error={state.error ?? '可选数据版本加载失败。'} title="可选数据版本加载失败" />
    <button type="button" onClick={onRetry}>重新加载数据版本</button>
  </div>;
  return <>
    {state.missingCurrentVersions > 0 && <p className="resource-load-state limitations" role="status">{state.missingCurrentVersions} 个数据集没有当前版本，不能作为本次输入。</p>}
    {state.resources.length === 0 && state.missingCurrentVersions === 0 && <p className="resource-load-state muted" role="status">{state.request.query ? '没有匹配的数据版本。' : '当前项目没有可选的数据版本。'}</p>}
  </>;
}

function resourceVersionId(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object' && 'version_id' in value && typeof value.version_id === 'string') return value.version_id;
  return '';
}

type ResourceLookup = {
  versionId: string;
  datasetId?: string;
};

type ResourceRead = ResourceLookup & (
  { status: 'loading' | 'ineligible' }
  | { status: 'error'; error: ApiError | string }
  | { status: 'ready'; detail: ResourceDetail }
);

function ResourceSchemaField({ name, label, value, originalValue, resources, frozenReference, disabled, onChange }: {
  name: string;
  label: React.ReactNode;
  value: unknown;
  originalValue: unknown;
  resources: CatalogResource[];
  frozenReference?: ResourceReference;
  disabled: boolean;
  onChange: (value: unknown, ref?: ResourceReference | null) => void;
}) {
  const selected = resourceVersionId(value);
  const original = resourceVersionId(originalValue);
  const [read, setRead] = useState<ResourceRead | null>(null);
  const request = useRef<AbortController | null>(null);
  const currentRead = read?.versionId === selected ? read : null;
  useEffect(() => () => request.current?.abort(), []);

  async function verify({ versionId, datasetId }: ResourceLookup) {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setRead({ status: 'loading', versionId, datasetId });
    try {
      const expected = versionId === original ? frozenReference : undefined;
      const detail = await readDataVersion(versionId, controller.signal, expected);
      if (controller.signal.aborted) return;
      if (datasetId && detail.datasetId !== datasetId) throw new ApiError(409, 'DATA_VERSION_IDENTITY_MISMATCH');
      if (versionId !== original) {
        const reference = selectableReference(detail);
        if (!reference) {
          setRead({ status: 'ineligible', versionId, datasetId });
          return;
        }
        onChange(versionId, reference);
      }
      setRead({ status: 'ready', versionId, datasetId, detail });
    } catch (error) {
      if (!controller.signal.aborted) setRead({ status: 'error', error: resourceError(error), versionId, datasetId });
    }
  }

  function select(versionId: string) {
    request.current?.abort();
    setRead(null);
    onChange(versionId === original ? originalValue : versionId, null);
    const option = resources.find(item => item.versionId === versionId);
    if (option && versionId !== original) void verify(option);
  }

  const detail = currentRead?.status === 'ready' ? currentRead.detail : null;
  return <div data-parameter-field={name}>
    <label>{label}
      <select disabled={disabled} value={selected} onChange={event => select(event.currentTarget.value)}>
        <option value="">选择数据版本</option>
        {selected && !resources.some(item => item.versionId === selected) && <option value={selected}>
          {selected === original ? '当前冻结版本' : '已选择版本'} · {selected}
        </option>}
        {original && selected !== original && !resources.some(item => item.versionId === original) && <option value={original}>当前冻结版本 · {original}</option>}
        {resources.map(({ name, versionId }) => <option key={versionId} value={versionId}>{name} · {versionId}</option>)}
      </select>
    </label>
    {selected && selected === original && !currentRead && <button type="button" className="text-button"
      onClick={() => void verify({ versionId: selected })}>查看当前冻结版本详情</button>}
    {currentRead?.status === 'loading' && <p className="muted" role="status">正在核验所选数据版本…</p>}
    {currentRead?.status === 'ineligible' && <p className="limitations" role="alert">该版本没有唯一的原始对象，不能作为本次输入。请选择其他数据版本。</p>}
    {currentRead?.status === 'error' && <div className="workbench-read-error">
      <ErrorNotice error={currentRead.error} title="所选数据版本核验失败" />
      <button type="button" onClick={() => void verify(currentRead)}>重新核验所选版本</button>
    </div>}
    {detail && <div className="selected-data-facts">
      <strong>{detail.title}</strong>
      <p>时间范围 · {detail.semantics.timeRange ?? '未提供'}</p>
      {detail.semantics.crs && <p>坐标参考系 · {detail.semantics.crs}</p>}
      <details><summary>字段单位、缺测与样例</summary>
        <DataFieldQuality descriptor={detail.descriptor} semantics={detail.semantics} />
      </details>
    </div>}
  </div>;
}

function SchemaField({ fieldKey, name, modelId, schema, value, originalValue, required, resource, resources, frozenReference, resourceDisabled, fieldError, onDraftError, onChange }: { fieldKey: string; name: string; modelId: string; schema: JsonSchemaProperty; value: unknown; originalValue: unknown; required: boolean; resource: boolean; resources: CatalogResource[]; frozenReference?: ResourceReference; resourceDisabled: boolean; fieldError?: string; onDraftError: (field: string, error: string | null) => void; onChange: (value: unknown, reference?: ResourceReference | null) => void }) {
  const errorId = useId();
  const presentation = parameterPresentation(name, modelId);
  const label = <span>{presentation.label}{presentation.unit ? `（${presentation.unit}）` : ''}{required && <b className="required"> *</b>}{schema.default !== undefined && <small> 默认 {String(schema.default)}</small>}</span>;
  if (resource) return <ResourceSchemaField name={name} label={label} value={value} originalValue={originalValue}
    resources={resources} frozenReference={frozenReference} disabled={resourceDisabled} onChange={onChange} />;
  const numeric = numericParameter(schema);
  if (schema.enum) return <label data-parameter-field={name}>{label}<select value={String(value ?? '')} onChange={event => {
    const selected = event.target.value;
    onChange(schema.enum?.find(item => String(item) === selected));
  }}>{schema.enum.map(item => <option key={String(item)} value={String(item)}>{presentation.values?.[String(item)] ?? String(item)}</option>)}</select></label>;
  if (numeric) return <label data-parameter-field={name}>{label}
    <input type="number" min={schema.minimum} max={schema.maximum} step={numeric.kind === 'integer' ? 1 : 'any'}
      required={required && !numeric.nullable} aria-invalid={Boolean(fieldError)} aria-describedby={fieldError ? errorId : undefined}
      value={typeof value === 'number' && Number.isFinite(value) ? value : ''}
      onChange={event => {
        const { value, valueAsNumber, validity } = event.currentTarget;
        onDraftError(fieldKey, validity.badInput ? '请输入有效数值。' : null);
        onChange(value === '' ? numeric.nullable ? null : undefined : valueAsNumber);
      }} />
    <small>原值：{String(originalValue ?? '∅')}</small>
    {fieldError && <small className="field-error" id={errorId} role="alert">{fieldError}</small>}
  </label>;
  if (schema.type === 'boolean') return <label data-parameter-field={name}>{label}<select value={String(value)} onChange={event => onChange(event.target.value === 'true')}><option value="true">是</option><option value="false">否</option></select></label>;
  if (schema.type === 'object' || schema.type === 'array') return <JsonSchemaField fieldKey={fieldKey} name={name} type={schema.type} value={value} label={label} onJsonError={onDraftError} onChange={onChange} />;
  return <label data-parameter-field={name}>{label}<input value={typeof value === 'string' ? value : ''} onChange={event => onChange(event.target.value)} placeholder={schema.description} /></label>;
}

function jsonDraftError(raw: string, type: 'object' | 'array'): { parsed?: unknown; error: string | null } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { error: 'JSON 格式无效，请检查引号、逗号和括号。' };
  }
  if (type === 'array' && !Array.isArray(parsed)) return { error: '请输入 JSON 数组。' };
  if (type === 'object' && (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed))) return { error: '请输入 JSON 对象。' };
  return { parsed, error: null };
}

function JsonSchemaField({ fieldKey, name, type, value, label, onJsonError, onChange }: {
  fieldKey: string; name: string; type: 'object' | 'array'; value: unknown; label: React.ReactNode;
  onJsonError: (field: string, error: string | null) => void; onChange: (value: unknown) => void;
}) {
  const errorId = useId();
  const serializedValue = JSON.stringify(value);
  const [raw, setRaw] = useState(() => JSON.stringify(value, null, 2) ?? '');
  const [error, setError] = useState<string | null>(null);
  const lastCommitted = useRef(serializedValue);

  useEffect(() => {
    if (serializedValue === lastCommitted.current) return;
    lastCommitted.current = serializedValue;
    setRaw(JSON.stringify(value, null, 2) ?? '');
    setError(null);
    onJsonError(fieldKey, null);
  }, [fieldKey, onJsonError, serializedValue, value]);

  function validate(nextRaw: string) {
    const result = jsonDraftError(nextRaw, type);
    setError(result.error);
    onJsonError(fieldKey, result.error);
    if (result.error === null) {
      lastCommitted.current = JSON.stringify(result.parsed);
      onChange(result.parsed);
    }
  }

  return <label data-parameter-field={name}>{label}
    <textarea
      value={raw}
      aria-invalid={error !== null}
      aria-describedby={error ? errorId : undefined}
      onChange={event => { setRaw(event.target.value); validate(event.target.value); }}
      onBlur={() => validate(raw)}
    />
    {error && <small className="field-error" id={errorId} role="alert">{error}</small>}
  </label>;
}
