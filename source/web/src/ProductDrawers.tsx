import { useEffect, useRef, useState } from 'react';
import { ApiError, readReadiness, type ReadinessSnapshot } from './api';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { modelName, outputName, parameterPresentation } from './card-presentation';
import { productUi, type BusinessCapability, type BusinessCapabilityProfile, type ProductUiProfile } from './product-ui';
import type { Capabilities, OperatorCapability } from './types';
import { modelIdentity, type WorkflowModel } from './workflow-selection';
import { Button } from './ui/Button';
import { Badge, EmptyState, Loading, type BadgeTone } from './ui/Feedback';
import { Input } from './ui/Fields';
import { Drawer } from './ui/Overlays';
import './product-directories.css';

const operatorCoverage = {
  BROWSER_SINGLE_NODE: '网页单节点', WORKER_SINGLE_OPERATOR: 'Worker 单算子', REMOTE_PRESET_NODE: '预设远程节点',
};
const runtimeNames = { native: 'Native', qgis: 'QGIS', remote_http: '远程 HTTP' };

function OperatorDirectory({ operators, heading: Heading }: { operators: readonly OperatorCapability[]; heading: 'h2' | 'h3' }) {
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(20);
  const [refresh, setRefresh] = useState(0);
  const [executor, setExecutor] = useState<ReadinessSnapshot['components'][string]>();
  const [error, setError] = useState<ApiError>();
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    void readReadiness(controller.signal).then(
      result => { if (!controller.signal.aborted) setExecutor(result.components.executor); },
      caught => { if (!controller.signal.aborted && caught instanceof ApiError) setError(caught); },
    ).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);
  const term = query.trim().toLocaleLowerCase();
  const matches = operators.filter(item => [item.operatorId, item.title, item.version, item.domain, ...item.tags]
    .some(value => value.toLocaleLowerCase().includes(term)));
  return <>
    <div className="capability-search">
      <Input label="搜索算子" type="search" value={query} onChange={event => { setQuery(event.target.value); setLimit(20); }} />
      <Button variant="text" isPending={loading} onPress={() => {
        setLoading(true); setError(undefined); setRefresh(value => value + 1);
      }}>刷新 Worker 状态</Button>
    </div>
    {error && <ErrorNotice error={error} title="Worker 状态暂时无法读取" />}
    <details className="capability-search"><summary>指定输入端口</summary>
      <p>附加所需数据后，可以在对话消息中另起一行锁定角色。</p>
      <code>输入角色 target = 《资料标题》</code>
    </details>
    {matches.length === 0 && <EmptyState description="没有匹配的算子。" />}
    {matches.slice(0, limit).map(item => {
      const key = item.runtime === 'remote_http' ? 'remoteHttp' : item.runtime;
      const worker = error || !executor ? 'UNKNOWN' : executor.status === 'UP'
        ? executor.components?.[key] ?? 'UNKNOWN' : 'DOWN';
      const workerLabel = loading ? '正在核验' : worker === 'UP' ? '在线' : worker === 'UNKNOWN' ? '未知' : '不可用';
      const validation = item.validation;
      const status = validation.status === 'PASSED' ? '通过' : validation.status === 'STALE' ? '需复验' : '暂无记录';
      return <article className="capability-item" key={`${item.operatorId}@${item.version}`}>
        <Heading>{item.title}</Heading>
        <p translate="no" className="capability-identity">{item.operatorId}@{item.version}</p>
        <dl className="argument-list">
          <div><dt>独立调用</dt><dd>{item.confirmationSupported ? '已准入' : '未准入'}</dd></div>
          <div><dt>{runtimeNames[item.runtime]} Worker</dt><dd>{workerLabel}</dd></div>
          <div><dt>开发验收</dt><dd>{status}{validation.coverage ? ` · ${operatorCoverage[validation.coverage]}` : ''}</dd></div>
          <div><dt>科学适用性</dt><dd>{item.scientificStatus === 'REVIEW_EVIDENCE_ATTACHED' ? '已有案例与复核证据' : '需按案例与领域复核'}</dd></div>
        </dl>
        {item.workflowUses.length > 0 ? <details><summary>引用此版本的工作流 · {item.workflowUses.length}</summary>
          <ul>{item.workflowUses.map(use => <li key={`${use.workflowId}@${use.version}`}>
            <span translate="no">{use.workflowId}@{use.version}</span>
            {use.models.map(model => <div key={`${model.modelId}@${model.version}`}>{modelName(model.modelId)} · {model.version}</div>)}
          </li>)}</ul></details> : <p className="muted">当前预设引用 0 项</p>}
        {validation.checkedOn && <details><summary>查看验收证据</summary><dl className="argument-list">
          <div><dt>验收日期</dt><dd>{validation.checkedOn}</dd></div>
          <div><dt>报告校验和</dt><dd className="capability-identity" translate="no">{validation.reportChecksum}</dd></div>
          <div><dt>已测实现摘要</dt><dd className="capability-identity" translate="no">{validation.implementationRevision}</dd></div>
        </dl></details>}
        {item.scientificReview && <details><summary>查看科学证据</summary><dl className="argument-list">
          <div><dt>基准案例</dt><dd className="capability-identity" translate="no">{item.scientificReview.benchmark.path} · {item.scientificReview.benchmark.checksum}</dd></div>
          <div><dt>领域复核</dt><dd className="capability-identity" translate="no">{item.scientificReview.domainReview.path} · {item.scientificReview.domainReview.checksum}</dd></div>
        </dl></details>}
      </article>;
    })}
    {limit < matches.length && <Button variant="text" onPress={() => setLimit(value => value + 20)}>显示更多算子（共 {matches.length} 项）</Button>}
  </>;
}

export function CapabilityDrawer({ capabilities, businessCapabilities, examples, onBusinessCapability, onExample, onWorkflow, onClose, embedded = false }: {
  capabilities: Capabilities;
  businessCapabilities: BusinessCapability[];
  examples: ProductUiProfile['examples'];
  onBusinessCapability: (id: string, version: string) => void;
  onExample: (prompt: string) => void;
  onWorkflow?: (model: WorkflowModel) => void;
  onClose: () => void;
  embedded?: boolean;
}) {
  const ui = productUi();
  const [query, setQuery] = useState('');
  const [showOperators, setShowOperators] = useState(false);
  const search = useRef<HTMLInputElement>(null);
  const term = query.trim().toLocaleLowerCase();
  const models = capabilities.models.filter(({ modelId }) => {
    const { displayName, description } = ui.models[modelId];
    return `${displayName} ${description}`.toLocaleLowerCase().includes(term);
  });
  const scenarios = businessCapabilities.filter(item => `${item.title} ${item.description} ${item.scenario}`.toLocaleLowerCase().includes(term));
  const Heading = embedded ? 'h2' : 'h3';
  const content = <>
    {!!capabilities.operators?.length && <div className="capability-search" role="group" aria-label="能力目录类型">
      <button type="button" aria-pressed={!showOperators} onClick={() => setShowOperators(false)}>工作流</button>
      <button type="button" aria-pressed={showOperators} onClick={() => setShowOperators(true)}>算子</button>
    </div>}
    {showOperators ? <OperatorDirectory operators={capabilities.operators ?? []} heading={Heading} /> : <>
    <div className="capability-search">
      <Input ref={search} label="搜索工作流" type="search" value={query} onChange={event => setQuery(event.target.value)} />
    </div>
    {models.length === 0 && scenarios.length === 0 && <EmptyState description="没有匹配的工作流。" action={term &&
      <Button variant="text" onPress={() => { setQuery(''); search.current?.focus(); }}>清除关键词</Button>} />}
    {scenarios.length > 0 && <section className="capability-item"><Heading>业务场景</Heading><div className="capability-examples">
      {scenarios.map(({ ref, title }) => <button type="button" key={`${ref.id}@${ref.version}`} aria-haspopup="dialog" onClick={() => onBusinessCapability(ref.id, ref.version)}>{title}<Icon name="arrow-right" /></button>)}
    </div></section>}
    {models.map(model => {
      const presentation = ui.models[model.modelId];
      const name = modelName(model.modelId);
      const outputs = (model.finalOutputs ?? []).flatMap(item => typeof item === 'string' ? [item] : item.name ? [item.name] : []);
      return <article className="capability-item" key={model.modelId}>
        <div className="capability-heading">
          <Heading>{name}</Heading>
          {onWorkflow && <Button aria-label={`在会话中使用${name}`} isDisabled={!modelIdentity(model)} onPress={() => onWorkflow(model)}>在会话中使用</Button>}
        </div>
        <p>{presentation.description}</p>
        <p className="muted">适用条件：{presentation.applicability}</p>
        <details><summary>所需资料与参数</summary><dl className="argument-list">
          {Object.entries(presentation.inputs).map(([key, input]) => <div key={key}><dt>{input.label}</dt><dd>{input.description}<small>{input.formats.join(' / ')}</small></dd></div>)}
          {Object.keys(model.inputSchema?.properties ?? {}).filter(key => !presentation.inputs[key]).map(key => <div key={key}>
            <dt>{parameterPresentation(key, model.modelId).label}</dt><dd>{model.inputSchema?.required?.includes(key) ? '必填' : '可选'}</dd>
          </div>)}
        </dl></details>
        <div className="tag-list">{outputs.map(key => <i key={key}>{outputName(key)}</i>)}</div>
        {model.deadlineSeconds != null && <small>最长执行时间 {model.deadlineSeconds} 秒</small>}
      </article>;
    })}
    {!term && <>
      {Object.keys(ui.cases).length > 0 && <section><Heading>案例与执行档位</Heading><dl className="argument-list">{Object.entries(ui.cases).map(([id, item]) => <div key={id}><dt>{item.label}</dt><dd>{Object.values(item.profiles).join(' · ')}</dd></div>)}</dl></section>}
      {examples.length > 0 && <section><Heading>推荐问题</Heading><div className="capability-examples">{examples.map(example => <button type="button" key={example.id} onClick={() => onExample(example.prompt)}>{example.title}<Icon name="arrow-up-right" /></button>)}</div></section>}
    </>}
    </>}
  </>;
  if (embedded) return <section className="capability-directory">{content}</section>;
  return <Drawer title="能力目录" isOpen dismissOnOutsidePress onOpenChange={open => { if (!open) onClose(); }}>
    {content}
  </Drawer>;
}

const labels: Record<string, string> = { kernel: '核心编排', data: '数据服务', 'knowledge-next': '知识服务', executor: '执行服务', 'agent-provider': '对话模型', 'intent-router-provider': '意图路由模型', embedding: '向量检索' };
const capabilityTiers: Record<BusinessCapability['tier'], string> = {
  CORE: '核心', SPECIALIZED: '专项', ADVANCED: '高级', EXPERIMENTAL: '实验',
};

function CapabilityOutputPreview({ outputs }: { outputs: BusinessCapabilityProfile['outputs'] }) {
  return <dl className="argument-list">
    {outputs.map(output => <div key={output.name}>
      <dt>{outputName(output.name)}</dt><dd>{output.meaning}<small>{output.format}</small></dd>
    </div>)}
  </dl>;
}

function CapabilityProfilePreview({ profile }: { profile: BusinessCapabilityProfile }) {
  const primaryOutputs = profile.outputs.filter(output => output.role === 'PRIMARY_RESULT' || output.role === 'VISUALIZATION');
  const additionalOutputs = profile.outputs.filter(output => output.role === 'SUMMARY' || output.role === 'DIAGNOSTIC');
  return <section className="capability-item">
    <h3>{profile.label}</h3>
    <h4>所需资料</h4>
    <dl className="argument-list">
      {profile.inputs.map((input, index) => <div key={`${input.label}-${index}`}>
        <dt>{input.label}</dt><dd>{input.meaning}<small>{input.formats.join(' / ')}</small>
          {input.preconditions.length > 0 && <ul>{input.preconditions.map(condition => <li key={condition}>{condition}</li>)}</ul>}
        </dd>
      </div>)}
    </dl>
    {profile.limitations.length > 0 && <><h4>使用限制</h4><ul>{profile.limitations.map(limitation => <li key={limitation}>{limitation}</li>)}</ul></>}
    <h4>成果预览</h4>
    <CapabilityOutputPreview outputs={primaryOutputs.length ? primaryOutputs : additionalOutputs} />
    {primaryOutputs.length > 0 && additionalOutputs.length > 0 && <details>
      <summary>查看摘要与诊断</summary>
      <CapabilityOutputPreview outputs={additionalOutputs} />
    </details>}
    <details><summary>查看方案版本</summary><dl className="argument-list">
      <div><dt>方案版本</dt><dd>{profile.profileRef.version}</dd></div>
      <div><dt>模型版本</dt><dd>{profile.modelRef.version}</dd></div>
      <div><dt>工作流版本</dt><dd>{profile.workflowRef.version}</dd></div>
    </dl></details>
  </section>;
}

export function BusinessCapabilityDrawer({ capability, onUse, onClose }: {
  capability: BusinessCapability;
  onUse?: () => void;
  onClose: () => void;
}) {
  return <Drawer title={capability.title} description={capability.description} isOpen dismissOnOutsidePress
    onOpenChange={open => { if (!open) onClose(); }}>
    <p>{capability.scenario}</p>
    {onUse && <Button variant="primary" onPress={onUse}>填入对话</Button>}
    <dl className="argument-list">
      <div><dt>验证状态</dt><dd>{capabilityTiers[capability.tier]} · {capability.validationStatus === 'TECHNICALLY_VALIDATED' ? '已通过技术验证' : '待验证'}</dd></div>
      {capability.requiredServices.length > 0 && <div><dt>依赖服务</dt><dd>{capability.requiredServices.map(name => labels[name] ?? name).join(' · ')}</dd></div>}
    </dl>
    {capability.profiles.map(profile => <CapabilityProfilePreview key={`${profile.profileRef.id}@${profile.profileRef.version}`} profile={profile} />)}
  </Drawer>;
}

function readinessTone(status?: string): BadgeTone {
  switch (status) {
    case 'UP':
    case 'READY': return 'success';
    case 'DEGRADED': return 'warning';
    case 'DOWN': return 'error';
    default: return 'neutral';
  }
}

export function ReadinessDrawer({ services, onClose }: { services?: string[]; onClose: () => void }) {
  const [snapshot, setSnapshot] = useState<ReadinessSnapshot | null>(null); const [error, setError] = useState<ApiError | null>(null); const [loading, setLoading] = useState(false); const [checkedAt, setCheckedAt] = useState<Date | null>(null);
  async function refresh() { setLoading(true); try { setSnapshot(await readReadiness()); setError(null); setCheckedAt(new Date()); } catch (caught) { if (caught instanceof ApiError) setError(caught); else throw caught; } finally { setLoading(false); } }
  useEffect(() => { void refresh(); }, []);
  const configured = services ?? [];
  const names = [...new Set(['kernel', ...configured, 'agent-provider', 'intent-router-provider', ...(configured.includes('knowledge-next') ? ['embedding'] : []), ...Object.keys(snapshot?.components ?? {})])];
  return <Drawer title="服务状态" description="只读可用性快照" isOpen
    onOpenChange={open => { if (!open) onClose(); }}>
    {error && <ErrorNotice error={error} title="状态检查未完成" />}
    <p>总体状态 <Badge tone={readinessTone(snapshot?.status)}>{snapshot?.status ?? 'UNKNOWN'}</Badge></p>
    <ul className="readiness-list">{names.map(name => {
      const component = snapshot?.components[name];
      return <li key={name}>
        <strong>{labels[name] ?? name}</strong>
        <Badge tone={readinessTone(component?.status)}>{component?.status ?? 'UNKNOWN'}</Badge>
        {component?.error && <code>{component.error}</code>}
      </li>;
    })}</ul>
    {checkedAt && <p className="gw-note">检查时间 {checkedAt.toLocaleTimeString()}</p>}
    {loading && <Loading>正在检查服务状态…</Loading>}
    <Button variant="primary" isPending={loading} onPress={() => void refresh()}>重新检查</Button>
  </Drawer>;
}
