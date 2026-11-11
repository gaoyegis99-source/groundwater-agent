import { useMemo, useState } from 'react';
import type { WorkflowDefinition, WorkflowDraftPayload } from './types';
import { ArgumentValue, argumentLabel, outputName } from './card-presentation';

type WorkflowNode = WorkflowDefinition['nodes'][number];
type DraftOperator = WorkflowDraftPayload['operators'][number];
type MissingInput = WorkflowDraftPayload['missingInputs'][number];

type Point = { x: number; y: number };
type InputVisual = {
  input: WorkflowDefinition['inputs'][number];
  x: number;
  y: number;
  missing: boolean;
};
type NodeVisual = {
  node: WorkflowNode;
  operator: DraftOperator;
  x: number;
  y: number;
  level: number;
  outputPorts: string[];
  terminalOutputCount: number;
};
type MissingVisual = { input: MissingInput; x: number; y: number };

const INPUT_WIDTH = 150;
const INPUT_HEIGHT = 64;
const NODE_WIDTH = 190;
const NODE_HEIGHT = 82;
const COLUMN_STEP = 250;
const ROW_STEP = 112;
const TOP = 56;
const LEFT = 22;

const sourceNames = {
  USER: '用户说明', DATA: '数据', KNOWLEDGE: '知识', OPERATOR_DEFAULT: '算子默认值',
};

function unique(values: string[]) {
  return [...new Set(values)];
}

function compact(value: string, limit: number) {
  return value.length <= limit ? value : `${value.slice(0, limit - 1)}…`;
}

function safeId(value: string) {
  return value.replace(/[^a-zA-Z0-9_-]/g, '-');
}

function outputPorts(workflow: WorkflowDefinition, nodeId: string) {
  const consumed = workflow.nodes.flatMap(node => Object.values(node.inputs).flatMap(binding => (
    'nodeOutput' in binding && binding.nodeOutput.nodeId === nodeId
      ? [binding.nodeOutput.port]
      : []
  )));
  const published = workflow.outputs.flatMap(output => (
    output.nodeOutput.nodeId === nodeId ? [output.nodeOutput.port] : []
  ));
  return unique([...consumed, ...published]);
}

function nodeLevels(workflow: WorkflowDefinition) {
  const levels = new Map<string, number>();
  const remaining = new Set(workflow.nodes.map(node => node.nodeId));
  while (remaining.size > 0) {
    let progressed = false;
    for (const node of workflow.nodes) {
      if (!remaining.has(node.nodeId)) continue;
      const dependencies = unique(Object.values(node.inputs).flatMap(binding => (
        'nodeOutput' in binding ? [binding.nodeOutput.nodeId] : []
      ))).filter(nodeId => remaining.has(nodeId) || levels.has(nodeId));
      if (dependencies.every(nodeId => levels.has(nodeId))) {
        levels.set(node.nodeId, Math.max(1, ...dependencies.map(nodeId => levels.get(nodeId)! + 1)));
        remaining.delete(node.nodeId);
        progressed = true;
      }
    }
    if (!progressed) {
      // The compiler owns cycle rejection. Keep a malformed diagnostic payload
      // inspectable instead of allowing a view-only layout to loop forever.
      remaining.forEach(nodeId => levels.set(nodeId, 1));
      break;
    }
  }
  return levels;
}

function buildLayout(payload: WorkflowDraftPayload) {
  const workflow = payload.workflow;
  const levels = nodeLevels(workflow);
  const nodesByLevel = new Map<number, WorkflowNode[]>();
  workflow.nodes.forEach(node => {
    const level = levels.get(node.nodeId) ?? 1;
    nodesByLevel.set(level, [...(nodesByLevel.get(level) ?? []), node]);
  });
  const inputs: InputVisual[] = workflow.inputs.map((input, index) => ({
    input,
    x: LEFT,
    y: TOP + index * ROW_STEP,
    missing: payload.missingInputs.some(item => item.kind === 'DATA' && item.name === input.name),
  }));
  const nodes: NodeVisual[] = [];
  [...nodesByLevel.entries()].sort(([left], [right]) => left - right).forEach(([level, levelNodes]) => {
    levelNodes.forEach((node, index) => {
      const operator = payload.operators.find(item => item.nodeId === node.nodeId) ?? {
        nodeId: node.nodeId,
        operatorId: node.operatorId,
        operatorVersion: node.operatorVersion,
        implementationRevision: '',
        runtime: '',
        title: node.operatorId,
      };
      nodes.push({
        node,
        operator,
        x: LEFT + level * COLUMN_STEP,
        y: TOP + index * ROW_STEP,
        level,
        outputPorts: outputPorts(workflow, node.nodeId),
        terminalOutputCount: workflow.outputs.filter(output => output.nodeOutput.nodeId === node.nodeId).length,
      });
    });
  });
  const maxLevel = Math.max(1, ...nodes.map(node => node.level));
  const graphEnd = LEFT + maxLevel * COLUMN_STEP + NODE_WIDTH;
  const missingX = graphEnd + 54;
  const missing: MissingVisual[] = payload.missingInputs.map((input, index) => ({
    input, x: missingX, y: TOP + index * 50,
  }));
  const rowCount = Math.max(
    1,
    inputs.length,
    missing.length,
    ...[...nodesByLevel.values()].map(items => items.length),
  );
  return {
    inputs,
    nodes,
    missing,
    width: missing.length > 0 ? missingX + 224 : graphEnd + 28,
    height: Math.max(170, TOP + rowCount * ROW_STEP + 22),
  };
}

function inputAnchor(visual: InputVisual): Point {
  return { x: visual.x + INPUT_WIDTH, y: visual.y + INPUT_HEIGHT / 2 };
}

function nodeInputAnchor(visual: NodeVisual, port: string): Point {
  const ports = Object.keys(visual.node.inputs);
  const index = Math.max(0, ports.indexOf(port));
  return {
    x: visual.x,
    y: visual.y + 24 + ((index + 1) * (NODE_HEIGHT - 30) / (ports.length + 1)),
  };
}

function nodeOutputAnchor(visual: NodeVisual, port: string): Point {
  const index = Math.max(0, visual.outputPorts.indexOf(port));
  return {
    x: visual.x + NODE_WIDTH,
    y: visual.y + 24 + ((index + 1) * (NODE_HEIGHT - 30) / (visual.outputPorts.length + 1)),
  };
}

function edgePath(source: Point, target: Point) {
  const bend = Math.max(38, Math.abs(target.x - source.x) * 0.42);
  return `M ${source.x} ${source.y} C ${source.x + bend} ${source.y}, ${target.x - bend} ${target.y}, ${target.x} ${target.y}`;
}

function NodeDetail({ payload, visual, detailId }: {
  payload: WorkflowDraftPayload;
  visual: NodeVisual;
  detailId: string;
}) {
  const { node, operator, outputPorts: ports } = visual;
  const published = payload.workflow.outputs.filter(output => output.nodeOutput.nodeId === node.nodeId);
  return <section className="draft-node-detail" id={detailId} data-testid="draft-node-detail" aria-label={`${operator.title}节点详情`}>
    <div className="draft-node-detail-heading"><div><strong>{operator.title}</strong><p className="mono">节点 <code>{node.nodeId}</code> · {node.operatorId}@{node.operatorVersion}</p></div><span>{Object.keys(node.inputs).length} 入 · {ports.length} 出</span></div>
    <div className="draft-node-detail-grid">
      <section><h5>输入端口</h5>{Object.keys(node.inputs).length > 0 ? <dl>{Object.entries(node.inputs).map(([port, binding]) => <div key={port}><dt><code>{port}</code></dt><dd>{'workflowInput' in binding ? <>工作流输入 <code>{binding.workflowInput}</code></> : <>节点 <code>{binding.nodeOutput.nodeId}</code> · 输出端口 <code>{binding.nodeOutput.port}</code></>}</dd></div>)}</dl> : <p className="muted">无数据输入端口</p>}</section>
      <section><h5>输出端口</h5>{ports.length > 0 ? <dl>{ports.map(port => <div key={port}><dt><code>{port}</code></dt><dd>{published.filter(output => output.nodeOutput.port === port).map(output => outputName(output.name)).join('、') || '供下游步骤使用'}</dd></div>)}</dl> : <p className="muted">当前草稿没有引用此节点的输出端口</p>}</section>
    </div>
    <section><h5>参数与来源</h5>{Object.keys(node.parameters).length > 0 ? <dl className="argument-list readable-arguments">{Object.entries(node.parameters).map(([port, binding]) => {
      const name = binding.workflowParameter;
      const source = payload.parameterSources.find(item => item.name === name);
      const missing = payload.missingInputs.some(item => item.kind === 'PARAMETER' && item.name === name && item.requiredBy.some(required => required.nodeId === node.nodeId && required.port === port));
      const hasValue = Object.prototype.hasOwnProperty.call(payload.parameterValues, name);
      return <div key={port}><dt><code>{port}</code><small> · {argumentLabel(name)}</small></dt><dd>{missing ? <span className="draft-inline-missing">缺少参数</span> : hasValue ? <ArgumentValue value={payload.parameterValues[name]} name={name} /> : '未设置'}{source && <p className="muted">{sourceNames[source.source]} · {source.explanation}</p>}</dd></div>;
    })}</dl> : <p className="muted">无额外参数</p>}</section>
    <section><h5>适用限制</h5>{payload.limitations.length > 0 ? <ul>{payload.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p className="muted">当前草稿未声明额外限制</p>}</section>
  </section>;
}

export function WorkflowDraftGraph({ draftId, payload }: {
  draftId: string;
  payload: WorkflowDraftPayload;
}) {
  const layout = useMemo(() => buildLayout(payload), [payload]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const selected = layout.nodes.find(item => item.node.nodeId === selectedNodeId);
  const markerId = `draft-arrow-${safeId(draftId)}`;
  const detailId = `draft-node-detail-${safeId(draftId)}`;
  const inputByName = new Map(layout.inputs.map(item => [item.input.name, item]));
  const nodeById = new Map(layout.nodes.map(item => [item.node.nodeId, item]));
  const terminalNodes = unique(payload.workflow.outputs.map(output => output.nodeOutput.nodeId)).length;
  const chooseNode = (nodeId: string) => setSelectedNodeId(current => current === nodeId ? null : nodeId);

  return <div className="draft-graph-shell">
    <div className="draft-graph-scroll" tabIndex={0} aria-label="可横向滚动的工作流依赖图">
      <svg className="draft-graph" role="group" aria-label="工作流依赖图" width={layout.width} height={layout.height} viewBox={`0 0 ${layout.width} ${layout.height}`}>
        <defs><marker id={markerId} markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 z" /></marker></defs>
        <text className="draft-graph-column-label" x={LEFT} y="24">所需资料</text>
        <text className="draft-graph-column-label" x={LEFT + COLUMN_STEP} y="24">分析步骤</text>
        <text className="draft-graph-terminal-summary" x={layout.width - 24} y="24" textAnchor="end">{terminalNodes} 个最终节点</text>

        {payload.workflow.nodes.flatMap(node => Object.entries(node.inputs).map(([port, binding]) => {
          const target = nodeById.get(node.nodeId);
          if (!target) return null;
          if ('workflowInput' in binding) {
            const source = inputByName.get(binding.workflowInput);
            if (!source) return null;
            return <path key={`${node.nodeId}:${port}`} className="draft-graph-edge" data-dag-edge-kind="workflow-input" data-edge-source={`workflow:${binding.workflowInput}`} data-edge-target={`${node.nodeId}:${port}`} d={edgePath(inputAnchor(source), nodeInputAnchor(target, port))} markerEnd={`url(#${markerId})`} />;
          }
          const source = nodeById.get(binding.nodeOutput.nodeId);
          if (!source) return null;
          return <path key={`${node.nodeId}:${port}`} className="draft-graph-edge" data-dag-edge-kind="node-output" data-edge-source={`${binding.nodeOutput.nodeId}:${binding.nodeOutput.port}`} data-edge-target={`${node.nodeId}:${port}`} d={edgePath(nodeOutputAnchor(source, binding.nodeOutput.port), nodeInputAnchor(target, port))} markerEnd={`url(#${markerId})`} />;
        }))}

        {layout.missing.flatMap(item => item.input.requiredBy.map(required => {
          const source = nodeById.get(required.nodeId);
          if (!source) return null;
          return <path key={`${item.input.kind}:${item.input.name}:${required.nodeId}:${required.port}`} className="draft-graph-missing-edge" data-dag-edge-kind="missing" d={edgePath({ x: source.x + NODE_WIDTH, y: source.y + NODE_HEIGHT / 2 }, { x: item.x, y: item.y + 16 })} />;
        }))}

        {layout.inputs.map(visual => <g key={visual.input.name} className={`draft-graph-input${visual.missing ? ' is-missing' : ''}`} data-workflow-input={visual.input.name} transform={`translate(${visual.x} ${visual.y})`}>
          <rect width={INPUT_WIDTH} height={INPUT_HEIGHT} rx="8" />
          <title>{visual.input.name} · {visual.input.formats.join(' / ')}</title>
          <text className="draft-graph-input-title" x="12" y="24">{compact(argumentLabel(visual.input.name), 20)}</text>
          <text className="draft-graph-input-meta" x="12" y="45">{compact(visual.input.formats.join(' / '), 21)}</text>
          {visual.missing && <text className="draft-graph-input-missing" x={INPUT_WIDTH - 10} y="45" textAnchor="end">待补数据</text>}
        </g>)}

        {layout.nodes.map(visual => {
          const selectedNode = selectedNodeId === visual.node.nodeId;
          return <g key={visual.node.nodeId} className={`draft-graph-node${selectedNode ? ' is-selected' : ''}`} role="button" tabIndex={0} aria-label={`查看${visual.operator.title}节点详情`} aria-expanded={selectedNode} aria-controls={detailId} data-node-id={visual.node.nodeId} data-level={visual.level} data-terminal-output-count={visual.terminalOutputCount} transform={`translate(${visual.x} ${visual.y})`} onClick={() => chooseNode(visual.node.nodeId)} onKeyDown={event => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              chooseNode(visual.node.nodeId);
            }
          }}>
            <rect width={NODE_WIDTH} height={NODE_HEIGHT} rx="9" />
            <title>{visual.operator.title} · {visual.node.nodeId}</title>
            <text className="draft-graph-node-title" x="14" y="25">{compact(visual.operator.title, 20)}</text>
            <text className="draft-graph-node-id" x="14" y="47">{compact(visual.node.nodeId, 24)}</text>
            <text className="draft-graph-node-count" x="14" y="68">{Object.keys(visual.node.inputs).length} 入 · {visual.outputPorts.length} 出</text>
            {visual.terminalOutputCount > 0 && <text className="draft-graph-terminal-badge" x={NODE_WIDTH - 12} y="68" textAnchor="end">最终 ×{visual.terminalOutputCount}</text>}
          </g>;
        })}

        {layout.missing.map(visual => {
          const label = `${visual.input.kind === 'DATA' ? '缺少数据' : '缺少参数'}：${argumentLabel(visual.input.name)}`;
          return <g key={`${visual.input.kind}:${visual.input.name}`} className="draft-graph-missing" data-missing-input={`${visual.input.kind}:${visual.input.name}`} transform={`translate(${visual.x} ${visual.y})`}>
            <rect width="202" height="32" rx="16" /><text x="12" y="21">{compact(label, 27)}</text>
          </g>;
        })}
      </svg>
    </div>
    {selected && <NodeDetail payload={payload} visual={selected} detailId={detailId} />}
  </div>;
}
