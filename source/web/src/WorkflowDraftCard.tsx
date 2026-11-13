import type { KnowledgeReference, WorkflowDraft } from './types';
import { ArgumentValue, argumentLabel, outputName } from './card-presentation';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { WorkflowDraftGraph } from './WorkflowDraftGraph';

const sourceNames = { USER: '用户说明', DATA: '数据', KNOWLEDGE: '知识', OPERATOR_DEFAULT: '算子默认值' };

export function WorkflowDraftCard({ draft, onKnowledge, onResource }: {
  draft: WorkflowDraft; onKnowledge: (reference: KnowledgeReference) => void; onResource: (id: string) => void;
}) {
  const payload = draft.payload;
  const state = { GENERATING: '正在编排', COMPLETE: payload ? '已生成' : '未生成', FAILED: '未完成', CANCELLED: '已停止', SUPERSEDED: '已替代' }[draft.status];
  const nodeName = (id: string) => `第 ${payload!.workflow.nodes.findIndex(node => node.nodeId === id) + 1} 步 · ${payload!.operators.find(operator => operator.nodeId === id)!.title}`;
  return <section className="workflow-draft-card" data-testid="workflow-draft-card" data-draft-id={draft.draftId} aria-label="工作流编排草稿">
    <div className="card-eyebrow"><Icon name="branch" />编排草稿 <span>{state}</span></div>
    <p className="draft-notice">仅草稿，不可执行。</p>
    {!payload ? draft.status === 'FAILED' ? <ErrorNotice code={draft.errorCode!} diagnostics={draft.diagnostics} title="编排未完成" /> : <p className="muted" role="status">
      {draft.status === 'GENERATING' ? '正在查阅可用方法与当前资料，整理分析步骤。'
        : draft.status === 'CANCELLED' ? '编排已停止。'
        : draft.status === 'SUPERSEDED' ? '本次编排已由新的要求替代。'
        : '未生成可用草稿，请查看本轮回复中的说明。'}
    </p> : <>
      <h3>{payload.title}</h3><p className="prose">{payload.summary}</p>
      <div className="draft-overview"><span>{payload.workflow.nodes.length} 个分析步骤</span><span>{payload.workflow.inputs.length} 份所需资料</span><span>{payload.workflow.outputs.length} 项预期输出</span></div>
      {(payload.missingInputs.length > 0 || payload.unresolvedItems.length > 0) && <section className="draft-missing" data-testid="draft-missing"><h4>待补充与待核实</h4>
        {payload.missingInputs.length > 0 && <ul>{payload.missingInputs.map(input => <li key={`${input.kind}:${input.name}`}>
          <strong>{input.kind === 'DATA' ? '缺少数据' : '缺少参数'}：{argumentLabel(input.name)}</strong>
          <ul className="muted">{input.requiredBy.map(item => <li key={`${item.nodeId}:${item.port}`}>
            <span>用于 {nodeName(item.nodeId)}</span> · {input.kind === 'DATA' ? '输入端口' : '参数'} <code>{item.port}</code>
          </li>)}</ul>
        </li>)}</ul>}
        {payload.unresolvedItems.length > 0 && <ul>{payload.unresolvedItems.map((item, index) => <li key={index}>{item}</li>)}</ul>}
      </section>}
      <h4>依赖关系图</h4><WorkflowDraftGraph draftId={draft.draftId} payload={payload} />
      <h4>分析步骤</h4>
      <ol className="draft-nodes" data-testid="draft-nodes">{payload.workflow.nodes.map((node, index) => {
        const operator = payload.operators.find(item => item.nodeId === node.nodeId)!;
        return <li key={node.nodeId} data-node-id={node.nodeId}>
          <span className="draft-node-number">{String(index + 1).padStart(2, '0')}</span>
          <div className="draft-node-body"><strong>{operator.title}</strong>
            <p className="muted">使用：{Object.values(node.inputs).map(binding => 'workflowInput' in binding ? `输入资料「${argumentLabel(binding.workflowInput)}」` : `${nodeName(binding.nodeOutput.nodeId)}的输出`).join('；')}</p>
          </div>
        </li>;
      })}</ol>
      <h4>预期输出</h4><ul>{payload.workflow.outputs.map(output => <li key={output.name}>{outputName(output.name)}</li>)}</ul>
      {payload.dataBindings.length > 0 && <><h4>已绑定数据</h4><div className="draft-references">{payload.dataBindings.map(binding => <button className="citation" key={binding.inputName} onClick={() => onResource(binding.dataRef.versionId)}><Icon name="file" />{argumentLabel(binding.inputName)} · {binding.dataRef.versionId} · {binding.dataRef.format}</button>)}</div></>}
      {payload.parameterSources.length > 0 && <><h4>参数值与来源</h4><dl className="argument-list readable-arguments">{payload.parameterSources.map(parameter => <div key={parameter.name}><dt>{argumentLabel(parameter.name)}</dt><dd><ArgumentValue value={payload.parameterValues[parameter.name]} name={parameter.name} /><p className="muted">{sourceNames[parameter.source]} · {parameter.explanation}</p></dd></div>)}</dl></>}
      <details className="draft-evidence"><summary>输入格式与知识依据</summary>
        {payload.workflow.inputs.length > 0 && <><h4>所需资料格式</h4><dl className="argument-list readable-arguments">{payload.workflow.inputs.map(input => <div key={input.name}><dt>{argumentLabel(input.name)}</dt><dd>{input.formats.join(' / ')} · {input.mediaTypes.join(' / ')} · 单份输入</dd></div>)}</dl></>}
        {payload.knowledgeRefs.length > 0 && <><h4>知识依据</h4><div className="citation-list">{payload.knowledgeRefs.map((reference, index) => <button className="citation" key={`${reference.versionId}:${reference.unitId}`} data-knowledge-version-id={reference.versionId} data-knowledge-unit-id={reference.unitId} onClick={() => onKnowledge(reference)}><Icon name="book" />知识依据 {index + 1} · {reference.unitId}</button>)}</div></>}
      </details>
      {payload.limitations.length > 0 && <div className="limitations"><strong>适用限制</strong><ul>{payload.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></div>}
      {draft.status === 'COMPLETE' && <div className="draft-footer"><a className="secondary" aria-label="导出草稿 JSON" href={`/api/v12/sessions/${draft.sessionId}/workflow-drafts/${draft.draftId}/export`} download>导出 JSON<Icon name="download" /></a></div>}
    </>}
  </section>;
}
