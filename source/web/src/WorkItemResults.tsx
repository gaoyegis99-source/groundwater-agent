import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from './api';
import { ArgumentValue, argumentLabel, modelName, outputName } from './card-presentation';
import { DataVersionQuality } from './DataVersionQuality';
import { DataFieldQuality } from './DataFieldQuality';
import { newRequestId } from './message-routing';
import { productUi } from './product-ui';
import { isResourceReference } from './resource-item';
import { localizedSessionTitle } from './SessionNavigation';
import { useWorkItemCommand } from './use-work-item-command';
import { linkWorkItemMaterial } from './work-item-api';
import { workItemCommandCache } from './work-item-command-cache';
import {
  readWorkItemOutput, readWorkItemPublication, readWorkItemResult, readWorkItemRun, readWorkItemRunProfile,
  readWorkItemSessionEvidence, sameDataIdentity, sameWorkItemResult, workItemOutputRows, workItemResultReference,
  type WorkItemResultReference, type WorkItemRun, type WorkItemRunLink,
} from './work-item-result-evidence';
import { workItemNextStep, workItemTime } from './work-item-presentation';
import { sessionFromWorkItem } from './work-item-route';
import { WorkItemIssue } from './WorkItemForms';
import { MaterialIdentity } from './WorkItemMaterials';
import { WorkItemResultReuse } from './WorkItemResultReuse';
import { WorkItemReview } from './WorkItemReview';
import type { ResourceReference, WorkItemDetail, WorkItemLifecycleSnapshot, WorkItemMaterialCommand, WorkItemSummary } from './types';

const ArtifactViewer = lazy(() => import('./ArtifactViewer').then(module => ({ default: module.ArtifactViewer })));
const pageSize = 8;
type OutputRow = ReturnType<typeof workItemOutputRows>[number];
type PublishedResult = { linkId: string; reference: WorkItemResultReference };
export type WorkItemResultInspectorSource = {
  reference: WorkItemResultReference;
  link: WorkItemRunLink;
  assetLinkId?: string;
};

type ResultsProps = {
  detail: WorkItemDetail;
  scope: string;
  roles?: readonly string[];
  current: boolean;
  recheck: number;
  onRefresh: () => void;
  inspector?: WorkItemResultInspectorSource;
  onInspectResult?: (source: WorkItemResultInspectorSource) => void;
  onInspectorAccessLost?: () => void;
};

type ResultRead<T> = { value: T; error?: never } | { value?: never; error: unknown };

function useResultRead<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; attempt: number; result: ResultRead<T> } | null>(null);
  // Keys bind each view to its exact run/output and explicit refresh generation.
  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    const complete = (result: ResultRead<T>) => {
      if (!controller.signal.aborted) setResult({ key, attempt, result });
    };
    void load(controller.signal).then(value => complete({ value }), error => complete({ error }));
    return () => controller.abort();
  }, [key, attempt]);
  const read = result?.key === key && result.attempt === attempt ? result.result : null;
  return { read, retry: () => setAttempt(previous => previous + 1) };
}

const executionLabels: Record<string, string> = {
  QUEUED: '等待执行', RUNNING: '正在执行', SUCCEEDED: '执行成功',
  FAILED: '执行失败', CANCELLED: '执行已取消', TIMED_OUT: '执行超时',
};
const publicationLabels: Record<string, string> = {
  OPEN: '待发布', PREPARING: '待发布', READY: '待发布',
  PUBLISHED: '已发布', FAILED: '发布失败', ABORTED: '发布已中止',
};
const outputIssues: Record<string, string> = {
  MISSING: '成果缺失', INVALID: '成果身份无效', DUPLICATE: '输出名重复',
  FORMAT_MISMATCH: '成果格式与合同不符', UNEXPECTED: '输出未列入合同',
};
const outputRoles: Record<string, string> = {
  PRIMARY_RESULT: '主要成果', DIAGNOSTIC: '诊断', VISUALIZATION: '可视化', SUMMARY: '摘要',
};

function publishedAssets(detail: WorkItemDetail): PublishedResult[] {
  return detail.assets.flatMap(({ linkId, reference, state }) =>
    state === 'AVAILABLE' && reference?.kind === 'RESULT' ? [{ linkId, reference }] : []);
}

function exactRunLink(left: WorkItemRunLink, right: WorkItemRunLink) {
  return left.runId === right.runId && left.sessionId === right.sessionId && left.agentRunId === right.agentRunId
    && left.workflowRef.id === right.workflowRef.id && left.workflowRef.version === right.workflowRef.version
    && left.workflowRef.checksum === right.workflowRef.checksum;
}

function resultAccessLost(error: unknown) {
  return error instanceof ApiError && ([401, 403, 404].includes(error.status)
    || ['WORK_ITEM_REFERENCE_MISMATCH', 'WORK_ITEM_RESULT_NOT_PUBLISHED', 'DATA_VERSION_IDENTITY_MISMATCH'].includes(error.code));
}

function resultAssociationCache(scope: string, item: WorkItemSummary, { runId, publicationId, outputName, dataRef }: WorkItemResultReference) {
  const source = JSON.stringify([runId, publicationId, outputName, dataRef.kind, dataRef.service, dataRef.datasetId,
    dataRef.versionId, dataRef.semanticId, dataRef.objectId, dataRef.checksum, dataRef.mediaType, dataRef.format, dataRef.sizeBytes]);
  return workItemCommandCache<WorkItemMaterialCommand>(scope, item, 'result-association', value => {
    if (Object.keys(value).length !== 4 || !value.reference || typeof value.reference !== 'object') return false;
    const reference = value.reference as Record<string, unknown>;
    return Object.keys(reference).length === 3 && reference.kind === 'RESULT'
      && reference.runId === runId && reference.outputName === outputName;
  }, source);
}

function RunDetails({ run, link, detail, open, onOpen, recheck, verifiedModel }: {
  run: WorkItemRun; link: WorkItemRunLink; detail: WorkItemDetail; open: boolean;
  onOpen: (value: boolean) => void; recheck: number; verifiedModel: boolean;
}) {
  const goal = detail.agentRuns.find(agent => agent.agentRunId === link.agentRunId && agent.sessionId === link.sessionId)?.goalRevision;
  const source = useResultRead(open ? JSON.stringify([link.sessionId, run.runId, goal, recheck]) : null,
    signal => readWorkItemSessionEvidence(link, run, signal, undefined, goal));
  const action = source.read?.value?.action;
  const inputs: [string, ResourceReference][] = Object.entries(action?.inputReferences ?? {});
  const parameters = Object.entries(action?.arguments ?? run.arguments).filter(([name]) => !inputs.some(([role]) => role === name));

  return <details open={open} onToggle={event => onOpen(event.currentTarget.open)} className="work-result-disclosure">
    <summary>运行来源、输入和参数</summary>
    {open && <>
      <dl className="work-result-facts">
        <div><dt>可执行模型</dt><dd>{run.modelId} · {run.modelVersion}</dd></div>
        <div><dt>工作流</dt><dd>{run.workflowId} · {run.workflowVersion}</dd></div>
        <div><dt>运行</dt><dd><code>{run.runId}</code></dd></div>
        {run.startedAt && <div><dt>开始时间</dt><dd>{workItemTime(run.startedAt)}</dd></div>}
        {run.finishedAt && <div><dt>结束时间</dt><dd>{workItemTime(run.finishedAt)}</dd></div>}
        {run.error && <div><dt>执行诊断</dt><dd><code>{run.error}</code></dd></div>}
      </dl>
      {!source.read ? <p role="status">正在核对冻结输入…</p> : source.read.error
        ? <div className="work-item-actions"><span>冻结输入暂不可核验。</span><button type="button" onClick={source.retry}>重试</button></div>
        : inputs.length ? <ul className="work-item-items" aria-label="冻结输入资料">
          {inputs.map(([role, ref]) => <li key={role}><div><strong>{verifiedModel ? argumentLabel(role, run.modelId) : role}</strong>
            <MaterialIdentity reference={ref} /></div></li>)}
        </ul> : <p>{action ? '确认记录中没有 Data 输入引用。' : '未取得与本次执行匹配的冻结输入。'}</p>}
      {parameters.length > 0 && <><p>{action ? '冻结参数' : '执行记录参数'}</p><dl className="argument-list readable-arguments">
        {parameters.map(([name, value]) => <div key={name}><dt>{verifiedModel ? argumentLabel(name, run.modelId) : name}</dt>
          <dd><ArgumentValue value={value} name={verifiedModel ? name : undefined} modelId={run.modelId} /></dd></div>)}
      </dl></>}
    </>}
  </details>;
}

function ResultInspection({ reference, linked, row, run, link, historical, taskRevision, openDiagnostic, props }: {
  reference: WorkItemResultReference;
  linked: PublishedResult | undefined;
  row: OutputRow;
  run: WorkItemRun;
  link: WorkItemRunLink;
  historical: boolean;
  taskRevision: number | null | undefined;
  openDiagnostic?: () => void;
  props: ResultsProps;
}) {
  const { detail, scope, current, recheck, onRefresh, inspector, onInspectResult, onInspectorAccessLost } = props;
  const item = detail.workItem;
  const key = JSON.stringify([reference, item.workItemId, recheck, linked?.linkId, historical]);
  const version = useResultRead(key, signal => historical
    ? readWorkItemResult(reference, item.projectId, signal, link,
      detail.assets.find(asset => asset.linkId === linked?.linkId)).then(result => result.version)
    : readWorkItemOutput(reference.dataRef, item.projectId, reference.publicationId, signal));
  const goal = detail.agentRuns.find(agent => agent.agentRunId === link.agentRunId && agent.sessionId === link.sessionId)?.goalRevision;
  const evidence = useResultRead(version.read?.value ? JSON.stringify([key, goal, taskRevision]) : null,
    signal => readWorkItemSessionEvidence(link, run, signal, reference, goal));
  const [showQuality, setShowQuality] = useState(false);
  const cache = resultAssociationCache(scope, item, reference);
  const [restored] = useState(cache.read);
  const association = useWorkItemCommand<WorkItemMaterialCommand>(
    (command, signal) => linkWorkItemMaterial(item.workItemId, command, signal), onRefresh,
    { restored, remember: cache.remember, onRejected: onRefresh },
  );
  const target = useRef<HTMLHeadingElement>(null);
  useEffect(() => { target.current?.focus(); }, []);
  const inspectionLost = !!inspector && current && sameWorkItemResult(inspector.reference, reference)
    && resultAccessLost(version.read?.error);
  useEffect(() => { if (inspectionLost) onInspectorAccessLost?.(); }, [inspectionLost, onInspectorAccessLost]);
  const resource = version.read?.value;
  const title = outputName(reference.outputName);
  const taskAdoption = evidence.read?.value?.taskAdoption;
  const diagnostic = row.expected?.qualitySource;
  const taskLabel = taskAdoption === 'ADOPTED' ? '已作为任务结果'
    : taskAdoption === 'NOT_ADOPTED' ? '当前任务尚未采用' : '采用状态待核验';

  return <section className="work-result-inspection" aria-labelledby="work-result-heading">
    <h4 id="work-result-heading" tabIndex={-1} ref={target}>{title}</h4>
    {!version.read ? <p role="status">正在核对精确成果版本…</p> : !resource ? <div className="work-item-issue" role="alert">
      <span>成果不可用或当前身份无权读取。</span><button type="button" onClick={version.retry}>重新核对成果</button>
    </div> : <>
      {row.expected && <p>{outputRoles[row.expected.role]} · {row.expected.meaning}</p>}
      {row.expected?.unit && <p>成果合同单位 · {row.expected.unit}</p>}
      <dl className="work-result-facts">
        <div><dt>发布</dt><dd>{historical ? '已核对历史发布' : '已发布'}</dd></div>
        <div><dt>任务采用</dt><dd>{taskLabel}</dd></div>
        <div><dt>工作关联</dt><dd>{linked ? '已关联到工作' : '待关联到工作'}</dd></div>
      </dl>
      {!!evidence.read?.error && <button type="button" onClick={evidence.retry}>重新核对任务采用</button>}
      <details className="work-result-disclosure">
        <summary>精确发布身份</summary>
        <dl className="work-result-facts">
          <div><dt>具名输出</dt><dd>{reference.outputName}</dd></div>
          <div><dt>发布记录</dt><dd><code>{reference.publicationId}</code></dd></div>
          {resource.createdAt && <div><dt>资料登记时间</dt><dd>{workItemTime(resource.createdAt)}</dd></div>}
        </dl>
        <MaterialIdentity reference={reference.dataRef} />
      </details>
      {current && onInspectResult && <button type="button" onClick={() => onInspectResult({ reference, link, assetLinkId: linked?.linkId })}>
        在数据工作台查看
      </button>}
      <Suspense fallback={<p role="status">正在加载成果查看器…</p>}>
        <ArtifactViewer resource={resource} project={item.projectId} objectId={reference.dataRef.objectId}
          contextHeading="h4" preferredViewer={productUi().outputs[reference.outputName]?.viewer ?? 'auto'} />
      </Suspense>
      {diagnostic && <details className="work-result-disclosure">
        <summary>工作流质量诊断</summary>
        <p>合同指定 {outputName(diagnostic.outputName)} 中的诊断字段。</p>
        <ul>{diagnostic.fields.map(field => <li key={field}><code>{field}</code></li>)}</ul>
        {diagnostic.outputName === reference.outputName ? <p>诊断值见当前成果查看器。</p>
          : openDiagnostic ? <button type="button" onClick={openDiagnostic}>查看诊断 · {outputName(diagnostic.outputName)}</button>
          : <p>合同指定的诊断成果暂不可核对。</p>}
      </details>}
      <details className="work-result-disclosure" onToggle={event => setShowQuality(event.currentTarget.open)}>
        <summary>Data 质量检查</summary>
        {showQuality && <><DataVersionQuality version={resource} />
          {!!resource.descriptor.fields?.length && <DataFieldQuality descriptor={resource.descriptor} semantics={resource.semantics} />}</>}
      </details>
      {!association.unsettled && item.status === 'ACTIVE' && !linked && <button type="button" disabled={!current} onClick={() => {
        if (!current) return;
        void association.submit({
          projectId: item.projectId, requestId: newRequestId(), expectedRevision: item.revision,
          reference: { kind: 'RESULT', runId: reference.runId, outputName: reference.outputName },
        });
      }}>关联到此工作</button>}
      {linked && <WorkItemResultReuse detail={detail} scope={scope} sourceSessionId={link.sessionId}
        assetLinkId={linked.linkId} reference={reference} disabled={!current || item.status !== 'ACTIVE'} />}
    </>}
    <WorkItemIssue error={association.error} />
    {association.unsettled && <div className="work-item-actions">
      <span role="status">{association.busy ? '正在关联…' : '成果关联回执待核对。'}</span>
      {!association.busy && <button type="button" disabled={!current} onClick={() => {
        if (current && association.unsettled) void association.submit(association.unsettled);
      }}>重试关联成果</button>}
    </div>}
  </section>;
}

function RunResults({ link, snapshot, props }: {
  link: WorkItemRunLink;
  snapshot: WorkItemLifecycleSnapshot | null;
  props: ResultsProps;
}) {
  const { detail, recheck, roles, current, onInspectorAccessLost } = props;
  const { projectId, workItemId } = detail.workItem;
  const inspector = props.inspector?.reference.runId === link.runId ? props.inspector : undefined;
  const [selectedOutput, setSelected] = useState<string | null>(null);
  const selected = inspector?.reference.outputName ?? selectedOutput;
  const [count, setCount] = useState(pageSize);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const track = snapshot?.tracks.find(item => item.sessionId === link.sessionId);
  const delivery = track?.publications.find(item => item.runId === link.runId);
  const key = JSON.stringify([link, projectId, recheck, track?.state, track?.reason,
    delivery?.state]);
  const runRead = useResultRead(key, signal => readWorkItemRun(link, projectId, signal));
  const run = runRead.read?.value;
  const publication = useResultRead(run?.status === 'SUCCEEDED' ? key : null,
    signal => readWorkItemPublication(link.runId, projectId, signal));
  const pub = publication.read?.value;
  const title = localizedSessionTitle(detail.sessions.find(item => item.sessionId === link.sessionId)?.title ?? null);
  const approvedAssets = publishedAssets(detail).filter(asset => asset.reference.runId === link.runId);
  const pubId = pub?.state === 'PUBLISHED' ? pub.publicationId : null;
  const pubError = publication.read?.error;
  const inspectedReference = inspector?.reference;
  const matchingOutputs = inspectedReference && run?.outputs.filter(output => output.outputName === inspectedReference.outputName);
  const output = matchingOutputs?.[0];
  const inspectionLost = !!inspectedReference && current && (
    resultAccessLost(runRead.read?.error) || resultAccessLost(pubError)
    || (!!run && (run.status !== 'SUCCEEDED' || matchingOutputs?.length !== 1
      || !isResourceReference(output) || !sameDataIdentity(output, inspectedReference.dataRef)))
    || (!!pub?.state && pubId !== inspectedReference.publicationId)
  );
  useEffect(() => { if (inspectionLost) onInspectorAccessLost?.(); }, [inspectionLost, onInspectorAccessLost]);
  const historical = !!publication.read && (!pub?.state &&
    (!pubError || (pubError instanceof ApiError && pubError.code === 'WORKFLOW_PUBLICATION_UNAVAILABLE')));
  const inspectable = !!pubId || (historical && approvedAssets.length > 0);
  const profileRead = useResultRead(run && inspectable ? JSON.stringify([key, roles]) : null,
    signal => readWorkItemRunProfile(link, run!, productUi(), signal, roles));
  const profile = profileRead.read?.value;
  const rows = run && inspectable
    ? workItemOutputRows(run, profile?.state === 'VERIFIED' ? profile.profile : undefined) : [];
  const readableRows = rows.filter(row => !historical || approvedAssets.some(asset => asset.reference.outputName === row.name));
  const visibleRows = [...readableRows.filter(row => !Object.hasOwn(outputIssues, row.state)),
    ...readableRows.filter(row => Object.hasOwn(outputIssues, row.state))];
  const contractIssues = rows.filter(row => Object.hasOwn(outputIssues, row.state));
  const rowReference = (row: OutputRow) => {
    const { output } = row;
    if (!output || Object.hasOwn(outputIssues, row.state) || profile?.state === 'MISMATCH') return undefined;
    if (pubId) return workItemResultReference(link.runId, pubId, output);
    return approvedAssets.find(asset => asset.reference.outputName === row.name
      && sameDataIdentity(asset.reference.dataRef, output))?.reference;
  };
  const activeRow = visibleRows.find(row => row.name === selected);
  const reference = activeRow && rowReference(activeRow);
  const activeAsset = reference && approvedAssets.find(asset => sameWorkItemResult(asset.reference, reference));
  const diagnosticRow = visibleRows.find(row => row.name === activeRow?.expected?.qualitySource?.outputName);
  function openOutput(name: string) {
    const index = visibleRows.findIndex(row => row.name === name);
    if (index >= count) setCount(Math.ceil((index + 1) / pageSize) * pageSize);
    setSelected(name);
  }

  if (!run) return runRead.read
    ? <div className="work-item-issue" role="alert">该运行暂不可读。<button type="button" onClick={runRead.retry}>重试读取运行</button></div>
    : <p role="status">正在读取执行记录…</p>;
  return <div className="work-result-run">
    <h3>{modelName(run.modelId)}</h3>
    <dl className="work-result-facts">
      <div><dt>执行状态</dt><dd>{executionLabels[run.status] ?? '执行状态待核验'}</dd></div>
      <div><dt>发布状态</dt><dd>{run.status !== 'SUCCEEDED' ? '尚未进入发布' : pub?.state
        ? publicationLabels[pub.state] : publication.read ? '发布状态待核验' : '正在核对发布'}</dd></div>
      {delivery && <div><dt>本次交付</dt><dd>{track?.acceptedAt ? '工作交付已验收'
        : workItemNextStep(track?.reason ?? '') ?? '等待核对交付'}</dd></div>}
    </dl>
    {run.status === 'SUCCEEDED' && publication.read && pub?.state !== 'PUBLISHED' &&
      <button type="button" onClick={publication.retry}>重新核对发布</button>}
    <a href={sessionFromWorkItem(`#/sessions/${encodeURIComponent(link.sessionId)}/workbench/tasks`, workItemId)}>{title} · 查看来源会话</a>
    <RunDetails run={run} link={link} detail={detail} open={detailsOpen} onOpen={setDetailsOpen}
      recheck={recheck} verifiedModel={profile?.state === 'VERIFIED'} />
    {inspectable && <>
      {profile?.state === 'VERIFIED' && <div className="work-result-contract">
        <h4>{profile.profile.label}</h4>
        <p>{profile.capability.description}</p>
        <details><summary>成果合同与适用限制</summary>
          <p>业务档位 {profile.profile.profileRef.version} · 成果合同 {profile.profile.resultContractRef.version}</p>
          <p><code>{profile.profile.resultContractRef.id}</code></p>
          <ul>{profile.profile.limitations.map(limit => <li key={limit}>{limit}</li>)}</ul>
        </details>
      </div>}
      {profile?.state === 'MISMATCH' && <p role="alert">运行与冻结的成果合同不一致，需核对运行来源。</p>}
      {profileRead.read && !profileRead.read.error && profile?.state === 'UNVERIFIED' && <p>此运行的成果合同待核验。</p>}
      {profileRead.read?.error && <div className="work-item-actions"><span>成果合同暂不可核验。</span>
        <button type="button" onClick={profileRead.retry}>重试核对合同</button></div>}
      {contractIssues.length > 0 && <p role="status">有 {contractIssues.length} 个具名输出待核对。</p>}
      {!visibleRows.length ? <p>未找到具名成果。</p> : <ul className="work-item-items work-result-outputs" aria-label="具名输出">
        {visibleRows.slice(0, count).map(row => {
          const verifiedReference = rowReference(row);
          const pending = verifiedReference && profileRead.read && selected !== row.name
            && resultAssociationCache(props.scope, detail.workItem, verifiedReference).read();
          return <li key={row.name}>
            <div><strong>{outputName(row.name)}</strong>
              {Object.hasOwn(outputIssues, row.state) ? <span>{outputIssues[row.state]}</span>
                : row.expected ? <span>{outputRoles[row.expected.role]} · {row.expected.format.toUpperCase()}</span>
                : row.output ? <span>{row.output.format.toUpperCase()}</span> : null}
              {pending && <span className="work-item-meta">成果关联回执待核对</span>}</div>
            {verifiedReference && profileRead.read && <button type="button" aria-expanded={selected === row.name}
              aria-label={`查看成果 · ${outputName(row.name)}`}
              onClick={() => selected === row.name ? setSelected(null) : openOutput(row.name)}>{selected === row.name ? '收起查看' : '查看成果'}</button>}
          </li>;
        })}
      </ul>}
      {visibleRows.length > count && <button type="button" onClick={() => setCount(previous => previous + pageSize)}>显示更多成果</button>}
      {reference && activeRow && profileRead.read && <ResultInspection key={JSON.stringify([reference.runId, reference.outputName, reference.publicationId, reference.dataRef])}
        reference={reference} linked={activeAsset} row={activeRow} run={run} link={link}
        historical={historical} taskRevision={track?.taskRevision}
        openDiagnostic={diagnosticRow && rowReference(diagnosticRow) ? () => openOutput(diagnosticRow.name) : undefined} props={props} />}
    </>}
  </div>;
}

export function WorkItemResults({ snapshot, ...props }: ResultsProps & { snapshot: WorkItemLifecycleSnapshot | null }) {
  const { detail, scope, current, onRefresh, inspector, onInspectorAccessLost } = props;
  const [selectedRunId, setSelectedRunId] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const closeInspector = useCallback(() => {
    if (!onInspectorAccessLost) return;
    onInspectorAccessLost();
    requestAnimationFrame(() => heading.current?.focus());
  }, [onInspectorAccessLost]);
  const choices = detail.workflowRuns.filter(run => detail.sessions.some(session => session.sessionId === run.sessionId));
  const inspectedRun = inspector && choices.find(link => exactRunLink(link, inspector.link));
  const inspectionLost = !!inspector && current && (!inspectedRun || (!!inspector.assetLinkId && !detail.assets.some(asset =>
    asset.linkId === inspector.assetLinkId && asset.state === 'AVAILABLE' && asset.reference?.kind === 'RESULT'
    && sameWorkItemResult(asset.reference, inspector.reference),
  )));
  useEffect(() => { if (inspectionLost) closeInspector(); }, [inspectionLost, closeInspector]);
  const deliveryIds = new Set(snapshot?.tracks.flatMap(track => track.publications.map(publication => publication.runId)));
  const run = inspectedRun ?? choices.find(link => link.runId === selectedRunId) ?? choices.find(link => deliveryIds.has(link.runId)) ?? choices.at(-1);
  const latestReason = snapshot && workItemNextStep(snapshot.reason);
  return <section className="work-item-section work-result-center" aria-labelledby="work-item-results">
    <h2 id="work-item-results" tabIndex={-1} ref={heading}>成果中心</h2>
    {choices.length > 1 && <label className="work-result-picker">选择计算记录
      <select value={run?.runId ?? ''} onChange={event => setSelectedRunId(event.target.value)}>
        {choices.map(choice => <option key={choice.runId} value={choice.runId}>
          {localizedSessionTitle(detail.sessions.find(session => session.sessionId === choice.sessionId)?.title ?? null)} · {choice.runId.slice(0, 8)}
        </option>)}
      </select>
    </label>}
    {run ? <RunResults key={run.runId} link={run} snapshot={snapshot} props={{ ...props, onInspectorAccessLost: closeInspector }} />
      : <p>尚无执行记录。{latestReason && ` ${latestReason}。`}</p>}
    {snapshot?.tracks.some(track => track.review || track.acceptedAt ||
      ['WAITING_REVIEW', 'RESULT_REVIEW'].includes(track.state)) && <h3>业务验收</h3>}
    {snapshot?.tracks.map(track => <WorkItemReview key={track.sessionId} detail={detail}
      track={track} scope={scope} current={current} onRefresh={onRefresh} />)}
  </section>;
}
