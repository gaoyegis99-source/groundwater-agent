import { useEffect, useState } from 'react';
import { canConfirm, statusLabel, taskLabel } from './chat-state';
import { contentUrl } from './api';
import { ArgumentValue, argumentGroup, argumentLabel, modelName, outputName } from './card-presentation';
import { ErrorNotice } from './ErrorNotice';
import { ResourceLink } from './ResourceLink';
import { Icon } from './Icon';
import type { Capabilities, Confirmation, ExecutionTask, History, ResourceReference, Result } from './types';
import { productUi } from './product-ui';
import { shortVersion } from './format';

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
function ParameterList({ entries, modelId, project, onResource }: {
  entries: [string, unknown][]; modelId: string; project: string; onResource: (id: string) => void;
}) {
  return <dl className="argument-list readable-arguments">{entries.map(([name, value]) => <div key={name}><dt>{argumentLabel(name, modelId)}</dt><dd>{(name === 'versionId' || name.endsWith('VersionId')) && typeof value === 'string'
    ? <ResourceLink id={value} project={project} onResource={onResource} />
    : <ArgumentValue value={value} name={name} modelId={modelId} />}</dd></div>)}</dl>;
}

export function ConfirmationCard({ confirmation, history, busy, onDecision, onEdit, onResource, project, verificationSession = false, current = false, capabilities }: {
  confirmation: Confirmation; history: History | null; busy: boolean;
  onDecision: (id: string, decision: 'confirm' | 'reject', reason?: string) => void;
  onEdit?: (id: string) => void;
  onResource: (id: string, reference?: ResourceReference) => void; project: string; verificationSession?: boolean; current?: boolean; capabilities?: Capabilities | null;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const enabled = canConfirm(confirmation, history, busy);
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
  const inputCount = confirmation.actions.reduce((count, action) => count + Object.keys(action.inputReferences ?? {}).length, 0);
  const firstAction = confirmation.actions[0];
  const declaredSummaries = firstAction ? productUi().models[firstAction.modelId]?.confirmationSummary ?? [] : [];
  const verification = verificationSession || isVerificationConfirmation(confirmation);
  const replaced = confirmation.replacesConfirmationId ? history?.turns.flatMap(turn => turn.confirmations).find(item => item.confirmationId === confirmation.replacesConfirmationId) : undefined;
  const changedFields = replaced ? confirmation.actions.reduce((count, action, index) => count + Object.keys(action.arguments).filter(name => JSON.stringify(action.arguments[name]) !== JSON.stringify(replaced.actions[index]?.arguments[name])).length, 0) : 0;
  const expectedOutputs = current ? confirmation.actions.flatMap<string | { name?: string }>(action => (
    capabilities?.models.find(model => model.modelId === action.modelId && model.modelVersion === action.modelVersion &&
      model.workflowId === action.workflowId && model.workflowVersion === action.workflowVersion)?.finalOutputs ?? []
  )).map(item => typeof item === 'string' ? item : item.name).filter((name): name is string => !!name) : [];
  const changes = current && replaced ? confirmation.actions.flatMap(action => {
    const before = replaced.actions.find(item => item.toolCallId === action.toolCallId)?.arguments ?? {};
    return Object.entries(action.arguments).filter(([name, value]) => JSON.stringify(value) !== JSON.stringify(before[name]))
      .map(([name, value]) => ({ action, name, before: before[name], after: value }));
  }) : [];
  const refs = current ? confirmation.actions.flatMap(action => Object.values(action.inputReferences ?? {})) : [];
  const details = <details className="confirmation-details" open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}><summary>{enabled ? '核对本批次资料与参数' : `查看本批次 ${confirmation.actions.length} 项分析的资料与参数`}</summary>
    {expanded && changes.length > 0 && <dl>{changes.map(({ action, name, before, after }) => <div key={`${action.toolCallId}:${name}`}><dt>{argumentLabel(name, action.modelId)}</dt><dd>{before === undefined ? '未设置' : <ArgumentValue name={name} modelId={action.modelId} value={before} />} → <ArgumentValue name={name} modelId={action.modelId} value={after} /></dd></div>)}</dl>}
    {expanded && confirmation.actions.map((action, index) => { const entries = Object.entries(action.arguments); const groups = entries.reduce<Record<string, [string, unknown][]>>((result, entry) => { (result[argumentGroup(entry[0], action.modelId)] ??= []).push(entry); return result; }, {}); return <section className="frozen-action" key={action.toolCallId}>
      <h4>{index + 1}. {modelName(action.modelId || action.tool)}</h4>
      <p className="muted">模型 {action.modelId}@{action.modelVersion} · 工作流 {action.workflowId}@{action.workflowVersion}</p>
      {largeBatch ? <div className="parameter-groups">{Object.entries(groups).map(([group, groupEntries]) => <details key={group} className="parameter-group"><summary>{group}<span>{groupEntries.length} 项</span></summary><ParameterList entries={groupEntries} modelId={action.modelId} project={project} onResource={onResource} /></details>)}</div>
        : <ParameterList entries={entries} modelId={action.modelId} project={project} onResource={onResource} />}
    </section>; })}</details>;
  return <section className={`confirmation-card ${enabled ? 'actionable' : ''}`} id={current ? "current-confirmation" : undefined} tabIndex={current ? -1 : undefined} data-testid="confirmation-card" data-confirmation-id={confirmation.confirmationId} aria-label={`执行确认 · ${statusLabel[confirmation.status] ?? confirmation.status}`}>
    <div className="card-eyebrow"><Icon name="check" />执行确认 · 修订 {confirmation.revision ?? 1} <span>{confirmation.decisionState ? '正在处理决定' : statusLabel[confirmation.status] ?? confirmation.status}</span></div>
    <h3>{current ? '核对当前执行批次' : enabled ? '请确认本批次的执行内容' : rejected ? '本批次已拒绝' : '本批次执行内容'}</h3>
    <div className="confirmation-badges">{verification && <span className="badge verification-badge">验证会话 · 不代表业务执行</span>}{confirmation.kind === 'RECOVERY' && <span className="badge">失败恢复批次</span>}{profileLabel && <span className="badge profile-badge">{profileLabel}</span>}</div>
    {current && <div className="confirmation-key-facts">
      {refs.length > 0 && <p>输入版本 · {refs.slice(0, 2).map((ref, index) => <span key={index}><ResourceLink id={ref.versionId} project={project} onResource={() => onResource(ref.versionId, ref)} />{' '}</span>)}</p>}
      {changes.slice(0, 2).map(({ action, name, before, after }) => <p key={`${action.toolCallId}:${name}`}>{argumentLabel(name, action.modelId)} · {before === undefined ? '未设置' : <ArgumentValue name={name} modelId={action.modelId} value={before} />} → <ArgumentValue name={name} modelId={action.modelId} value={after} /></p>)}
      {!changes.length && firstAction && declaredSummaries.slice(0, 1).map(summary => <p key={summary.label}>{summary.label} · {summary.parameters.map(name => argumentLabel(name, firstAction.modelId) + ' ' + String(firstAction.arguments[name] ?? '未设置')).join('、')}</p>)}
      <p>预期成果 · {expectedOutputs.length > 0 ? [...new Set(expectedOutputs)].map(outputName).join('、') : '未提供成果清单'}</p>
    </div>}
    {!current && changedFields > 0 && <p className="muted">用户已修改 {changedFields} 个参数字段</p>}
    {enabled && !current ? <p className="muted">请核对以下资料与参数；只有确认后才会执行。</p>
      : rejected ? <><p className="confirmation-state-copy">本批次已拒绝，未创建工作流，也未生成分析结果。</p>{confirmation.reason && <p className="rejection-reason"><strong>拒绝原因：</strong>{confirmation.reason}</p>}</>
      : confirmation.status === 'CONFIRMED' ? <p className="muted">本批次已经确认；执行状态和成果请查看后续记录。</p> : null}
    {!current && <dl className="confirmation-summary">
      {caseLabel && <div><dt>案例</dt><dd>{caseLabel}</dd></div>}
      <div><dt>分析任务</dt><dd>{confirmation.actions.length} 项</dd></div>
      <div><dt>输入资料</dt><dd>{inputCount} 项</dd></div>
      <div><dt>参数</dt><dd>{parameterCount} 项</dd></div>
      {profileLabel && <div><dt>执行档位</dt><dd>{profileLabel}</dd></div>}
      {declaredSummaries.filter(summary => summary.parameters.every(name => firstAction.arguments[name] !== undefined)).map(summary => <div className="summary-wide" key={summary.label}><dt>{summary.label}</dt><dd>{summary.parameters.length === 1
        ? <>{argumentLabel(summary.parameters[0], firstAction.modelId)} <span>{String(firstAction.arguments[summary.parameters[0]])}</span></>
        : summary.parameters.map(name => `${argumentLabel(name, firstAction.modelId)} ${String(firstAction.arguments[name])}`).join(' · ')}</dd></div>)}
    </dl>}
    {!current && details}
    {enabled && <><div className="actions">{onEdit && <button className="secondary" data-testid="edit-confirmation" onClick={() => onEdit(confirmation.confirmationId)}>编辑参数</button>}<button className="primary" title="仅批准本批次，后续批次会单独确认" data-testid="confirm-execution" onClick={() => onDecision(confirmation.confirmationId, 'confirm')}>{current ? `确认执行修订 ${confirmation.revision ?? 1}` : '确认执行'}</button><button className="text-button" onClick={() => setRejecting(!rejecting)}>拒绝此批次</button></div>
      {rejecting && <div className="reject-form"><label>拒绝原因<input aria-label="拒绝原因" value={reason} onChange={event => setReason(event.target.value)} placeholder="说明需要调整的地方" /></label><button disabled={!reason.trim()} onClick={() => onDecision(confirmation.confirmationId, 'reject', reason)}>提交拒绝</button></div>}</>}
    {current && details}
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

export function ResultCard({ task, result, project, onResource, onAttach, onContinue, onCompare, continueDisabled = false, continuationVersionId = null }: { task: ExecutionTask; result?: Result; project: string; onResource: (item: ResourceReference) => void; onAttach?: (item: ResourceReference) => void; onContinue?: (item: ResourceReference) => void; onCompare?: (item: ResourceReference) => void; continueDisabled?: boolean; continuationVersionId?: string | null }) {
  const [expanded, setExpanded] = useState(false);
  const outputs = task.status === 'SUCCEEDED' && result && result.workflowRunId === task.workflowRunId
    && ['COMPLETE', 'SUCCEEDED'].includes(result.status) && result.remoteOutputs.length === task.outputCount
    ? result.remoteOutputs : [];
  return <section className="result-card" data-testid="result-card" data-status={task.status} data-workflow-run-id={task.workflowRunId} aria-label={`分析结果 · ${taskLabel(task)}`}>
    <p className="result-review-note">结果须结合输入、参数和验收状态复核。</p>
    <div className="card-eyebrow"><Icon name="chart" />分析结果 <span>{taskLabel(task)}</span></div>
    <strong>{modelName(task.modelId)}</strong>
    {task.status === 'SUCCEEDED' && (task.attempt ?? 1) > 1 && <p className="recovery-success">已通过第 {task.attempt} 次执行恢复</p>}
    {(expanded ? outputs : outputs.slice(0, 3)).map((output: ResourceReference) => <div className="output-item" key={output.versionId} data-resource-id={output.versionId}>
      <div className="output-heading"><button className="text-button" onClick={() => onResource(output)}>{output.outputName ? outputName(output.outputName) : '结果文件'}<Icon name="arrow-up-right" /></button><small className="output-format" title={`数据版本 ${output.versionId}`}>{output.format} · 版本 {shortVersion(output.versionId)}</small><a href={contentUrl(project, output.versionId, output.objectId)} download={resultDownloadName(output)}><Icon name="download" />下载</a></div>
      <details className="technical-details output-identity"><summary>数据身份</summary><dl className="argument-list">
        <div><dt>数据集</dt><dd>{output.datasetId}</dd></div>
        <div><dt>数据版本</dt><dd>{output.versionId}</dd></div>
        <div><dt>语义版本</dt><dd>{output.semanticId}</dd></div>
        <div><dt>数据对象</dt><dd>{output.objectId}</dd></div>
        <div><dt>校验和</dt><dd>{output.checksum}</dd></div>
      </dl></details>
      {output.mediaType === 'image/png' && <img className="result-image" src={contentUrl(project, output.versionId, output.objectId)} alt={output.outputName ?? '分析结果地图'} />}
      <div className="output-reuse-actions">{onAttach && <button onClick={() => onAttach(output)}>附加到当前草稿</button>}{onContinue && <button disabled={continueDisabled || !!continuationVersionId} onClick={() => onContinue(output)}>{continuationVersionId === output.versionId ? '正在创建对话…' : '带成果打开新会话'}</button>}{onCompare && <button onClick={() => onCompare(output)}>与另一版本比较</button>}</div>
    </div>)}
    {outputs.length > 3 && <button className="text-button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? '收起结果列表' : `查看全部 ${outputs.length} 个结果`}</button>}
    {outputs.length === 0 && <p className="muted">{task.status === 'SUCCEEDED' && task.outputCount > 0 ? '正在同步结果文件…' : ['FAILED', 'TIMED_OUT', 'CANCELLED', 'REJECTED', 'SKIPPED'].includes(task.status) ? '本次没有发布结果文件。' : '结果将在发布后显示。'}</p>}
    {task.errorCode && <ErrorNotice code={task.errorCode} title="本次分析未完成" />}
  </section>;
}
