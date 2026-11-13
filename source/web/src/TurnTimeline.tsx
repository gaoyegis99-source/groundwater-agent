import { useEffect, useMemo, useState } from 'react';
import { ActivityPanel } from './ActivityPanel';
import { ConfirmationCard, ResultCard } from './ChatCards';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { AssistantContent, CopyMessage, MessageContent } from './MessageContent';
import { IntentOptions } from './RoutingDetails';
import { WorkflowDraftCard } from './WorkflowDraftCard';
import { ApiError, readDataVersion } from './api';
import { shortVersion } from './format';
import { selectSessionUi, type ResultsRead } from './ui-selectors';
import type { History, KnowledgeReference, ResourceReference, WorkflowDraft } from './types';

interface Props {
  history: History;
  scope: string;
  project: string;
  assistant: { name: string; image: string };
  connected: boolean;
  currentConfirmationId?: string;
  busy: boolean;
  canWrite: boolean;
  verificationSession: boolean;
  preview: { agentRunId: string; text: string | null } | null;
  drafts: Readonly<Record<string, WorkflowDraft>>;
  draftReads: Readonly<Record<string, { status: 'loading' | 'ready' } | { status: 'error'; error: ApiError }>>;
  retryDraft: (id: string) => void;
  results: ResultsRead;
  retryResults: () => void;
  onJump: (revision: number) => void;
  onChoose: (message: string) => void;
  onResource: (id: string, reference?: ResourceReference) => void;
  onInputResource: (id: string, reference?: ResourceReference) => void;
  onKnowledge: (reference: KnowledgeReference) => void;
  onAttach: (resource: ResourceReference) => void;
  onContinue: (resource: ResourceReference) => void;
  onCompare: (resource: ResourceReference) => void;
  continueDisabled: boolean;
  continuationVersionId: string | null;
}

function useVerifiedOutputs(scope: string, results: ResultsRead, references: readonly ResourceReference[]) {
  // The signature uses the entire reference to invalidate verification on any identity change.
  const signature = JSON.stringify(references);
  const [check, setCheck] = useState<{ scope: string; signature: string; results: ResultsRead; verified: ResourceReference[] }>();
  useEffect(() => {
    if (results.status !== 'loaded') return;
    const controller = new AbortController();
    const unique = new Map((JSON.parse(signature) as ResourceReference[]).map(ref => [JSON.stringify(ref), ref]));
    void (async () => {
      // Bounded batches allow result actions to become available without waiting for the whole history.
      const verified: ResourceReference[] = [];
      const pending = [...unique.values()];
      for (let index = 0; index < pending.length && !controller.signal.aborted; index += 6) {
        const batch = await Promise.all(pending.slice(index, index + 6).map(async ref => {
          try {
            await readDataVersion(ref.versionId, controller.signal, ref);
            return ref;
          } catch (error) {
            if (error instanceof ApiError || controller.signal.aborted) return null;
            throw error;
          }
        }));
        if (controller.signal.aborted) return;
        for (const ref of batch) if (ref) verified.push(ref);
        setCheck({ scope, signature, results, verified: [...verified] });
      }
    })();
    return () => controller.abort();
  }, [scope, signature, results]);
  return check?.scope === scope && check.signature === signature && check.results === results ? check.verified : undefined;
}

export function TurnTimeline(props: Props) {
  const { history, results } = props;
  const input = useMemo(() => ({
    sessionId: history.sessionId, history, results,
    freshness: props.connected ? 'current' as const : 'unverified' as const,
    canWrite: props.canWrite,
  }), [history, results, props.connected, props.canWrite]);
  const projection = useMemo(() => selectSessionUi(input), [input]);
  const references = projection.turns.flatMap(turn => turn.workflowRuns.flatMap(run => run.outputs));
  const verifiedOutputs = useVerifiedOutputs(props.scope, results, references);
  const view = useMemo(() => verifiedOutputs ? selectSessionUi({ ...input, verifiedOutputs }) : projection,
    [input, projection, verifiedOutputs]);
  const messagesByTurn = new Map<number, History['items']>();
  for (const message of history.items) {
    if (message.role !== 'user') continue;
    const messages = messagesByTurn.get(message.goalRevision) ?? [];
    messages.push(message);
    messagesByTurn.set(message.goalRevision, messages);
  }

  return <>
    {history.turns.length > 1 && <label className="turn-jump">定位对话轮次
      <select value="" onChange={event => { if (event.target.value) props.onJump(Number(event.target.value)); }}>
        <option value="">选择轮次</option>
        {history.turns.map(turn => <option key={turn.goalRevision} value={turn.goalRevision}>第 {turn.goalRevision} 轮</option>)}
      </select>
    </label>}
    {history.turns.map((turn, index) => {
      const turnUi = view.turns[index];
      const isCurrent = turn.goalRevision === history.goalRevision;
      const draftId = turn.workflowDraftId;
      const draft = draftId ? props.drafts[draftId] : undefined;
      const draftRead = draftId ? props.draftReads[draftId] : undefined;
      const confirmations = turn.confirmations.filter(item => item.agentRunId === turn.agentRunId && item.goalRevision === turn.goalRevision);
      const runs = turn.executionTasks.filter(item => item.agentRunId === turn.agentRunId && item.goalRevision === turn.goalRevision);
      const images = turnUi.workflowRuns.filter(run => runs.some(item => item.workflowRunId === run.workflowRunId))
        .flatMap(run => run.outputAccess.filter(output => output.actions.includes('preview')
          && ['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(output.reference.mediaType)).map(output => output.reference));
      const started = turn.timing.createdAt;
      const date = started ? new Date(started) : null;
      const time = date && Number.isFinite(date.getTime()) ? date : null;
      const status = turnUi.status;
      return <section className="turn" id={`turn-${turn.goalRevision}`} key={turn.goalRevision}
        aria-labelledby={`turn-heading-${turn.goalRevision}`} tabIndex={-1} data-goal-revision={turn.goalRevision}>
        <header className="turn-heading">
          <h2 id={`turn-heading-${turn.goalRevision}`}>第 {turn.goalRevision} 轮</h2>
          <span className="turn-status" data-tone={status.tone}>{status.label}</span>
          {turnUi.stop && <span className="turn-status" data-tone={turnUi.stop.tone}
            title={turnUi.stop.description}>{turnUi.stop.label}</span>}
          {time && <time dateTime={time.toISOString()}>{time.toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })}</time>}
        </header>
        {messagesByTurn.get(turn.goalRevision)?.map(message => <div className="user-message" key={message.messageId}>
          <div className="user-message-content">
            <MessageContent content={message.content} onResource={props.onInputResource} />
            {!!message.inputReferences?.length && <div className="message-input-references">{message.inputReferences.map(ref => {
              const label = ref.outputName ?? ref.datasetId;
              return <button key={JSON.stringify([ref.versionId, ref.semanticId, ref.objectId, ref.checksum])}
                aria-label={`查看附件 ${label} · ${shortVersion(ref.versionId)}`} title={`数据版本 ${ref.versionId}`}
                onClick={() => props.onInputResource(ref.versionId, ref)}>
                <Icon name="file" />{label} · {shortVersion(ref.versionId)}
              </button>;
            })}</div>}
            <CopyMessage text={message.content} label="复制提问" />
          </div>
        </div>)}
        <div className="assistant-message">
          <img className="assistant-avatar" src={props.assistant.image} alt={props.assistant.name} />
          <div className="assistant-body">
            <ActivityPanel turn={turn} snapshotAt={history.snapshotAt} connected={props.connected} />
            {isCurrent && turn.status === 'RUNNING' && props.preview?.agentRunId === turn.agentRunId
              && props.preview.text && !turn.reply && !turn.errorCode && <div className="reply-preview" role="status">
                <small>生成中 · 尚未核验</small><p>{props.preview.text}</p>
              </div>}
            {turn.reply && <>
              <AssistantContent reply={turn.reply} images={images} onResource={id => {
                const exact = turnUi.workflowRuns.flatMap(run => run.outputs).filter(ref => ref.versionId === id);
                if (exact.length === 1) props.onResource(id, exact[0]);
                else props.onInputResource(id);
              }} onKnowledge={props.onKnowledge} />
              <CopyMessage label="复制回复" text={[
                turn.reply.conclusion, turn.reply.executionSummary, ...turn.reply.details, ...turn.reply.unresolvedItems,
              ].flatMap(section => section ? [section.markdown] : []).join('\n\n')} />
            </>}
            <IntentOptions turn={turn} actionable={isCurrent && props.canWrite} busy={props.busy} onChoose={props.onChoose} />
            {draftId && <>
              {draft && draft.draftId === draftId && draft.sessionId === history.sessionId
                && draft.agentRunId === turn.agentRunId && draft.goalRevision === turn.goalRevision
                && <WorkflowDraftCard draft={draft} onKnowledge={props.onKnowledge} onResource={props.onResource} />}
              {draftRead?.status === 'loading' && <p className="muted" role="status">正在读取工作流草稿…</p>}
              {draftRead?.status === 'error' && <section className="workflow-draft-card" aria-label="工作流草稿读取失败">
                <ErrorNotice title="草稿暂时无法读取" error={draftRead.error} />
                <button onClick={() => props.retryDraft(draftId)}>重试读取草稿</button>
              </section>}
            </>}
            {confirmations.map(confirmation => confirmation.confirmationId === props.currentConfirmationId
              ? <a key={confirmation.confirmationId} className="turn-confirmation-link" href="#current-confirmation"
                onClick={event => { event.preventDefault(); document.getElementById('current-confirmation')?.focus(); }}>
                查看本轮执行确认{confirmation.revision ? ` · 修订 ${confirmation.revision}` : ''}
              </a>
              : <details key={confirmation.confirmationId} className="historical-confirmation">
                <summary>{confirmation.revision ? `历史修订 ${confirmation.revision}` : '历史确认'}{confirmation.status === 'SUPERSEDED' ? ' · 已被替代' : ''}</summary>
                <ConfirmationCard confirmation={confirmation} history={history} readOnly busy={props.busy}
                  verificationSession={props.verificationSession} onDecision={() => {}} onResource={props.onInputResource} project={props.project} />
              </details>)}
            {runs.map(run => <ResultCard key={run.workflowRunId} task={run}
              presentation={turnUi.workflowRuns.find(item => item.workflowRunId === run.workflowRunId)}
              project={props.project} onResource={ref => props.onResource(ref.versionId, ref)}
              onAttach={props.onAttach} onContinue={props.onContinue} onCompare={props.onCompare}
              continuationVersionId={props.continuationVersionId} continueDisabled={props.continueDisabled} />)}
            {results.status === 'failed' && runs.some(run => run.status === 'SUCCEEDED' && run.outputCount > 0)
              && <button className="text-button" onClick={props.retryResults}>重新读取成果</button>}
            {turn.errorCode && <ErrorNotice title="本轮未完成" code={turn.errorCode}
              agentDiagnostics={turn.diagnostics} agentMetrics={turn.agentMetrics} />}
          </div>
        </div>
      </section>;
    })}
  </>;
}
