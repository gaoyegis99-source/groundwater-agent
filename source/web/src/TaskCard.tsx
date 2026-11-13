import type { History, ResourceReference, Result, TaskContext } from './types';
import { errorCodeMessage } from './api';
import { outputName } from './card-presentation';
import { selectTaskCriterion, selectTaskCriterionOutputs, selectTaskStep, selectTaskUi } from './ui-selectors';
import './task-overview.css';

export function sessionTask(history: History | null, sessionId: string) {
  const task = history?.sessionId === sessionId ? history.task : null;
  return task && (!task.spec.sessionId || task.spec.sessionId === sessionId) ? task : null;
}

function blockerDescription(reason: string) {
  if (!/^[A-Z][A-Z0-9_]*$/.test(reason)) return reason;
  const description = errorCodeMessage(reason);
  return description.startsWith('本次操作未完成。请查看技术详情') ? reason : description;
}

export function TaskHeader({ task, name, onOpen, onSupplement, onConfirmation }: {
  task: TaskContext; name?: string | null; onOpen: () => void;
  onSupplement?: () => void; onConfirmation?: () => void;
}) {
  const { state, lifecycle } = selectTaskUi(task, Boolean(onSupplement));
  const status = lifecycle?.tone === 'success' ? lifecycle : state;
  const blocker = task.progress.blockingReasons?.[0];
  const missing = state?.raw === 'WAITING_INPUT' ? task.spec.unresolvedItems[0] : undefined;
  return <section className="task-header" aria-label="当前研究任务">
    <div className="task-header-top">
      <h2>{name?.trim() || '研究任务'}</h2>
      <span className="task-badge" data-tone={status?.tone}>{status?.label}</span>
    </div>
    <p><strong>当前研究目标</strong> {task.spec.objective}</p>
    <div className="task-header-next">
      <span>{blocker ? blockerDescription(blocker) : missing ?? state?.description}</span>
      <div className="task-header-actions">
        <button type="button" onClick={onOpen}>查看任务概览</button>
        {state?.actions.includes('clarify') && onSupplement &&
          <button type="button" onClick={onSupplement}>补充任务信息</button>}
        {state?.raw === 'WAITING_CONFIRMATION' && onConfirmation &&
          <button type="button" onClick={onConfirmation}>查看当前执行确认</button>}
      </div>
    </div>
  </section>;
}

function TaskOutputLinks({ items, onResource }: {
  items: ResourceReference[]; onResource: (id: string, reference: ResourceReference) => void;
}) {
  return <ul className="task-output-links">{items.map(item =>
    <li key={JSON.stringify([item.versionId, item.semanticId, item.objectId, item.checksum])}>
      <button type="button" onClick={() => onResource(item.versionId, item)}>{item.outputName ? outputName(item.outputName) : '查看成果'} <small>{item.format.toUpperCase()}</small></button>
    </li>
  )}</ul>;
}

export function TaskCard({ task, disabled, onReview, onRequestChanges, onResource, onEvidenceTurn, turns = [], results = [] }: {
  task: TaskContext; disabled: boolean; onReview: (text: string) => void;
  onRequestChanges?: () => void;
  onResource?: (id: string, reference: ResourceReference) => void;
  onEvidenceTurn?: (revision: number) => void;
  turns?: History['turns']; results?: Result[];
}) {
  const { spec, progress } = task;
  const { state } = selectTaskUi(task, !disabled);
  const criterionById = new Map(spec.criteria.map(criterion => [criterion.id, criterion.text]));
  const stepById = new Map(spec.steps.map(step => [step.id, step.title]));
  return <section className="task-card task-overview" aria-label="主任务">
    <div className="task-detail-group">
      <div className="task-spec-heading"><h4>原始研究目标</h4><span>当前规格 · 修订 {spec.revision}</span></div>
      <p className="task-objective">{spec.objective}</p>
      {!!spec.constraints.length && <div className="task-detail-subsection"><h5>研究范围与约束</h5><ul>{spec.constraints.map(item =>
        <li key={item.id}>{item.text}{item.interpretation && <small>智能体解释：{item.interpretation}</small>}</li>
      )}</ul></div>}
      {!!spec.unresolvedItems.length && <div className="task-detail-subsection"><h5>尚未解决的缺项</h5><ul>{spec.unresolvedItems.map((item, index) =>
        <li key={index}>{item}</li>
      )}</ul></div>}
      {!!progress.blockingReasons?.length && <div className="task-detail-subsection task-blockers"><h5>任务阻塞原因</h5><ul>{progress.blockingReasons.map((item, index) =>
        <li key={index}>{blockerDescription(item)}</li>
      )}</ul></div>}
    </div>
    <div className="task-detail-group">
      <div className="task-spec-heading"><h4>科研交付条件</h4>
        {onRequestChanges && <button type="button" disabled={disabled} onClick={onRequestChanges}>在对话中提出修改</button>}
      </div>
      {spec.criteria.length ? <ul className="task-criteria">{spec.criteria.map(item => {
        const check = selectTaskCriterion(item, progress.criteria);
        const evidence = check.evidence;
        const turn = evidence && turns.find(candidate => item.kind === 'USER_REVIEW'
          ? candidate.goalRevision === evidence.reviewGoalRevision
          : candidate.agentRunId === evidence.agentRunId && (item.kind !== 'DRAFT' || candidate.workflowDraftId === evidence.workflowDraftId));
        const outputs = evidence ? selectTaskCriterionOutputs(item, evidence, progress.results, results) : [];
        return <li key={item.id}>
          <div className="task-spec-heading">
            <span>{check.kind}</span><span className="task-badge" data-tone={check.tone}>{check.label}</span>
          </div>
          <p className="task-criterion-text">{item.text}</p>
          {item.interpretation && <small>智能体解释：{item.interpretation}</small>}
          {item.kind === 'RESULT' && !!item.outputNames?.length && <small>要求成果：{item.outputNames.map(outputName).join('、')}</small>}
          {check.state === 'unknown' && <small>当前快照无法核实该条件的状态或证据。</small>}
          {evidence && <div className="task-criterion-evidence">
            {item.kind === 'USER_REVIEW' && <blockquote>{evidence.reviewQuote}</blockquote>}
            {onResource && outputs.length > 0 && <TaskOutputLinks items={outputs} onResource={onResource} />}
            {item.kind === 'RESULT' && outputs.length === 0 && <small>当前成果列表未提供与此证据匹配的版本。</small>}
            <details><summary>查看服务端证据</summary>
              <dl>
                <dt>AgentRun</dt><dd>{evidence.agentRunId}</dd>
                {evidence.workflowRunId && <><dt>WorkflowRun</dt><dd>{evidence.workflowRunId}</dd></>}
                {evidence.workflowDraftId && <><dt>草稿 ID</dt><dd>{evidence.workflowDraftId}</dd></>}
                {item.kind === 'USER_REVIEW' && evidence.reviewGoalRevision !== undefined && <><dt>评审轮次</dt><dd>{evidence.reviewGoalRevision}</dd></>}
              </dl>
              {turn && onEvidenceTurn && <button type="button" onClick={() => onEvidenceTurn(turn.goalRevision)}>查看来源对话</button>}
            </details>
          </div>}
          {item.kind === 'USER_REVIEW' && check.state === 'pending' && <button type="button" disabled={!state?.actions.includes('review')}
            onClick={() => onReview(`确认完成：${item.text}`)}>确认此项满足</button>}
        </li>;
      })}</ul> : <p className="muted">当前快照缺少完成条件。</p>}
    </div>
    {onResource && <div className="task-detail-group"><h4>任务成果</h4>
      {progress.results.length ? <TaskOutputLinks items={progress.results} onResource={onResource} />
        : <p className="muted">当前任务快照未提供成果。</p>}
    </div>}
    <div className="task-detail-group"><h4>业务步骤</h4>
      {spec.steps.length ? <ol className="task-business-steps">{spec.steps.map(step => {
        const status = selectTaskStep(step, progress.steps);
        const dependencies = step.dependsOn.map(id => stepById.get(id) ?? `未提供步骤 ${id}`);
        const criteria = step.criterionIds.map(id => criterionById.get(id) ?? `未提供条件 ${id}`);
        return <li key={step.id}>
          <strong>{step.title}</strong><span className="task-badge" data-tone={status.tone}>{status.label}</span>
          {!!dependencies.length && <small>依赖：{dependencies.join('、')}</small>}
          {!!criteria.length && <small>关联条件：{criteria.join('、')}</small>}
          {step.gaps.map((gap, index) => <small key={index}>缺口：{gap}</small>)}
        </li>;
      })}</ol> : <p className="muted">当前规格未定义业务步骤。</p>}
    </div>
    {!!spec.suggestions.length && <details><summary>任务建议</summary><ul>{spec.suggestions.map((item, index) =>
      <li key={index}>{item}</li>
    )}</ul></details>}
  </section>;
}
