import { needsExecutionInputs } from './chat-state';
import { lazy, Suspense, useEffect, useMemo, useState, type ReactNode, type ComponentProps } from 'react';
import { ActivityPanel, type CurrentTaskView } from './ActivityPanel';
import { ConfirmationCard, ResultCard } from './ChatCards';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { copyQuestionText, copyReplyText, CopyMessage, MessageContent } from './MessageContent';
import { IntentOptions } from './RoutingDetails';
import { WorkflowDraftCard } from './WorkflowDraftCard';
import { ApiError, readDataVersion } from './api';
import { argumentLabel, taskModelName, outputName } from './card-presentation';
import { ResourceLink } from './ResourceLink';
import { selectSessionUi, type ResultsRead } from './ui-selectors';
import { inactiveResultGroups } from './result-presentation';
import { productUi, type WorkflowContinuation } from './product-ui';
import { Select } from './ui/Fields';
import { Badge, Loading } from './ui/Feedback';
import type { History, KnowledgeReference, ResourceReference, WorkflowDraft } from './types';

const AssistantContent = lazy(() => import('./AssistantContent').then(module => ({ default: module.AssistantContent })));

interface Props {
  task?: CurrentTaskView;
  confirmationCard?: ReactNode;
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
  onReplyReady?: () => void;
  onJump: (revision: number) => void;
  onRecover?: (workflowRunId: string) => void;
  modelChoices?: readonly { label: string; isMap: boolean; prepare: () => void }[];
  onChoose: (message: string) => void;
  onResource: (id: string, reference?: ResourceReference) => void;
  onInputResource: (id: string, reference?: ResourceReference) => void;
  knowledgeAvailable?: boolean;
  onKnowledge: (reference: KnowledgeReference) => void;
  onAttach: (resource: ResourceReference) => void;
  onContinue: (resource: ResourceReference) => void;
  onPrepareContinuation?: (runId: string, policy: WorkflowContinuation, ref: ResourceReference) => void;
  onCompare: (resource: ResourceReference) => void;
  continueDisabled: boolean;
  continuationVersionId: string | null;
}

function HistoricalConfirmation(props: ComponentProps<typeof ConfirmationCard>) {
  const [open, setOpen] = useState(false);
  const { confirmation } = props;
  return <details className="historical-confirmation" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{confirmation.revision ? `历史修订 ${confirmation.revision}` : '历史确认'}{confirmation.status === 'SUPERSEDED' ? ' · 已被替代' : ''}</summary>
    {open && <ConfirmationCard {...props} readOnly />}
  </details>;
}

export function useVerifiedOutputs(scope: string, results: ResultsRead, references: readonly ResourceReference[]) {
  const signature = useMemo(() => JSON.stringify(references), [references]);
  const cache = useMemo(() => new Map<string, ResourceReference | number>(), [scope, results]);
  const [retry, setRetry] = useState(0);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (results.status !== 'loaded') return;
    const controller = new AbortController();
    const unique = new Map((JSON.parse(signature) as ResourceReference[]).map(ref => [JSON.stringify(ref), ref]));
    const pending = [...unique.entries()].filter(([key]) => !cache.has(key));
    async function verify(key: string, ref: ResourceReference) {
      let state: ResourceReference | number;
      try {
        await readDataVersion(ref.versionId, controller.signal, ref);
        state = ref;
      } catch (error) {
        if (controller.signal.aborted) return;
        if (!(error instanceof ApiError)) throw error;
        state = error.status;
      }
      if (!controller.signal.aborted) {
        cache.set(key, state);
        setRevision(value => value + 1);
      }
    }
    void (async () => {
      for (let index = 0; index < pending.length && !controller.signal.aborted; index += 6) {
        await Promise.all(pending.slice(index, index + 6).map(([key, ref]) => verify(key, ref)));
      }
    })();
    return () => controller.abort();
  }, [cache, signature, results.status, retry]);
  const verifiedState = useMemo(() => ({
    verified: [...cache.values()].filter(state => typeof state !== 'number'),
    errors: Object.fromEntries([...cache].filter((entry): entry is [string, number] => typeof entry[1] === 'number')),
  }), [cache, revision, retry]);
  return {
    ...verifiedState,
    retry: (reference: ResourceReference) => {
      cache.delete(JSON.stringify(reference));
      setRetry(value => value + 1);
    },
  };
}

export function TurnTimeline(props: Props) {
  const { history, results } = props;
  const input = useMemo(() => ({
    sessionId: history.sessionId, history, results,
    freshness: props.connected ? 'current' as const : 'unverified' as const,
    canWrite: props.canWrite,
  }), [history, results, props.connected, props.canWrite]);
  const projection = useMemo(() => selectSessionUi(input), [input]);
  const [expandedRounds, setExpandedRounds] = useState<ReadonlySet<number>>(new Set());
  const [indexFilter, setIndexFilter] = useState('all');
  const visible = (revision: number) => revision === history.goalRevision || expandedRounds.has(revision);
  const references = useMemo(() => projection.turns
    .filter(turn => turn.goalRevision === history.goalRevision || expandedRounds.has(turn.goalRevision))
    .reverse()
    .flatMap(turn => turn.workflowRuns.flatMap(run => {
      const task = history.turns.find(item => item.goalRevision === turn.goalRevision)?.executionTasks.find(item => item.workflowRunId === run.workflowRunId);
      const primary = task ? productUi().models[task.modelId]?.resultOverview?.sections[0].outputs : undefined;
      const priority = (ref: ResourceReference) => ref.mediaType === 'image/png' ? 2 : primary?.includes(ref.outputName ?? '') ? 1 : 0;
      return [...run.outputs].sort((left, right) => priority(right) - priority(left));
    })), [projection, history.goalRevision, history.turns, expandedRounds]);
  const check = useVerifiedOutputs(props.scope, results, references);
  const verifiedOutputs = check.verified;
  const view = useMemo(() => verifiedOutputs ? selectSessionUi({ ...input, verifiedOutputs }) : projection,
    [input, projection, verifiedOutputs]);
  const messagesByTurn = new Map<number, History['items']>();
  for (const message of history.items) {
    if (message.role !== 'user') continue;
    const messages = messagesByTurn.get(message.goalRevision) ?? [];
    messages.push(message);
    messagesByTurn.set(message.goalRevision, messages);
  }

  function turnTitle(turn: History['turns'][number]) {
    const model = turn.confirmations.at(-1)?.actions[0]?.modelId ?? turn.executionTasks[0]?.modelId;
    return model ? taskModelName(model) : messagesByTurn.get(turn.goalRevision)?.[0]?.content.trim().replace(/\s+/g, ' ').slice(0, 32) || '对话';
  }
  const turns = history.turns.map((turn, index) => ({ turn, turnUi: view.turns[index] }))
    .sort((left, right) => left.turn.goalRevision - right.turn.goalRevision);
  const indexedTurns = turns.map(item => item.turn).filter(turn => indexFilter === 'all' || (indexFilter === 'published'
    ? turn.executionTasks.some(run => run.status === 'SUCCEEDED' && run.outputCount > 0)
    : turn.confirmations.some(item => item.status === 'PENDING') || needsExecutionInputs(turn)
      || turn.executionTasks.some(run => run.isLatestAttempt && ['FAILED', 'TIMED_OUT'].includes(run.status))));

  return <>
    {history.turns.length > 1 && <details className="turn-index">
      <summary>轮次目录 <span>{history.turns.length} 轮</span></summary>
      <nav className="turn-jump" aria-label="轮次目录">
        <Select label="筛选轮次" value={indexFilter} onChange={event => setIndexFilter(event.target.value)}>
          <option value="all">全部轮次</option><option value="pending">待办</option><option value="published">已发布</option>
        </Select>
        <Select label="定位对话轮次" value="" disabled={!indexedTurns.length} onChange={event => { if (event.target.value) props.onJump(Number(event.target.value)); }}>
          <option value="">{indexedTurns.length ? '选择轮次' : '没有符合条件的轮次'}</option>
          {indexedTurns.map(turn =>
            <option key={turn.goalRevision} value={turn.goalRevision}>第 {turn.goalRevision} 轮 · {turnTitle(turn)}
              {turn.timing.createdAt ? ` · ${new Date(turn.timing.createdAt).toLocaleDateString('zh-CN')}` : ''}
              {turn.executionTasks.some(run => run.status === 'SUCCEEDED' && run.outputCount > 0) ? ' · 已发布' : ''}</option>)}
        </Select>
      </nav>
    </details>}
    {turns.map(({ turn, turnUi }) => {
      const isCurrent = turn.goalRevision === history.goalRevision;
      const draftId = turn.workflowDraftId;
      const draft = draftId ? props.drafts[draftId] : undefined;
      const draftRead = draftId ? props.draftReads[draftId] : undefined;
      const confirmations = turn.confirmations.filter(item => item.agentRunId === turn.agentRunId && item.goalRevision === turn.goalRevision);
      const currentConfirmation = confirmations.find(item => item.confirmationId === props.currentConfirmationId);
      const runs = turn.executionTasks.filter(item => item.agentRunId === turn.agentRunId && item.goalRevision === turn.goalRevision);
      const publishedRuns = runs.filter(run => run.isLatestAttempt !== false && run.status === 'SUCCEEDED' && run.outputCount > 0);
      const published = publishedRuns.map(run => ({ run, view: turnUi.workflowRuns.find(item => item.workflowRunId === run.workflowRunId) }));
      const hasInactiveBranches = published.some(({ run, view }) => inactiveResultGroups(run.modelId, view?.runArguments).length > 0);
      const analysisResultCount = !hasInactiveBranches ? undefined
        : published.every(({ run, view }) => view?.publishedOutputCount === run.outputCount)
          ? published.reduce((total, { view }) => total + (view?.outputs.length ?? 0), 0) : null;
      const executionSummary = turn.reply?.executionSummary;
      const reply = analysisResultCount !== undefined && turn.reply && executionSummary
        && /^已完成 \d+ 项分析；生成 \d+ 份结果，可在分析结果中查看或下载。$/.test(executionSummary.markdown)
        ? { ...turn.reply, executionSummary: { ...executionSummary, markdown: analysisResultCount === null
          ? '分析成果已发布，可在成果中查看或下载。' : `已发布 ${analysisResultCount} 份分析成果，可在成果中查看或下载。` } }
        : turn.reply;
      const images = turnUi.workflowRuns.filter(run => runs.some(item => item.workflowRunId === run.workflowRunId))
        .flatMap(run => run.outputAccess.filter(output => output.actions.includes('preview')
          && ['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(output.reference.mediaType)).map(output => output.reference));
      const frozen = confirmations.at(-1)?.actions[0];
      const inputRefs = Object.values(frozen?.inputReferences ?? {});
      const parameters = frozen ? (productUi().models[frozen.modelId]?.confirmationSummary.flatMap(group => group.parameters) ?? [])
        .filter(name => ['string', 'number', 'boolean'].includes(typeof frozen.arguments[name]) && frozen.arguments[name] !== '').slice(0, 4) : [];
      const started = turn.timing.createdAt;
      const date = started ? new Date(started) : null;
      const time = date && Number.isFinite(date.getTime()) ? date : null;
      const status = turnUi.status;
      const clarificationRecovery = isCurrent && props.canWrite && turn.status === 'FAILED'
        && turn.clarificationRequired && turn.clarificationReason === 'TASK_CONFLICT'
        ? '请在消息框说明要继续的任务，或补充本次的工作目标。'
        : undefined;
      const resultCards = <>
            {!visible(turn.goalRevision) && runs.some(run => run.outputCount > 0) &&
              <button type="button" className="text-button" onClick={() => setExpandedRounds(current => new Set([...current, turn.goalRevision]))}>核验并查看本轮成果</button>}
            {confirmations.flatMap(confirmation => confirmation.publishedOutputSteps ?? []).map(step => step.errorCode &&
              <ErrorNotice key={step.toolCallId} title="分析已发布，地图准备未完成" code={step.errorCode} />)}
            {runs.filter(run => !isCurrent || !props.task || !['RUNNING', 'QUEUED'].includes(run.status)).map(run => {
              const result = turnUi.workflowRuns.find(item => item.workflowRunId === run.workflowRunId);
              const recovery = result?.status.actions.includes('check-recovery') && !props.busy ? props.onRecover : undefined;
              const card = <ResultCard key={run.workflowRunId} task={run} presentation={result}
                project={props.project} onResource={ref => props.onResource(ref.versionId, ref)}
                onAttach={props.onAttach} onContinue={props.onContinue} onCompare={props.onCompare}
                onPrepareContinuation={props.onPrepareContinuation} runArguments={result?.runArguments}
                onRecover={recovery} verificationErrors={check.errors} retryVerification={check.retry}
                verificationDeferred={!visible(turn.goalRevision)}
                continuationVersionId={props.continuationVersionId} continueDisabled={props.continueDisabled} />;
              return run.isLatestAttempt === false ? <details key={run.workflowRunId}><summary>此前执行 · 第 {run.attempt ?? 1} 次尝试</summary>{card}</details> : card;
            })}
            {results.status === 'failed' && runs.some(run => run.status === 'SUCCEEDED' && run.outputCount > 0)
              && <button className="text-button" onClick={props.retryResults}>重新读取成果</button>}
      </>;
      return <section className="turn" id={`turn-${turn.goalRevision}`} key={turn.goalRevision}
        aria-labelledby={`turn-heading-${turn.goalRevision}`} tabIndex={-1} data-goal-revision={turn.goalRevision} data-current={isCurrent}>
        <header className="turn-heading">
          <h2 id={`turn-heading-${turn.goalRevision}`} aria-current={isCurrent ? 'step' : undefined}>第 {turn.goalRevision} 轮</h2>
          <Badge tone={status.tone}>{status.label}</Badge>
          {turnUi.stop && <Badge tone={turnUi.stop.tone} title={turnUi.stop.description}>{turnUi.stop.label}</Badge>}
          {time && <time dateTime={time.toISOString()}>{time.toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })}</time>}
        </header>
        {messagesByTurn.get(turn.goalRevision)?.map(message => <article className="user-message" aria-label="研究者提问" key={message.messageId}>
          <div className="user-message-content">
            <p className="message-byline">研究者</p>
            <MessageContent content={message.content} onResource={props.onInputResource} />
            {!!message.inputReferences?.length && <div className="message-input-references" role="group" aria-label="本条消息的附件">{message.inputReferences.map(ref => {
              const key = JSON.stringify([ref.versionId, ref.semanticId, ref.objectId, ref.checksum]);
              const action = confirmations.flatMap(confirmation => confirmation.actions).find(item =>
                Object.values(item.inputReferences ?? {}).some(candidate => JSON.stringify(candidate) === JSON.stringify(ref)));
              const field = action?.arguments.valueField ?? action?.arguments.column;
              return ref.outputName
                ? <button key={key} onClick={() => props.onInputResource(ref.versionId, ref)}><Icon name="file" />{outputName(ref.outputName)}</button>
                : <ResourceLink key={key} id={ref.versionId} project={props.project} reference={ref} fieldName={typeof field === 'string' ? field : undefined} onResource={props.onInputResource} />;
            })}</div>}
            <CopyMessage text={() => copyQuestionText(message)} label="复制提问" />
          </div>
        </article>)}
        <article className="assistant-message" aria-label={props.assistant.name}>
          <img className="assistant-avatar" src={props.assistant.image} alt="" width={28} height={28} />
          <div className="assistant-body">
            <p className="message-byline">{props.assistant.name}</p>
            <ActivityPanel turn={turn} snapshotAt={history.snapshotAt} connected={props.connected} task={isCurrent ? props.task : undefined}
              analysisResultCount={analysisResultCount}>
              {isCurrent && currentConfirmation && props.confirmationCard}
              {isCurrent && props.task && !currentConfirmation && frozen && <div className="task-scope">
                {inputRefs.length === 1 && <ResourceLink id={inputRefs[0].versionId} reference={inputRefs[0]} project={props.project} onResource={props.onInputResource} />}
                {inputRefs.length > 1 && <small>本批次使用 {inputRefs.length} 份资料</small>}
                {parameters.length > 0 && <p>{parameters.map(name => argumentLabel(name, frozen.modelId) + ' ' + String(frozen.arguments[name])).join(' · ')}</p>}
              </div>}
            </ActivityPanel>
            {isCurrent && resultCards}
            {isCurrent && turn.status === 'RUNNING' && props.preview?.agentRunId === turn.agentRunId
              && props.preview.text && !turn.reply && !turn.errorCode && <div className="reply-preview" role="status">
                <small>生成中 · 尚未核验</small><p>{props.preview.text}</p>
              </div>}
            {reply && <>
              <Suspense fallback={<Loading>正在显示回复…</Loading>}>
                <AssistantContent reply={reply} images={images} onReady={props.onReplyReady}
                  unresolvedLabel={turn.dialoguePolicy === 'READ_ONLY' && turn.semanticIntent === 'CONSULT' ? '后续可补充' : undefined} onResource={id => {
                  const exact = turnUi.workflowRuns.flatMap(run => run.outputs).filter(ref => ref.versionId === id);
                  if (exact.length === 1) props.onResource(id, exact[0]);
                  else props.onInputResource(id);
                }} onKnowledge={props.onKnowledge} />
              </Suspense>
              <CopyMessage label="复制回复" text={() => copyReplyText(reply)} />
            </>}
            <IntentOptions turn={turn} current={isCurrent} actionable={isCurrent && props.canWrite} busy={props.busy} onChoose={props.onChoose} />
            {draftId && <>
              {draft && draft.draftId === draftId && draft.sessionId === history.sessionId
                && draft.agentRunId === turn.agentRunId && draft.goalRevision === turn.goalRevision
                && <WorkflowDraftCard draft={draft} onKnowledge={props.onKnowledge} onResource={props.onResource} />}
              {draftRead?.status === 'loading' && <Loading>正在读取工作流草稿…</Loading>}
              {draftRead?.status === 'error' && <section className="workflow-draft-card" aria-label="工作流草稿读取失败">
                <ErrorNotice title="草稿暂时无法读取" error={draftRead.error} />
                <button onClick={() => props.retryDraft(draftId)}>重试读取草稿</button>
              </section>}
            </>}
            {reply && isCurrent && props.canWrite && !props.busy && turn.semanticIntent === 'CONSULT' && !runs.length && (() => {
              const question = messagesByTurn.get(turn.goalRevision)?.[0]?.content ?? '';
              const matches = props.modelChoices?.filter(item => question.includes(item.label)
                || ((reply.conclusion?.markdown ?? '').includes(item.label) && question.includes('制图') && item.isMap));
              return matches?.length === 1 ? <button type="button" onClick={matches[0].prepare}>准备{matches[0].label}</button> : null;
            })()}
            {!isCurrent && resultCards}
            {turn.errorCode && <ErrorNotice title="本轮未完成" code={turn.errorCode}
              recovery={clarificationRecovery} agentDiagnostics={turn.diagnostics} agentMetrics={turn.agentMetrics} />}
            {confirmations.filter(item => item !== currentConfirmation).map(confirmation =>
              <HistoricalConfirmation key={confirmation.confirmationId} confirmation={confirmation} history={history} busy={props.busy}
                verificationSession={props.verificationSession} onDecision={() => {}} onResource={props.onInputResource}
                onKnowledge={props.knowledgeAvailable ? props.onKnowledge : undefined} project={props.project} />)}
          </div>
        </article>
      </section>;
    })}
  </>;
}
