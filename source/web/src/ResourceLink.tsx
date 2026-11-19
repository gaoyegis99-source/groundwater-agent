import { useEffect, useId, useState } from 'react';
import { ApiError, callApi, readDataVersion } from './api';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { productUi } from './product-ui';
import type { KnowledgeEvidence, KnowledgeReference, ResourceDetail, ResourceReference } from './types';

type FieldBinding = NonNullable<KnowledgeEvidence['bindings']>[number];

export function DataIdentity({ reference }: { reference: ResourceReference }) {
  return <dl className="argument-list">
    <div><dt>数据集</dt><dd>{reference.datasetId}</dd></div>
    <div><dt>数据版本</dt><dd>{reference.versionId}</dd></div>
    <div><dt>语义版本</dt><dd>{reference.semanticId}</dd></div>
    <div><dt>数据对象</dt><dd>{reference.objectId}</dd></div>
    <div><dt>校验和</dt><dd>{reference.checksum}</dd></div>
  </dl>;
}

function FieldEvidence({ project, resource, fieldId, references, onKnowledge, compact }: {
  project: string; resource: ResourceDetail; fieldId: string;
  references: KnowledgeReference[]; onKnowledge: (reference: KnowledgeReference) => void; compact: boolean;
}) {
  const binding: FieldBinding = {
    serviceBinding: 'data-primary', projectId: project, datasetId: resource.datasetId,
    versionId: resource.versionId, semanticId: resource.semanticId, fieldId,
  };
  const signature = JSON.stringify({ binding, references });
  const [result, setResult] = useState<{ signature: string; items: KnowledgeEvidence[] | null }>();
  useEffect(() => {
    const controller = new AbortController();
    const { binding, references } = JSON.parse(signature) as { binding: FieldBinding; references: KnowledgeReference[] };
    const budget = { maxUnits: 16, maxChars: 32_000 };
    async function read() {
      const items: KnowledgeEvidence[] = [];
      if (!references.length) {
        const bundle = await callApi('knowledge_lookup', '/knowledge/lookup', { binding, budget }, controller.signal);
        items.push(...bundle.items);
      }
      for (let index = 0; index < references.length; index += 16) {
        const refs = references.slice(index, index + 16);
        const bundle = await callApi('knowledge_read_evidence', '/knowledge/evidence/read', { refs, budget }, controller.signal);
        items.push(...bundle.items.filter(item => refs.some(ref =>
          item.versionId === ref.versionId && item.unitId === ref.unitId && item.checksum === ref.checksum)));
      }
      return items.filter(item => item.status === 'OK' && item.content?.trim()
        && item.scope === 'DATA_BOUND' && item.bindingMatch !== 'MISMATCH'
        && item.bindings?.some(candidate => candidate.serviceBinding === binding.serviceBinding
          && candidate.projectId === binding.projectId && candidate.datasetId === binding.datasetId
          && candidate.versionId === binding.versionId && candidate.semanticId === binding.semanticId
          && candidate.fieldId === binding.fieldId));
    }
    void read().then(items => {
      if (!controller.signal.aborted) setResult({ signature, items });
    }).catch(caught => {
      if (controller.signal.aborted) return;
      if (!(caught instanceof ApiError)) throw caught;
      setResult({ signature, items: null });
    });
    return () => controller.abort();
  }, [signature]);

  const label = references.length ? '本轮字典依据' : '绑定字段的字典';
  if (result?.signature !== signature) return <p className="muted" role="status">正在核对字典依据…</p>;
  if (!result.items?.length) return <p className="muted">{result.items ? '未找到与所选字段匹配的字典依据。' : '字段字典暂时无法核对。'}</p>;
  if (compact && result.items.length === 1) {
    const { versionId, unitId, checksum, documentTitle, locator } = result.items[0];
    const title = [documentTitle, locator?.section].filter(Boolean).join(' · ');
    return <button type="button" className="text-button field-dictionary" title={title}
      aria-label={title ? `字段字典 · ${title}` : '字段字典'}
      onClick={() => onKnowledge({ versionId, unitId, checksum })}>字段字典<Icon name="arrow-up-right" /></button>;
  }
  return <div className="confirmation-evidence">
    <span>{label}</span>
    <ul>{result.items.map(({ versionId, unitId, checksum, documentTitle, locator }) => (
      <li key={JSON.stringify([versionId, unitId, checksum])}>
        <button type="button" className="text-button" onClick={() => onKnowledge({ versionId, unitId, checksum })}>
          {documentTitle || '查看字典原文'}{locator?.section ? ` · ${locator.section}` : ''}
        </button>
      </li>
    ))}</ul>
  </div>;
}

export function ResourceLink({ id, project, reference, fieldName, evidence = [], onResource, onKnowledge, showCoverage = false, compactEvidence = false }: {
  id: string; project: string; reference?: ResourceReference; fieldName?: string; showCoverage?: boolean;
  compactEvidence?: boolean;
  evidence?: KnowledgeReference[]; onResource: (id: string, reference?: ResourceReference) => void;
  onKnowledge?: (reference: KnowledgeReference) => void;
}) {
  const descriptionId = useId();
  const frozen = JSON.stringify(reference ?? null);
  const [read, setRead] = useState<{ id: string; project: string; frozen: string; value: ResourceDetail | ApiError }>();
  useEffect(() => {
    const controller = new AbortController();
    void readDataVersion(id, controller.signal, (JSON.parse(frozen) as ResourceReference | null) ?? undefined)
      .then(value => {
        if (!controller.signal.aborted) setRead({ id, project, frozen, value });
      }).catch(caught => {
        if (controller.signal.aborted) return;
        if (!(caught instanceof ApiError)) throw caught;
        setRead({ id, project, frozen, value: caught });
      });
    return () => controller.abort();
  }, [id, project, frozen]);
  const value = read?.id === id && read.project === project && read.frozen === frozen ? read.value : undefined;
  const error = value instanceof ApiError ? value : null;
  const resource = value instanceof ApiError ? null : value;
  const fields = fieldName ? resource?.descriptor.fields?.filter(field => field.name === fieldName) : undefined;
  const field = fields?.length === 1 ? fields[0] : undefined;
  const semantic = field ? resource?.semantics.fields?.[field.fieldId] : undefined;
  const labels = productUi().quantityKindLabels;
  const kind = semantic?.quantityKind;
  const quantity = kind && labels && Object.hasOwn(labels, kind) ? labels[kind] : null;
  const meaning = semantic?.meaning?.trim();
  const fieldDescription = quantity && meaning && quantity.includes(meaning) ? quantity
    : [meaning, quantity && !meaning?.includes(quantity) ? quantity : null].filter(Boolean).join(' · ');
  const unit = semantic?.unit === 'm' ? '米' : semantic?.unit;

  return <>
    <button type="button" className="text-button" aria-describedby={fieldName && resource ? descriptionId : undefined}
      onClick={() => reference ? onResource(id, reference) : onResource(id)}>{resource
      ? <>{resource.title}<Icon name="arrow-up-right" /></>
      : error ? '数据名称暂不可用 · 打开详情' : '正在读取数据名称…'}</button>
    {fieldName && resource && <p id={descriptionId} className="confirmation-field">
      {fieldName} · {fieldDescription || '字段含义待核对'}{unit ? `（${unit}）` : ''}
    </p>}
    {showCoverage && resource && <p className="confirmation-field">{[resource.semantics.timeRange, resource.semantics.crs].filter(Boolean).join(' · ') || '时间覆盖与坐标系未提供'}</p>}
    {field && resource && onKnowledge && /^f[0-9]+$/.test(field.fieldId) && <FieldEvidence project={project}
      resource={resource} fieldId={field.fieldId} references={evidence} onKnowledge={onKnowledge} compact={compactEvidence} />}
    {fieldName && resource && !field && onKnowledge && <p className="muted">字段标识待核对，暂时无法核对字典依据。</p>}
    {!reference && <details className="technical-details"><summary>数据版本详情</summary><code>{id}</code></details>}
    {error && <ErrorNotice error={error} />}
  </>;
}
