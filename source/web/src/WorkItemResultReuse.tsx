import { useLayoutEffect, useRef, useState } from 'react';
import { scopedStorageKey } from './browser-scope';
import { outputName } from './card-presentation';
import { isResourceItems } from './message-routing';
import { dataObjectKey } from './resource-item';
import { localizedSessionTitle } from './SessionNavigation';
import { ApiError } from './api';
import { workItemErrorMessage } from './work-item-presentation';
import { sameDataIdentity, sameWorkItemResult, type WorkItemResultReference } from './work-item-result-evidence';
import { requireReusableResults } from './work-item-result-reuse';
import { readWorkItemRoute, sessionFromWorkItem } from './work-item-route';
import type { ResourceItem, WorkItemDetail } from './types';

type ResultAttachment = ResourceItem & { resultProvenance: NonNullable<ResourceItem['resultProvenance']> };
type ReuseProps = {
  detail: WorkItemDetail;
  scope: string;
  sourceSessionId: string;
  assetLinkId: string;
  reference: WorkItemResultReference;
  disabled: boolean;
};

function matchingAsset(link: WorkItemDetail['assets'][number] | undefined, reference: WorkItemResultReference) {
  return link?.state === 'AVAILABLE' && link.reference?.kind === 'RESULT' && sameWorkItemResult(link.reference, reference);
}

function addDraftAttachment(scope: string, sessionId: string, result: ResultAttachment) {
  const key = scopedStorageKey(scope, 'draft-attachments', sessionId);
  try {
    const raw = sessionStorage.getItem(key);
    const attachments: unknown = raw === null ? [] : JSON.parse(raw);
    if (!isResourceItems(attachments)) return '无法读取目标会话的附件草稿。请在目标会话核对草稿。';

    const object = dataObjectKey(result);
    const matches = attachments.filter(item => dataObjectKey(item) === object);
    const attached = matches[0];
    const prior = attached?.resultProvenance;
    const checked = result.resultProvenance;
    const changedProvenance = prior && (prior.workItemId !== checked.workItemId || prior.sourceSessionId !== checked.sourceSessionId
      || prior.runId !== checked.runId || prior.outputName !== checked.outputName || prior.publicationId !== checked.publicationId);
    if (attached && (matches.length > 1
      || !sameDataIdentity({ ...attached, outputName: attached.outputName ?? result.outputName }, result) || changedProvenance)) {
      return '该资料对象与目标草稿中的附件有冲突。请先在目标会话核对附件。';
    }

    // The message contract accepts up to 20 input references.
    if (!attached && attachments.length >= 20) return '目标草稿已包含 20 个附件。请移除一个附件后重试。';
    if (!attached) sessionStorage.setItem(key, JSON.stringify([...attachments, result]));
    else if (!prior || attached.outputName === undefined) sessionStorage.setItem(key, JSON.stringify(
      attachments.map(item => item === attached ? { ...item, outputName: result.outputName, resultProvenance: checked } : item),
    ));
  } catch (error) {
    if (!(error instanceof DOMException || error instanceof SyntaxError)) throw error;
    return '无法保存目标会话的附件草稿。请核对浏览器存储后重试。';
  }
  return null;
}

export function WorkItemResultReuse(props: ReuseProps) {
  const { detail, scope, sourceSessionId, assetLinkId, reference } = props;
  const { workItemId, projectId, ownerSubject } = detail.workItem;
  const key = JSON.stringify([scope, workItemId, projectId, ownerSubject, sourceSessionId, assetLinkId, reference]);
  return <ResultDraftHandoff key={key} {...props} />;
}

function ResultDraftHandoff({ detail, scope, sourceSessionId, assetLinkId, reference, disabled }: ReuseProps) {
  const { workItemId, projectId, ownerSubject, status } = detail.workItem;
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [issue, setIssue] = useState<string | null>(null);
  const pending = useRef<AbortController | null>(null);
  const destinations = detail.sessions.filter(session => session.sessionId !== sourceSessionId && session.archivedAt === null);
  const targetSessionId = destinations.some(session => session.sessionId === selected) ? selected : '';
  const available = !!scope && status === 'ACTIVE' && reference.dataRef.outputName === reference.outputName
    && matchingAsset(detail.assets.find(link => link.linkId === assetLinkId), reference);
  const blocked = disabled || !available;

  useLayoutEffect(() => () => {
    pending.current?.abort();
    pending.current = null;
  }, []);
  useLayoutEffect(() => {
    if (blocked) pending.current?.abort();
  }, [blocked]);

  async function reuse() {
    const hash = window.location.hash;
    const route = readWorkItemRoute(hash);
    if (blocked || !targetSessionId || pending.current || route?.kind !== 'detail' || route.id !== workItemId) return;

    const controller = new AbortController();
    const { signal } = controller;
    pending.current = controller;
    const currentView = () => !signal.aborted && window.location.hash === hash;
    const identityScope = { project: projectId, subject: ownerSubject };
    setBusy(true);
    setIssue(null);
    try {
      await requireReusableResults(workItemId, targetSessionId, identityScope, [{ sourceSessionId, reference, assetLinkId }], signal);
      if (!currentView()) return;

      const failure = addDraftAttachment(scope, targetSessionId, {
        ...reference.dataRef,
        name: outputName(reference.outputName),
        familyId: reference.dataRef.datasetId,
        resultProvenance: { workItemId, sourceSessionId, runId: reference.runId, outputName: reference.outputName,
          publicationId: reference.publicationId },
      });
      if (failure) setIssue(failure);
      else window.location.hash = sessionFromWorkItem(`#/sessions/${encodeURIComponent(targetSessionId)}`, workItemId);
    } catch (error) {
      if (currentView()) setIssue(error instanceof ApiError && error.code === 'WORK_ITEM_RESULT_REUSE_TARGET_UNAVAILABLE'
        ? '目标会话已不可用于此工作。请刷新后重新选择。'
        : error instanceof ApiError && error.code === 'WORK_ITEM_RESULT_REUSE_UNAVAILABLE'
          ? '该发布成果当前不可读或引用已变化。请刷新后重新核对。' : workItemErrorMessage(error));
    } finally {
      if (pending.current === controller) {
        controller.abort();
        pending.current = null;
        setBusy(false);
      }
    }
  }

  if (!available) return null;
  if (!destinations.length) return <p>关联另一个未归档会话后可复用此成果。</p>;
  return <>
    <form className="work-item-actions work-result-reuse" onSubmit={event => { event.preventDefault(); void reuse(); }}>
      <label>复用目标会话 <select value={targetSessionId} disabled={blocked || busy}
        onChange={event => { setSelected(event.target.value); setIssue(null); }}>
        <option value="">选择会话</option>
        {destinations.map(session => <option key={session.sessionId} value={session.sessionId}>{localizedSessionTitle(session.title)}</option>)}
      </select></label>
      <button type="submit" disabled={blocked || busy || !targetSessionId}>添加到会话草稿</button>
      {busy && <span role="status">正在核对成果与目标会话…</span>}
    </form>
    {issue && <p className="work-item-issue" role="alert">{issue}</p>}
  </>;
}
