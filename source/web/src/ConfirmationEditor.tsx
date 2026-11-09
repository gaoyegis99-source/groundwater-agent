import { useEffect, useMemo, useState } from 'react';
import { callApi } from './api';
import { decodeResourceDetail } from './api-contract';
import { argumentGroup, argumentLabel, modelName, parameterPresentation } from './card-presentation';
import type { Capabilities, Confirmation, JsonSchemaProperty, ResourceDetail, ResourceItem } from './types';

function selectableResource(version: ResourceDetail): ResourceItem | null {
  const object = version.objects.find(item => item.role === 'raw') ?? version.objects[0];
  if (!object) return null;
  return {
    kind: 'data_version', service: 'data-primary', datasetId: version.datasetId,
    versionId: version.versionId, semanticId: version.semanticId, objectId: object.objectId,
    checksum: object.checksum, mediaType: object.mediaType, format: object.format,
    sizeBytes: object.size, familyId: version.datasetId, name: version.title,
  };
}

export function ConfirmationEditor({ confirmation, capabilities, project, busy, focusField, suggestedValue, onSave, onClose }: {
  confirmation: Confirmation; capabilities: Capabilities; project: string; busy: boolean;
  focusField?: string | null;
  suggestedValue?: string;
  onSave: (actions: { toolCallId: string; arguments: Record<string, unknown> }[]) => void; onClose: () => void;
}) {
  const [values, setValues] = useState(() => confirmation.actions.map(action => structuredClone(action.arguments)));
  const [resources, setResources] = useState<ResourceItem[]>([]);
  useEffect(() => {
    void callApi('data_list_datasets', '/data/datasets?limit=50').then(async page => {
      const versions = await Promise.all(page.items.map(item => callApi('data_get_version',
        `/data/versions/${encodeURIComponent(item.currentVersion)}`,
        undefined, undefined, decodeResourceDetail,
      )));
      setResources(versions.map(selectableResource).filter((item): item is ResourceItem => item !== null));
    });
  }, [project]);
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
  function update(actionIndex: number, name: string, value: unknown) { setValues(previous => previous.map((action, index) => index === actionIndex ? { ...action, [name]: value } : action)); }
  return <section className="confirmation-editor" data-testid="confirmation-editor"><div className="workspace-section-heading"><div><span className="workspace-kicker">确认修订 {(confirmation.revision ?? 1) + 1}</span><h3>{confirmation.kind === 'RECOVERY' ? '编辑恢复批次参数' : '编辑执行参数'}</h3></div><button className="text-button" onClick={onClose}>关闭</button></div>
    <p className="muted">保存会创建不可变的新修订。执行动作和顺序保持不变。</p>
    {confirmation.actions.map((action, actionIndex) => { const model = capabilities.models.find(item => item.modelId === action.modelId); const properties = model?.inputSchema?.properties ?? {}; const required = new Set(model?.inputSchema?.required ?? []); const groups = Object.entries(properties).reduce<Record<string, [string, JsonSchemaProperty][]>>((all, entry) => { const group = argumentGroup(entry[0], action.modelId); (all[group] ??= []).push(entry); return all; }, {}); return <article className="editor-action" key={action.toolCallId}><h4>{actionIndex + 1}. {modelName(action.modelId)}</h4>{Object.entries(groups).map(([group, fields]) => <details key={group} open={group === '基本参数' || fields.some(([name]) => name === focusField)}><summary>{group}</summary><div className="schema-fields">{fields.map(([name, schema]) => <SchemaField key={name} name={name} modelId={action.modelId} schema={schema} value={values[actionIndex][name]} required={required.has(name)} resource={model?.dataInputParameters?.includes(name) ?? false} resources={resources} onChange={value => update(actionIndex, name, value)} />)}</div></details>)}</article>; })}
    <section className="diff-preview"><h4>字段变化 · {changes.length}</h4>{changes.length ? <dl>{changes.map(change => <div key={`${change.actionIndex}:${change.name}`}><dt>{argumentLabel(change.name, confirmation.actions[change.actionIndex].modelId)}</dt><dd><del>{String(change.before ?? '∅')}</del><ins>{String(change.after ?? '∅')}</ins></dd></div>)}</dl> : <p className="muted">尚未修改参数。</p>}</section>
    <div className="actions"><button className="primary" disabled={busy || changes.length === 0} onClick={() => onSave(confirmation.actions.map((action, index) => ({ toolCallId: action.toolCallId, arguments: values[index] })))}>保存为新修订</button><button onClick={onClose}>取消</button></div>
  </section>;
}

function SchemaField({ name, modelId, schema, value, required, resource, resources, onChange }: { name: string; modelId: string; schema: JsonSchemaProperty; value: unknown; required: boolean; resource: boolean; resources: ResourceItem[]; onChange: (value: unknown) => void }) {
  const presentation = parameterPresentation(name, modelId);
  const label = <span>{presentation.label}{presentation.unit ? `（${presentation.unit}）` : ''}{required && <b className="required"> *</b>}{schema.default !== undefined && <small> 默认 {String(schema.default)}</small>}</span>;
  if (resource) return <label data-parameter-field={name}>{label}<select value={typeof value === 'string' ? value : ''} onChange={event => onChange(event.target.value)}><option value="">选择数据版本</option>{resources.map(item => <option key={item.versionId} value={item.versionId}>{item.name} · {item.versionId}</option>)}</select></label>;
  if (schema.enum) return <label data-parameter-field={name}>{label}<select value={String(value ?? '')} onChange={event => onChange(event.target.value)}>{schema.enum.map(item => <option key={String(item)} value={String(item)}>{presentation.values?.[String(item)] ?? String(item)}</option>)}</select></label>;
  if (schema.type === 'number' || schema.type === 'integer') return <label data-parameter-field={name}>{label}<input type="number" min={schema.minimum} max={schema.maximum} step={schema.type === 'integer' ? 1 : 'any'} value={typeof value === 'number' ? value : ''} onChange={event => onChange(event.target.value === '' ? undefined : Number(event.target.value))} /><small>原值：{String(value ?? '∅')}</small></label>;
  if (schema.type === 'boolean') return <label data-parameter-field={name}>{label}<select value={String(value)} onChange={event => onChange(event.target.value === 'true')}><option value="true">是</option><option value="false">否</option></select></label>;
  if (schema.type === 'object' || schema.type === 'array') return <label data-parameter-field={name}>{label}<textarea value={JSON.stringify(value, null, 2)} onChange={event => { try { onChange(JSON.parse(event.target.value)); } catch { /* retain last valid object */ } }} /></label>;
  return <label data-parameter-field={name}>{label}<input value={typeof value === 'string' ? value : ''} onChange={event => onChange(event.target.value)} placeholder={schema.description} /></label>;
}
