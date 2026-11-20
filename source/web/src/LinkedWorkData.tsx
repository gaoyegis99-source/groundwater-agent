import { useEffect, useState } from 'react';
import { WorkItemIssue } from './WorkItemForms';
import { outputName } from './card-presentation';
import type { ResourceReference, WorkItemSessionContext } from './types';
import { readWorkItemSessionContext } from './work-item-api';
import { workflowFileKey } from './workflow-selection';

export type LinkedWorkScope = { sessionId: string; subject: string };

export function LinkedWorkData({ scope, project, allowedFormats, onInspect }: {
  scope: LinkedWorkScope;
  project: string;
  allowedFormats?: string[];
  onInspect: (reference: ResourceReference) => void;
}) {
  const [context, setContext] = useState<WorkItemSessionContext | null>();
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  const { sessionId, subject } = scope;
  useEffect(() => {
    let pending: AbortController | undefined;
    function load() {
      pending?.abort();
      const controller = new AbortController();
      pending = controller;
      setContext(undefined);
      setError(null);
      void readWorkItemSessionContext(sessionId, { subject, project }, controller.signal).then(value => {
        if (!controller.signal.aborted) setContext(value);
      }, caught => {
        if (!controller.signal.aborted) setError(caught);
      });
    }
    load();
    window.addEventListener('focus', load);
    return () => { pending?.abort(); window.removeEventListener('focus', load); };
  }, [sessionId, project, subject, attempt]);
  if (context === null || context?.workItem.status === 'ARCHIVED') return null;
  const candidates = new Map<string, { label: string; reference: ResourceReference }>();
  function add(reference: ResourceReference, label: string) {
    if (allowedFormats?.length && !allowedFormats.some(value => value.toLowerCase() === reference.format.toLowerCase())) return;
    candidates.set(workflowFileKey(reference), { label, reference });
  }
  context?.assets.forEach(({ state, reference }) => {
    if (state === 'AVAILABLE' && (reference?.kind === 'DATA' || reference?.kind === 'RESULT')) {
      add(reference.dataRef, reference.kind === 'RESULT' ? outputName(reference.outputName) : '关联资料');
    }
  });
  context?.objects.forEach(({ state, reference }) => {
    if (state === 'AVAILABLE' && reference && !candidates.has(workflowFileKey(reference.source))) {
      add(reference.source, `对象来源 · ${reference.externalId}`);
    }
  });
  return <section className="linked-work-data" aria-label="当前工作的资料">
    <h4>当前工作的资料</h4>
    {error ? <WorkItemIssue error={error} retry={() => setAttempt(value => value + 1)} />
      : context === undefined ? <p role="status">正在核对关联资料…</p>
        : candidates.size === 0 ? <p>暂无适合此输入角色的可读资料。</p>
          : <ul>{[...candidates].map(([key, { reference, label }]) => <li key={key}>
            <button type="button" onClick={() => onInspect(reference)} aria-label={`${label}，版本 ${reference.versionId}，语义 ${reference.semanticId}`}>
              <strong>{label}</strong>
              <small>{reference.format.toUpperCase()} · {reference.versionId}</small>
            </button>
          </li>)}</ul>}
  </section>;
}
