import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { ApiError, callApi } from './api';
import { decodeResourceDetail } from './api-contract';
import { argumentGroup, argumentLabel, modelName, parameterPresentation } from './card-presentation';
import { ErrorNotice } from './ErrorNotice';
import type { Capabilities, Confirmation, JsonSchemaProperty, ResourceDetail, ResourceItem } from './types';

function selectableResource(version: ResourceDetail): ResourceItem | null {
  const object = version.objects.find(item => item.role === 'raw');
  if (!object) return null;
  return {
    kind: 'data_version', service: 'data-primary', datasetId: version.datasetId,
    versionId: version.versionId, semanticId: version.semanticId, objectId: object.objectId,
    checksum: object.checksum, mediaType: object.mediaType, format: object.format,
    sizeBytes: object.size, familyId: version.datasetId, name: version.title,
  };
}

type ResourceLoadState = {
  project: string;
  status: 'loading' | 'ready' | 'error';
  resources: ResourceItem[];
  error: ApiError | string | null;
  failedVersions: number;
  missingCurrentVersions: number;
  missingRawVersions: number;
};

function loadingResources(project: string): ResourceLoadState {
  return {
    project, status: 'loading', resources: [], error: null,
    failedVersions: 0, missingCurrentVersions: 0, missingRawVersions: 0,
  };
}

function resourceError(error: unknown): ApiError | string {
  return error instanceof ApiError ? error : '读取可选数据版本时发生未知错误。';
}

export function ConfirmationEditor({ confirmation, capabilities, project, busy, focusField, suggestedValue, onSave, onClose }: {
  confirmation: Confirmation; capabilities: Capabilities; project: string; busy: boolean;
  focusField?: string | null;
  suggestedValue?: string;
  onSave: (actions: { toolCallId: string; arguments: Record<string, unknown> }[]) => void; onClose: () => void;
}) {
  const [values, setValues] = useState(() => confirmation.actions.map(action => structuredClone(action.arguments)));
  const requiresResources = confirmation.actions.some(action => (
    capabilities.models.find(item => item.modelId === action.modelId)?.dataInputParameters?.length ?? 0
  ) > 0);
  const [resourceRetry, setResourceRetry] = useState(0);
  const [jsonErrors, setJsonErrors] = useState<Record<string, string>>({});
  const [resourceLoad, setResourceLoad] = useState<ResourceLoadState>(() => (
    requiresResources ? loadingResources(project) : { ...loadingResources(project), status: 'ready' }
  ));
  const currentResourceLoad = resourceLoad.project === project
    ? resourceLoad
    : loadingResources(project);
  useEffect(() => {
    if (!requiresResources) {
      setResourceLoad({ ...loadingResources(project), status: 'ready' });
      return;
    }
    const controller = new AbortController();
    setResourceLoad(loadingResources(project));
    void (async () => {
      try {
        const page = await callApi(
          'data_list_datasets', '/data/datasets?limit=50', undefined, controller.signal,
        );
        const versionIds = page.items
          .map(item => item.currentVersion)
          .filter((versionId): versionId is string => typeof versionId === 'string' && versionId.length > 0);
        const missingCurrentVersions = page.items.length - versionIds.length;
        const versions = await Promise.allSettled(versionIds.map(versionId => callApi(
          'data_get_version',
          `/data/versions/${encodeURIComponent(versionId)}`,
          undefined, controller.signal, decodeResourceDetail,
        )));
        if (controller.signal.aborted) return;
        const resources: ResourceItem[] = [];
        let failedVersions = 0;
        let missingRawVersions = 0;
        let firstFailure: unknown = null;
        versions.forEach(version => {
          if (version.status === 'rejected') {
            failedVersions += 1;
            firstFailure ??= version.reason;
            return;
          }
          const resource = selectableResource(version.value);
          if (resource) resources.push(resource);
          else missingRawVersions += 1;
        });
        if (failedVersions > 0 && resources.length === 0 && missingRawVersions === 0) {
          setResourceLoad({
            project, status: 'error', resources: [], error: resourceError(firstFailure),
            failedVersions, missingCurrentVersions, missingRawVersions,
          });
          return;
        }
        setResourceLoad({
          project, status: 'ready', resources, error: null,
          failedVersions, missingCurrentVersions, missingRawVersions,
        });
      } catch (caught) {
        if (controller.signal.aborted || caught instanceof DOMException && caught.name === 'AbortError') return;
        setResourceLoad({
          project, status: 'error', resources: [], error: resourceError(caught),
          failedVersions: 0, missingCurrentVersions: 0, missingRawVersions: 0,
        });
      }
    })();
    return () => controller.abort();
  }, [project, requiresResources, resourceRetry]);
  useEffect(() => {
    if (!focusField) return;
    if (suggestedValue !== undefined) {
      setValues(previous => previous.map((action, index) => (
        Object.hasOwn(confirmation.actions[index].arguments, focusField)
          ? { ...action, [focusField]: suggestedValue }
          : action
      )));
    }
    requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-parameter-field="${CSS.escape(focusField)}"] input,[data-parameter-field="${CSS.escape(focusField)}"] select,[data-parameter-field="${CSS.escape(focusField)}"] textarea`)?.focus());
  }, [confirmation, focusField, suggestedValue]);
  const changes = useMemo(() => confirmation.actions.flatMap((action, actionIndex) => Object.keys(values[actionIndex]).filter(name => JSON.stringify(values[actionIndex][name]) !== JSON.stringify(action.arguments[name])).map(name => ({ actionIndex, name, before: action.arguments[name], after: values[actionIndex][name] }))), [confirmation, values]);
  const hasJsonErrors = Object.keys(jsonErrors).length > 0;
  const updateJsonError = useCallback((field: string, error: string | null) => {
    setJsonErrors(previous => {
      if (error) return previous[field] === error ? previous : { ...previous, [field]: error };
      if (!Object.hasOwn(previous, field)) return previous;
      const next = { ...previous };
      delete next[field];
      return next;
    });
  }, []);
  function update(actionIndex: number, name: string, value: unknown) { setValues(previous => previous.map((action, index) => index === actionIndex ? { ...action, [name]: value } : action)); }
  return <section className="confirmation-editor" data-testid="confirmation-editor"><div className="workspace-section-heading"><div><span className="workspace-kicker">确认修订 {(confirmation.revision ?? 1) + 1}</span><h3>{confirmation.kind === 'RECOVERY' ? '编辑恢复批次参数' : '编辑执行参数'}</h3></div><button className="text-button" onClick={onClose}>关闭</button></div>
    <p className="muted">保存会创建不可变的新修订。执行动作和顺序保持不变。</p>
    {requiresResources && <ResourceLoadStatus state={currentResourceLoad} onRetry={() => setResourceRetry(value => value + 1)} />}
    {confirmation.actions.map((action, actionIndex) => { const model = capabilities.models.find(item => item.modelId === action.modelId); const properties = model?.inputSchema?.properties ?? {}; const required = new Set(model?.inputSchema?.required ?? []); const groups = Object.entries(properties).reduce<Record<string, [string, JsonSchemaProperty][]>>((all, entry) => { const group = argumentGroup(entry[0], action.modelId); (all[group] ??= []).push(entry); return all; }, {}); return <article className="editor-action" key={action.toolCallId}><h4>{actionIndex + 1}. {modelName(action.modelId)}</h4>{Object.entries(groups).map(([group, fields]) => <details key={group} open={group === '基本参数' || fields.some(([name]) => name === focusField)}><summary>{group}</summary><div className="schema-fields">{fields.map(([name, schema]) => <SchemaField key={name} fieldKey={`${action.toolCallId}:${name}`} name={name} modelId={action.modelId} schema={schema} value={values[actionIndex][name]} required={required.has(name)} resource={model?.dataInputParameters?.includes(name) ?? false} resources={currentResourceLoad.resources} resourceDisabled={currentResourceLoad.status !== 'ready'} onJsonError={updateJsonError} onChange={value => update(actionIndex, name, value)} />)}</div></details>)}</article>; })}
    <section className="diff-preview"><h4>字段变化 · {changes.length}</h4>{changes.length ? <dl>{changes.map(change => <div key={`${change.actionIndex}:${change.name}`}><dt>{argumentLabel(change.name, confirmation.actions[change.actionIndex].modelId)}</dt><dd><del>{String(change.before ?? '∅')}</del><ins>{String(change.after ?? '∅')}</ins></dd></div>)}</dl> : <p className="muted">尚未修改参数。</p>}</section>
    <div className="actions"><button className="primary" disabled={busy || changes.length === 0 || hasJsonErrors} onClick={() => { if (!hasJsonErrors) onSave(confirmation.actions.map((action, index) => ({ toolCallId: action.toolCallId, arguments: values[index] }))); }}>保存为新修订</button><button onClick={onClose}>取消</button></div>
  </section>;
}

function ResourceLoadStatus({ state, onRetry }: { state: ResourceLoadState; onRetry: () => void }) {
  if (state.status === 'loading') return <p className="resource-load-state muted" role="status">正在加载可选数据版本…</p>;
  if (state.status === 'error') return <div className="resource-load-state error-banner">
    <ErrorNotice error={state.error ?? '可选数据版本加载失败。'} title="可选数据版本加载失败" />
    <button type="button" onClick={onRetry}>重新加载数据版本</button>
  </div>;
  return <>
    {state.failedVersions > 0 && <div className="resource-load-state error-banner" role="alert"><span>{state.failedVersions} 个数据版本详情加载失败；已显示其余可用版本。</span><button type="button" onClick={onRetry}>重新加载数据版本</button></div>}
    {state.missingCurrentVersions > 0 && <p className="resource-load-state limitations" role="status">{state.missingCurrentVersions} 个数据集没有当前版本，不能作为本次输入。</p>}
    {state.missingRawVersions > 0 && <p className="resource-load-state limitations" role="status">{state.missingRawVersions} 个数据版本没有原始对象，不能作为本次输入。</p>}
    {state.resources.length === 0 && state.failedVersions === 0 && state.missingCurrentVersions === 0 && state.missingRawVersions === 0 && <p className="resource-load-state muted" role="status">当前项目没有可选的数据版本。</p>}
  </>;
}

function SchemaField({ fieldKey, name, modelId, schema, value, required, resource, resources, resourceDisabled, onJsonError, onChange }: { fieldKey: string; name: string; modelId: string; schema: JsonSchemaProperty; value: unknown; required: boolean; resource: boolean; resources: ResourceItem[]; resourceDisabled: boolean; onJsonError: (field: string, error: string | null) => void; onChange: (value: unknown) => void }) {
  const presentation = parameterPresentation(name, modelId);
  const label = <span>{presentation.label}{presentation.unit ? `（${presentation.unit}）` : ''}{required && <b className="required"> *</b>}{schema.default !== undefined && <small> 默认 {String(schema.default)}</small>}</span>;
  if (resource) {
    const selected = typeof value === 'string' ? value : '';
    const selectedLoaded = resources.some(item => item.versionId === selected);
    return <label data-parameter-field={name}>{label}<select disabled={resourceDisabled} value={selected} onChange={event => onChange(event.target.value)}><option value="">选择数据版本</option>{selected && !selectedLoaded && <option value={selected}>当前冻结版本 · {selected}</option>}{resources.map(item => <option key={item.versionId} value={item.versionId}>{item.name} · {item.versionId}</option>)}</select></label>;
  }
  if (schema.enum) return <label data-parameter-field={name}>{label}<select value={String(value ?? '')} onChange={event => onChange(event.target.value)}>{schema.enum.map(item => <option key={String(item)} value={String(item)}>{presentation.values?.[String(item)] ?? String(item)}</option>)}</select></label>;
  if (schema.type === 'number' || schema.type === 'integer') return <label data-parameter-field={name}>{label}<input type="number" min={schema.minimum} max={schema.maximum} step={schema.type === 'integer' ? 1 : 'any'} value={typeof value === 'number' ? value : ''} onChange={event => onChange(event.target.value === '' ? undefined : Number(event.target.value))} /><small>原值：{String(value ?? '∅')}</small></label>;
  if (schema.type === 'boolean') return <label data-parameter-field={name}>{label}<select value={String(value)} onChange={event => onChange(event.target.value === 'true')}><option value="true">是</option><option value="false">否</option></select></label>;
  if (schema.type === 'object' || schema.type === 'array') return <JsonSchemaField fieldKey={fieldKey} name={name} type={schema.type} value={value} label={label} onJsonError={onJsonError} onChange={onChange} />;
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
