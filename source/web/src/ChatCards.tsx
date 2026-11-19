import { useEffect, useState, type ReactNode } from 'react';
import { canConfirm, isComposedConfirmation, statusLabel, taskLabel } from './chat-state';
import { contentUrl } from './api';
import { ArgumentValue, argumentGroup, argumentLabel, modelName, outputName } from './card-presentation';
import { PrimaryTable } from './PrimaryTable';
import { ErrorNotice } from './ErrorNotice';
import { DataIdentity, ResourceLink } from './ResourceLink';
import { Icon } from './Icon';
import { CopyMessage } from './MessageContent';
import type { Action, Capabilities, Confirmation, ExecutionTask, History, KnowledgeReference, ResourceReference, Result, WorkflowDraftPayload } from './types';
import { productUi, type WorkflowContinuation } from './product-ui';
import { record, ResultInsights, usePublishedJson } from './ResultInsights';
import { Badge } from './ui/Feedback';

function confirmationProfile(confirmation: Confirmation, history: History | null) {
  const recorded = history?.turns.find(turn => turn.goalRevision === confirmation.goalRevision)
    ?.progressUpdates?.find(update => update.code === 'INPUTS_VERIFIED')?.facts.profile;
  return typeof recorded === 'string' ? recorded : null;
}
function confirmationCase(confirmation: Confirmation, history: History | null) {
  const caseId = history?.turns.find(turn => turn.goalRevision === confirmation.goalRevision)
    ?.progressUpdates?.find(update => update.code === 'CASE_MATCHED')?.facts.caseId;
  if (typeof caseId !== 'string') return null;
  return productUi().cases[caseId]?.label ?? caseId;
}
function isVerificationConfirmation(confirmation: Confirmation) {
  const reason = confirmation.reason?.trim() ?? '';
  return reason.startsWith('SYNTHETIC_TEST_ONLY') || reason.startsWith('【验证会话】') || reason === '别名命中验证完成，不重复执行';
}
function isVersionParameter(name: string) {
  return name === 'versionId' || name.endsWith('VersionId');
}

function isInputArgument(action: Action, name: string) {
  return isVersionParameter(name) || Object.hasOwn(action.inputReferences ?? {}, name);
}

function ParameterList({ entries, action, reviewedInput, project, onResource }: {
  entries: [string, unknown][]; action: Action; project: string;
  reviewedInput?: string;
  onResource: (id: string, reference?: ResourceReference) => void;
}) {
  return <dl className="argument-list readable-arguments">{entries.map(([name, value]) => {
    const reference = action.inputReferences?.[name];
    return <div key={name}><dt>{argumentLabel(name, action.modelId)}</dt><dd>{isVersionParameter(name) && typeof value === 'string' && name !== reviewedInput
      ? <ResourceLink id={reference?.versionId ?? value} project={project} reference={reference} onResource={onResource} />
      : reference
        ? <button type="button" className="text-button" aria-label={`查看冻结资料 ${argumentLabel(name, action.modelId)}`}
          onClick={() => onResource(reference.versionId, reference)}>查看资料</button>
        : <ArgumentValue value={value} name={name} modelId={action.modelId} />}</dd></div>;
  })}</dl>;
}

function selectedField(action: Action) {
  const field = action.arguments.valueField ?? action.arguments.column;
  return typeof field === 'string' && field ? field : undefined;
}

function recordedEvidence(history: History | null, confirmation: Confirmation, action: Action, name: string, reference: ResourceReference) {
  const preparation = history?.preparation;
  const entries = preparation?.agentRunId === confirmation.agentRunId && preparation.goalRevision === confirmation.goalRevision
    ? preparation.entries : [];
  const prepared = entries.flatMap(entry => {
    const input = entry.inputs[name];
    return entry.status === 'READY' && entry.targetId === action.modelId && entry.targetVersion === action.modelVersion
      && (entry.parameters.valueField ?? entry.parameters.column) === selectedField(action)
      && input?.datasetId === reference.datasetId && input.versionId === reference.versionId
      && input.semanticId === reference.semanticId && input.objectId === reference.objectId && input.checksum === reference.checksum
      ? entry.knowledgeRefs : [];
  });
  const turn = history?.turns.find(item => item.agentRunId === confirmation.agentRunId && item.goalRevision === confirmation.goalRevision);
  const refs = [...prepared, ...(turn?.reply?.sources ?? [])].map(({ versionId, unitId, checksum }) => ({ versionId, unitId, checksum }));
  return [...new Map(refs.map(ref => [JSON.stringify(ref), ref])).values()];
}

function ConfirmationChange({ action, name, before, after }: { action: Action; name: string; before: unknown; after: unknown }) {
  if (isInputArgument(action, name)) return <>输入资料已调整</>;
  return <>{before === undefined ? '未设置' : <ArgumentValue name={name} modelId={action.modelId} value={before} />} → {after === undefined ? '已移除' : <ArgumentValue name={name} modelId={action.modelId} value={after} />}</>;
}

function ComposedInputRoles({ draft, action, onResource }: {
  draft: WorkflowDraftPayload; action: Action;
  onResource: (id: string, reference?: ResourceReference) => void;
}) {
  return <section className="confirmation-input-roles" aria-label="冻结的输入端口绑定">
    <strong>输入端口与数据版本</strong>
    <dl className="argument-list">{draft.workflow.nodes.flatMap(node =>
      Object.entries(node.inputs).map(([port, binding]) => <div key={`${node.nodeId}/${port}`}>
        <dt translate="no">{node.nodeId} · {port}</dt>
        <dd>{('collection' in binding ? binding.collection : [binding]).map((item, index) => {
          if ('nodeOutput' in item) return <p key={index}>来自上游 <code translate="no">{item.nodeOutput.nodeId} · {item.nodeOutput.port}</code></p>;
          const reference = action.inputReferences?.[item.workflowInput];
          return <p key={index}>
            {reference ? <button type="button" className="text-button" aria-label={`查看${port}绑定的数据版本 ${reference.versionId}`}
              onClick={() => onResource(reference.versionId, reference)}><code translate="no">{reference.versionId}</code></button>
            : '未找到冻结的数据版本'}
          </p>;
        })}</dd>
      </div>))}</dl>
  </section>;
}

export function ConfirmationCard({ confirmation, history, busy, onDecision, onEdit, onResource, onKnowledge, project, verificationSession = false, current = false, readOnly = false, capabilities, draft }: {
  confirmation: Confirmation; history: History | null; busy: boolean;
  onDecision: (id: string, decision: 'confirm' | 'reject', reason?: string) => void;
  onEdit?: (id: string) => void;
  onKnowledge?: (reference: KnowledgeReference) => void;
  draft?: WorkflowDraftPayload | null;
  onResource: (id: string, reference?: ResourceReference) => void; project: string; verificationSession?: boolean; current?: boolean; readOnly?: boolean; capabilities?: Capabilities | null;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const enabled = !readOnly && canConfirm(confirmation, history, busy);
  const parameterCount = confirmation.actions.reduce((count, action) => count + Object.keys(action.arguments).length, 0);
  const largeBatch = parameterCount > 24;
  const [expanded, setExpanded] = useState(enabled && !largeBatch && !current);
  useEffect(() => { setExpanded(enabled && !largeBatch && !current); }, [confirmation.confirmationId, enabled, largeBatch, current]);
  const pending = confirmation.status === 'PENDING';
  const rejected = confirmation.status === 'REJECTED';
  const profile = confirmationProfile(confirmation, history);
  const caseId = history?.turns.find(turn => turn.goalRevision === confirmation.goalRevision)?.progressUpdates?.find(update => update.code === 'CASE_MATCHED')?.facts.caseId;
  const profileLabel = typeof caseId === 'string' && profile ? productUi().cases[caseId]?.profiles[profile] ?? profile : null;
  const caseLabel = confirmationCase(confirmation, history);
  const inputs = confirmation.actions.flatMap(action => Object.entries(action.inputReferences ?? {})
    .map(([name, reference]) => ({ action, name, reference })));
  const inputCount = inputs.length;
  const reviewInputs = inputs.filter(({ action }) => Object.keys(action.inputReferences ?? {}).length === 1
    && selectedField(action) && productUi().models[action.modelId]?.confirmationSummary.some(group =>
      group.parameters.includes('valueField') || group.parameters.includes('column')));
  const summaryInputs = inputCount <= 2 ? reviewInputs : [];
  const summaryLinks = inputs.filter(input => !summaryInputs.includes(input)).slice(0, 2);
  function reviewField({ action, name, reference }: (typeof inputs)[number]) {
    return <div className="confirmation-input" key={`${action.toolCallId}:${name}`}>
      <ResourceLink id={reference.versionId} project={project} reference={reference} fieldName={selectedField(action)}
        evidence={recordedEvidence(history, confirmation, action, name, reference)} onResource={onResource} onKnowledge={onKnowledge} compactEvidence={current} />
    </div>;
  }
  const firstAction = confirmation.actions[0];
  const composed = isComposedConfirmation(confirmation);
  const frozenDraft = composed && firstAction?.workflowChecksum
    && draft?.compositionCheck?.definitionDigest === firstAction.workflowChecksum ? draft : undefined;
  const stats = firstAction?.arguments;
  const overview = firstAction && productUi().models[firstAction.modelId]?.resultOverview;
  const statisticsOutput = overview?.insight?.kind === 'statistics' ? overview.insight.output : undefined;
  const tabularStatistics = current && confirmation.actions.length === 1 && !!statisticsOutput
    && typeof stats?.column === 'string' && stats.column !== '';
  const summaries = firstAction ? (productUi().models[firstAction.modelId]?.confirmationSummary ?? [])
    .filter(summary => summary.parameters.every(name => firstAction.arguments[name] !== undefined && !isInputArgument(firstAction, name)))
    .map(({ label, parameters }) => {
      const values = parameters.map(name => String(firstAction.arguments[name]));
      const range = parameters.length === 2 && parameters[0].endsWith('Start') && parameters[1].endsWith('End');
      const text = range ? values.join(' 至 ') : parameters.map((name, index) => `${argumentLabel(name, firstAction.modelId)} ${values[index]}`).join(' · ');
      return <div className="summary-wide" key={label}><dt>{label}</dt><dd>{parameters.length === 1
        ? <>{argumentLabel(parameters[0], firstAction.modelId)} <span>{values[0]}</span></>
        : text}</dd></div>;
    }) : [];
  const verification = verificationSession || isVerificationConfirmation(confirmation);
  const replaced = confirmation.replacesConfirmationId ? history?.turns.flatMap(turn => turn.confirmations).find(item => item.confirmationId === confirmation.replacesConfirmationId) : undefined;
  const objective = history?.task?.spec.objective;
  const expectedOutputs = current ? composed ? frozenDraft?.workflow.outputs.map(output => output.name) ?? [] : confirmation.actions.flatMap<string | { name?: string }>(action => (
    capabilities?.models.find(model => model.modelId === action.modelId && model.modelVersion === action.modelVersion &&
      model.workflowId === action.workflowId && model.workflowVersion === action.workflowVersion)?.finalOutputs ?? []
  )).map(item => typeof item === 'string' ? item : item.name).filter((name): name is string => !!name) : [];
  const deliveries = [...new Set(expectedOutputs)];
  const priorityOutputs = overview?.sections[0]?.outputs;
  const primaryDeliveries = priorityOutputs?.filter(name => deliveries.includes(name));
  const shortDeliveries = deliveries.length > 5 ? (primaryDeliveries?.length ? primaryDeliveries : deliveries).slice(0, 3) : deliveries;
  const statsTableExpected = tabularStatistics && deliveries.includes(statisticsOutput)
    && deliveries.every(name => name === statisticsOutput || name === 'summary');
  const changes = replaced ? confirmation.actions.flatMap(action => {
    const previous = replaced.actions.find(item => item.toolCallId === action.toolCallId);
    if (!previous) return [];
    const names = new Set([...Object.keys(previous.arguments), ...Object.keys(action.arguments)]);
    return [...names].filter(name => JSON.stringify(action.arguments[name]) !== JSON.stringify(previous.arguments[name]))
      .map(name => ({ action, name, before: previous.arguments[name], after: action.arguments[name] }));
  }) : [];
  const details = <details className="confirmation-details" open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}><summary>{enabled ? '核对本批次资料与参数' : `查看本批次 ${confirmation.actions.length} 项分析的资料与参数`}</summary>
    {expanded && current && <p className="muted">执行确认 · 修订 {confirmation.revision ?? 1}</p>}
    {expanded && statsTableExpected && <p>预期成果文件 · {deliveries.map(outputName).join('、')}</p>}
    {expanded && changes.length > 0 && <dl>{changes.map(change => <div key={`${change.action.toolCallId}:${change.name}`}><dt>{argumentLabel(change.name, change.action.modelId)}</dt><dd><ConfirmationChange {...change} /></dd></div>)}</dl>}
    {expanded && confirmation.actions.map((action, index) => {
      const entries = Object.entries(action.arguments);
      const reviewedInput = reviewInputs.find(input => input.action === action);
      const groups = entries.reduce<Record<string, [string, unknown][]>>((result, entry) => { (result[argumentGroup(entry[0], action.modelId)] ??= []).push(entry); return result; }, {});
      return <section className="frozen-action" key={action.toolCallId}>
      <h4>{index + 1}. {action.tool === 'run_composed_workflow' ? '组合工作流' : modelName(action.modelId || action.tool)}</h4>
      {reviewedInput && !summaryInputs.includes(reviewedInput) && reviewField(reviewedInput)}
      {Object.entries(action.inputReferences ?? {}).filter(([name]) => !Object.hasOwn(action.arguments, name)).map(([name, reference]) => <p key={name}>
        <button type="button" className="text-button" aria-label={`查看冻结资料 ${argumentLabel(name, action.modelId)}`}
          onClick={() => onResource(reference.versionId, reference)}>
          {argumentLabel(name, action.modelId)}
        </button>
      </p>)}
      {largeBatch ? <div className="parameter-groups">{Object.entries(groups).map(([group, groupEntries]) => <details key={group} className="parameter-group"><summary>{group}<span>{groupEntries.length} 项</span></summary><ParameterList entries={groupEntries} action={action} reviewedInput={reviewedInput?.name} project={project} onResource={onResource} /></details>)}</div>
        : <ParameterList entries={entries} action={action} reviewedInput={reviewedInput?.name} project={project} onResource={onResource} />}
      <details className="technical-details"><summary>模型与资料标识</summary>
        <p>模型 {action.modelId}@{action.modelVersion} · 工作流 {action.workflowId}@{action.workflowVersion}</p>
        {Object.entries(action.inputReferences ?? {}).map(([name, reference]) => <div key={name}>
          <strong>{argumentLabel(name, action.modelId)}</strong><DataIdentity reference={reference} />
        </div>)}
      </details>
    </section>; })}</details>;
  return <section className={`confirmation-card ${enabled ? 'actionable' : ''}`} id={current ? "current-confirmation" : undefined} tabIndex={current ? -1 : undefined} data-testid="confirmation-card" data-confirmation-id={confirmation.confirmationId} aria-label={`执行确认 · ${statusLabel[confirmation.status] ?? confirmation.status}`}>
    <header className="confirmation-heading">
      <div className="card-eyebrow"><Icon name="check" />执行确认 · 修订 {confirmation.revision ?? 1}</div>
      {confirmation.decisionState ? <span role="status">正在处理决定</span>
        : <Badge tone={pending ? 'warning' : rejected ? 'error' : confirmation.status === 'CONFIRMED' ? 'success' : 'neutral'}>{statusLabel[confirmation.status] ?? confirmation.status}</Badge>}
      {!current && <h3>{enabled ? '请确认本批次的执行内容' : rejected ? '本批次已拒绝' : '本批次执行内容'}</h3>}
    </header>
    {current && objective && <p className="confirmation-purpose">{objective}</p>}
    {(confirmation.kind === 'RECOVERY' || profileLabel || (!current && verification)) && <div className="confirmation-badges">{!current && verification && <span className="badge verification-badge">验证会话 · 不代表业务执行</span>}{confirmation.kind === 'RECOVERY' && <span className="badge">失败恢复批次</span>}{profileLabel && <span className="badge profile-badge">{profileLabel}</span>}</div>}
    {!current && summaryInputs.map(reviewField)}
    {current && <div className="confirmation-key-facts">
      {changes.slice(0, 3).map(change => <div key={`${change.action.toolCallId}:${change.name}`}>{argumentLabel(change.name, change.action.modelId)} · <ConfirmationChange {...change} /></div>)}
      {tabularStatistics ? <p className="statistics-selection">统计 <strong>{String(stats.column)}</strong>{stats.unit ? `（${String(stats.unit)}）` : ''}
        {stats.groupField ? <>，按 <strong>{String(stats.groupField)}</strong> 分组。</> : '，计算总体分布。'}</p>
        : summaries.length > 0 && <dl className="confirmation-summary">{summaries}</dl>}
      <dl className="decision-facts">
        {inputCount > 0 && !composed && <div><dt>输入资料</dt><dd>{summaryInputs.map(reviewField)}
          {summaryLinks.map(({ action, name, reference }) =>
            <button key={`${action.toolCallId}:${name}`} type="button" className="text-button" onClick={() => onResource(reference.versionId, reference)}>查看{argumentLabel(name, action.modelId)}</button>)}
          {inputCount > 2 && <small>共 {inputCount} 份</small>}</dd></div>}
        <div><dt>预期成果</dt><dd>{statsTableExpected ? (stats.groupField ? '分组统计表' : '总体统计表') + '（有效数、中位数、分位数）'
        : expectedOutputs.length > 0 ? shortDeliveries.map(name => {
        const viewer = productUi().outputs[name]?.viewer;
        return outputName(name) + (viewer === 'image' ? '（图件）' : viewer === 'map' ? '（空间图层）' : viewer === 'table' ? '（表格）' : '');
      }).join('、') : composed ? '见编排草稿' : '未提供成果清单'}</dd></div></dl>
      {deliveries.length > shortDeliveries.length && <details><summary>全部 {deliveries.length} 份预期成果</summary><p>{deliveries.map(outputName).join('、')}</p></details>}
    </div>}
    {current && frozenDraft && firstAction && <ComposedInputRoles draft={frozenDraft} action={firstAction} onResource={onResource} />}
    {current && pending && composed && !frozenDraft && <p role="status">正在核验输入端口绑定…</p>}
    {confirmation.publishedOutputSteps?.map(step => <details key={step.toolCallId} className="confirmation-followup">
      <summary>分析发布后 · {modelName(step.modelId)}使用「{outputName(step.output)}」生成{step.outputs.map(outputName).join('、')}</summary>
      <dl className="argument-list">{Object.entries(step.arguments).map(([key, value]) => <div key={key}>
        <dt>{argumentLabel(key, step.modelId)}</dt><dd><ArgumentValue name={key} value={value} modelId={step.modelId} /></dd>
      </div>)}</dl>
      {step.errorCode && <ErrorNotice code={step.errorCode} title="制图准备未完成" />}
    </details>)}
    {!current && changes.length > 0 && <p className="muted">用户已修改 {changes.length} 个参数字段</p>}
    {enabled && !current ? <p className="muted">请核对以下资料与参数；只有确认后才会执行。</p>
      : rejected ? <><p className="confirmation-state-copy">本批次已拒绝，未创建工作流，也未生成分析结果。</p>{confirmation.reason && <p className="rejection-reason"><strong>拒绝原因：</strong>{confirmation.reason}</p>}</> : null}
    {!current && <dl className="confirmation-summary">
      {caseLabel && <div><dt>案例</dt><dd>{caseLabel}</dd></div>}
      <div><dt>分析任务</dt><dd>{confirmation.actions.length} 项</dd></div>
      <div><dt>输入资料</dt><dd>{inputCount} 项</dd></div>
      <div><dt>参数</dt><dd>{parameterCount} 项</dd></div>
      {profileLabel && <div><dt>执行档位</dt><dd>{profileLabel}</dd></div>}
      {summaries}
    </dl>}
    {!current && details}
    {current && verification && <small className="confirmation-verification">验证会话 · 不代表业务执行</small>}
    {(current || enabled) && <div className="decision-controls" role="group" aria-label="本批次的执行决定">
      {enabled && <div className="actions">
        <button className="primary" title="仅批准本批次，后续批次会单独确认" aria-label={current ? `确认执行修订 ${confirmation.revision ?? 1}` : undefined}
          data-testid="confirm-execution" disabled={current && composed && !frozenDraft} onClick={() => onDecision(confirmation.confirmationId, 'confirm')}>{current && (confirmation.revision ?? 1) > 1 ? `确认执行修订 ${confirmation.revision}` : '确认执行'}</button>
        {onEdit && !composed && <button className={current ? 'text-button' : 'secondary'} data-testid="edit-confirmation" onClick={() => onEdit(confirmation.confirmationId)}>编辑参数</button>}
        <button className="text-button decline-confirmation" aria-expanded={rejecting} onClick={() => setRejecting(!rejecting)}>拒绝此批次</button>
      </div>}
      {enabled && rejecting && <div className="reject-form"><label>拒绝原因<input aria-label="拒绝原因" value={reason} onChange={event => setReason(event.target.value)} placeholder="说明需要调整的地方" /></label><button disabled={!reason.trim()} onClick={() => onDecision(confirmation.confirmationId, 'reject', reason)}>提交拒绝</button></div>}
      {current && details}
    </div>}
    {!enabled && pending && !confirmation.decisionState && <p className="muted">{busy ? '正在更新会话，请等待当前操作完成后核对。' : '此批次已无法审批，请核对当前确认。'}</p>}
  </section>;
}

const resultFileExtensions: Record<string, string> = {
  'arrow-ipc-file': 'arrow', binary: 'bin', cog: 'tif', flatgeobuf: 'fgb',
  geopackage: 'gpkg', geoparquet: 'parquet', geotiff: 'tif', markdown: 'md',
  'pytorch-state-dict': 'pt', text: 'txt',
};

export function resultDownloadName(output: ResourceReference): string | true {
  if (!output.outputName) return true;
  const format = output.format.toLowerCase();
  const extension = resultFileExtensions[format] ?? format;
  const name = output.outputName.toLowerCase();
  if (name.endsWith(`.${extension}`) || name.endsWith(`.${format}`) || (extension === 'tif' && name.endsWith('.tiff'))) return output.outputName;
  return `${output.outputName}.${extension}`;
}

export type ResultPresentation = {
  publication: { label: string; description: string };
  scientificValidation?: { label: string; description: string };
  outputAccess: readonly { reference: ResourceReference; actions: readonly string[] }[];
};

export function RecoveryCard({ children, actions }: { children: ReactNode; actions: ReactNode }) {
  return <section className="workflow-recovery" aria-label="恢复未完成步骤">
    <h4>恢复未完成步骤</h4>
    {children}
    <div className="workflow-recovery-actions">{actions}</div>
  </section>;
}

function regressionMetrics(value: unknown) {
  if (typeof value !== 'object' || value === null) return null;
  const { schemaVersion, rmse, baseline, sample_count: count, unit } = value as Record<string, unknown>;
  if (schemaVersion !== 'regression-metrics.v1' || typeof rmse !== 'number' || !Number.isFinite(rmse) || rmse < 0
    || typeof count !== 'number' || !Number.isSafeInteger(count) || count < 1 || typeof unit !== 'string'
    || typeof baseline !== 'object' || baseline === null) return null;
  const { rmse: baselineRmse, sample_count: baselineCount } = baseline as Record<string, unknown>;
  if (typeof baselineRmse !== 'number' || !Number.isFinite(baselineRmse) || baselineRmse < 0 || baselineCount !== count) return null;
  return { rmse, baselineRmse, count, unit };
}

function describeRegressionMetrics({ rmse, baselineRmse, count, unit }: NonNullable<ReturnType<typeof regressionMetrics>>) {
  const number = new Intl.NumberFormat('zh-CN', { maximumSignificantDigits: 4 });
  const format = (value: number) => [number.format(value), unit].filter(Boolean).join(' ');
  return {
    comparison: rmse < baselineRmse ? '模型的均方根误差低于基线。'
      : rmse > baselineRmse ? '模型的均方根误差高于基线。' : '模型和基线的均方根误差相同。',
    values: [
      { label: '模型 RMSE', value: format(rmse) },
      { label: '基线 RMSE', value: format(baselineRmse) },
      { label: '留出样本数', value: count.toLocaleString('zh-CN') },
    ],
  };
}

export function ResultCard({ idPrefix = 'result', task, result, presentation, project, onResource, onAttach, onContinue, onCompare, continueDisabled = false, continuationVersionId = null, onPrepareContinuation, targetTime, onRecover, verificationErrors, retryVerification, verificationDeferred }: { idPrefix?: string; task: ExecutionTask; result?: Result; presentation?: ResultPresentation; project: string; onResource: (item: ResourceReference) => void; onAttach?: (item: ResourceReference) => void; onContinue?: (item: ResourceReference) => void; onCompare?: (item: ResourceReference) => void; continueDisabled?: boolean; continuationVersionId?: string | null;
  onPrepareContinuation?: (runId: string, policy: WorkflowContinuation, ref: ResourceReference) => void;
  targetTime?: string;
  onRecover?: (workflowRunId: string) => void;
  verificationErrors?: Record<string, number>;
  retryVerification?: (reference: ResourceReference) => void;
  verificationDeferred?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const matchingResult = task.status === 'SUCCEEDED' && result?.workflowRunId === task.workflowRunId
    && result.agentRunId === task.agentRunId && result.goalRevision === task.goalRevision && result.toolCallId === task.toolCallId
    && ['COMPLETE', 'SUCCEEDED'].includes(result.status) && result.remoteOutputs.length === task.outputCount;
  const outputs = presentation?.outputAccess ?? (matchingResult
    ? result.remoteOutputs.map(reference => ({ reference, actions: ['preview', 'download'] })) : []);
  const configured = task.status === 'SUCCEEDED' ? productUi().models[task.modelId]?.resultOverview : undefined;
  const overview = configured?.sections[0].outputs.some(name => outputs.some(({ reference }) => reference.outputName === name))
    ? configured : undefined;
  const orderedOutputs = overview ? outputs : [
    ...outputs.filter(({ reference }) => reference.mediaType === 'image/png'),
    ...outputs.filter(({ reference }) => reference.mediaType !== 'image/png'),
  ];
  const sections = overview?.sections.map(section => ({
    label: section.label,
    outputs: section.outputs.flatMap(name => outputs.filter(({ reference }) => reference.outputName === name)),
  }));
  const primary = sections?.[0];
  const mainTable = primary && !overview?.insight && !overview?.regressionMetrics && !primary.outputs.some(output => output.reference.mediaType === 'image/png')
    ? primary.outputs.find(({ reference, actions }) => productUi().outputs[reference.outputName ?? '']?.viewer === 'table'
      && ['csv', 'parquet', 'arrow-ipc-file'].includes(reference.format) && actions.includes('preview'))?.reference : undefined;
  const extra = sections ? outputs.filter(output => !sections.some(section => section.outputs.includes(output))) : [];
  const metricSource = overview?.regressionMetrics ? primary?.outputs.find(({ reference, actions }) =>
    reference.outputName === overview.regressionMetrics && actions.includes('preview'))?.reference : undefined;
  const metrics = regressionMetrics(usePublishedJson(project, metricSource, 16_384));
  const verified = (name?: string) => outputs.find(({ reference, actions }) => reference.outputName === name && actions.includes('preview'))?.reference;
  const continuation = productUi().models[task.modelId]?.continuation;
  const continuationOutput = continuation && verified(continuation.output);
  const report = continuation?.reviewReport ? verified(continuation.reviewReport) : undefined;
  const reviewPayload = usePublishedJson(project, onPrepareContinuation ? report : undefined);
  const reviewStatus = record(reviewPayload)?.status;
  const canContinue = continuation && continuationOutput && onPrepareContinuation && (!continuation.reviewReport || reviewStatus === 'REVIEW_REQUIRED');
  const trend = overview?.insight?.kind === 'trend' ? verified(overview.sections[0].outputs[0]) : undefined;
  const visuals = overview?.visuals?.flatMap(name => {
    const artifact = verified(name);
    return artifact && ['geotiff', 'cog', 'tif', 'tiff'].includes(artifact.format) ? [artifact] : [];
  }) ?? [];
  const summary = metrics ? describeRegressionMetrics(metrics) : null;
  const name = modelName(task.modelId);
  function renderOutput({ reference: output, actions }: ResultPresentation['outputAccess'][number]) {
    const title = output.outputName ? outputName(output.outputName) : '结果文件';
    const reuse = actions.includes('preview') && (onAttach || onContinue || onCompare) ? <div className="output-reuse-actions">
      {onAttach && <button onClick={() => onAttach(output)}>附加到当前草稿</button>}
      {onContinue && <button disabled={continueDisabled || !!continuationVersionId} onClick={() => onContinue(output)}>{continuationVersionId === output.versionId ? '正在创建对话…' : '带成果打开新会话'}</button>}
      {onCompare && <button onClick={() => onCompare(output)}>与另一版本比较</button>}
    </div> : null;
    const status = verificationErrors?.[JSON.stringify(output)];
    const identity = <details className="technical-details output-identity"><summary>数据身份</summary><DataIdentity reference={output} /></details>;
    return <div className="output-item"
      key={JSON.stringify([output.versionId, output.semanticId, output.objectId, output.checksum])} data-resource-id={output.versionId}>
      <div className="output-heading">
        {actions.includes('preview') ? <button className="text-button" onClick={() => onResource(output)}>{title}<Icon name="arrow-up-right" /></button>
          : <strong>{title}</strong>}
        <small className="output-format">{output.format}</small>
        {actions.includes('download') && <a href={contentUrl(project, output.versionId, output.objectId)} download={resultDownloadName(output)}
          aria-label={overview ? `下载 ${title}` : undefined}><Icon name="download" />下载</a>}
      </div>
      {!actions.includes('preview') && !verificationDeferred && <p className="muted">{status === 401 || status === 403
        ? '当前身份无法访问该成果。' : status !== undefined ? <>精确版本尚未通过读取核验。{retryVerification && <button className="text-button" onClick={() => retryVerification(output)}>重新核验{title}</button>}</>
          : '正在核验精确版本…'}</p>}
      {actions.includes('preview') && output.mediaType === 'image/png' && <img className="result-image" src={contentUrl(project, output.versionId, output.objectId)} alt={title} />}
      {overview && reuse ? <details className="output-more"><summary aria-label={`更多操作 · ${title}`}>更多操作</summary>{reuse}{identity}</details> : <>{identity}{reuse}</>}
    </div>;
  }
  return <section id={`${idPrefix}-${task.workflowRunId}`} tabIndex={-1} className="result-card" data-testid="result-card" data-status={task.status} data-workflow-run-id={task.workflowRunId} aria-label={`分析结果 · ${taskLabel(task)}`}>
    <header className="result-card-heading"><h3>{name}</h3>
      <Badge tone={task.status === 'SUCCEEDED' ? 'success' : ['FAILED', 'TIMED_OUT', 'CANCELLED'].includes(task.status) ? 'error' : 'neutral'}>{taskLabel(task)}</Badge>
    </header>
    {task.sourceWorkflowRunId && <p className="muted">{['FAILED', 'TIMED_OUT', 'CANCELLED'].includes(task.status)
      ? '分析已发布 · 地图生成未完成' : '同批制图 · 使用分析的已发布图层'}</p>}
    {task.startedAt && task.finishedAt && task.durationMs != null && Number.isFinite(task.durationMs) && task.durationMs >= 0
      && <small className="run-duration">执行用时 {task.durationMs / 1000} 秒</small>}
    {presentation && task.status === 'SUCCEEDED' && <dl className="result-statuses">
      <div><dt>计算</dt><dd>{taskLabel(task)}</dd></div>
      <div><dt>发布</dt><dd title={presentation.publication.description}>{presentation.publication.label}</dd></div>
      <div><dt>科学验证</dt><dd>{presentation.scientificValidation?.label ?? '科学验证状态未提供'}</dd></div>
    </dl>}
    {task.status === 'SUCCEEDED' && (task.attempt ?? 1) > 1 && <p className="recovery-success">已通过第 {task.attempt} 次执行恢复</p>}
    {overview && primary && sections ? <>
      {!overview.insight && !summary && <p className="result-description">{overview.description}</p>}
      {summary && <div className="result-metrics">
        <p>{summary.comparison}</p>
        <dl>{summary.values.map(({ label, value }) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      </div>}
      {overview.insight && <ResultInsights key={task.workflowRunId} overview={overview.insight}
        summary={verified(overview.insight.output)} trend={trend} visuals={visuals} project={project} targetTime={targetTime} />}
      {mainTable && <PrimaryTable key={mainTable.objectId} reference={mainTable} />}
      {primary.outputs.length > 0 && <section className="result-primary" aria-label={primary.label}><h3>{primary.label}</h3>{primary.outputs.map(renderOutput)}</section>}
      {[...sections.slice(1), { label: '其他成果', outputs: extra }].filter(section => section.outputs.length > 0).map(section =>
        <details className="result-output-group" key={section.label}>
          <summary>{section.label} <span>{section.outputs.length} 份</span></summary>
          {section.outputs.map(renderOutput)}
        </details>)}
      <CopyMessage label="复制结果摘要" text={() => [
        `${name}已完成，共发布 ${outputs.length} 份成果。`, overview.description, summary?.comparison,
        summary?.values.map(({ label, value }) => `${label} ${value}`).join(' · '),
        ...primary.outputs.map(({ reference }) => reference.outputName ? `- ${outputName(reference.outputName)}` : '- 结果文件'),
      ].filter(Boolean).join('\n')} />
    </> : <>
      {(expanded ? orderedOutputs : orderedOutputs.slice(0, 3)).map(renderOutput)}
      {outputs.length > 3 && <button className="text-button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? '收起结果列表' : `查看全部 ${outputs.length} 个结果`}</button>}
    </>}
    {continuationOutput && onPrepareContinuation && <div className="result-continuation">
      {reviewStatus === 'BLOCKED' && <p>评价输入缺项或口径冲突，请核对来源报告。</p>}
      {reviewStatus === 'REVIEW_REQUIRED' && <p>候选输入待复核。请核对指标来源后确认下一步。</p>}
      {report && reviewPayload !== undefined && !['BLOCKED', 'REVIEW_REQUIRED'].includes(String(reviewStatus)) && <p>暂时无法核对候选输入的复核状态。</p>}
      {report && <button type="button" className="text-button" onClick={() => onResource(report)}>查看指标来源</button>}
      {canContinue && <button type="button" className="secondary" disabled={continueDisabled}
        onClick={() => onPrepareContinuation(task.workflowRunId, continuation, continuationOutput)}>{continuation.label}</button>}
    </div>}
    {outputs.length === 0 && !(presentation && task.status === 'SUCCEEDED' && task.outputCount > 0) && <p className="muted">{task.status === 'SUCCEEDED' && task.outputCount > 0 ? '正在同步结果文件…' : ['FAILED', 'TIMED_OUT', 'CANCELLED', 'REJECTED', 'SKIPPED'].includes(task.status) ? '本次没有发布结果文件。' : '结果将在发布后显示。'}</p>}
    {task.errorCode && <ErrorNotice code={task.errorCode} title="本次分析未完成" />}
    {onRecover && <RecoveryCard actions={<button type="button" onClick={() => onRecover(task.workflowRunId)}>核对冻结资料并准备恢复</button>}>
      <p>使用原批次冻结的资料和参数。准备好恢复批次后，需要再次确认。</p>
    </RecoveryCard>}
  </section>;
}
