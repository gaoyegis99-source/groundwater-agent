import { useEffect, useState } from 'react';
import { ApiError, readReadiness, type ReadinessSnapshot } from './api';
import { ErrorNotice } from './ErrorNotice';
import { Icon } from './Icon';
import { modelName, outputName, parameterPresentation } from './card-presentation';
import { productUi } from './product-ui';
import type { Capabilities } from './types';

export function CapabilityDrawer({ capabilities, onExample, onClose }: { capabilities: Capabilities; onExample: (prompt: string) => void; onClose: () => void }) {
  const ui = productUi();
  return <aside className="product-drawer" role="dialog" aria-modal="true" aria-label="能力目录"><header><div><span className="drawer-eyebrow">当前产品实际装配</span><h2>能力目录</h2></div><button className="icon-button" aria-label="关闭能力目录" onClick={onClose}><Icon name="close" /></button></header><div className="product-drawer-scroll">
    {capabilities.models.map(model => { const presentation = ui.models[model.modelId]; const outputs = (model.finalOutputs ?? []).map(item => typeof item === 'string' ? item : item.name).filter((item): item is string => !!item); return <article className="capability-item" key={model.modelId}><div><span className={`type-badge is-${model.applicabilityStatus === 'AVAILABLE' ? 'available' : 'limited'}`}>{model.applicabilityStatus ?? '已装配'}</span><h3>{modelName(model.modelId)}</h3></div><p>{presentation.description}</p><p className="muted">适用条件：{presentation.applicability}</p><details><summary>所需资料与参数</summary><dl className="argument-list">{Object.entries(presentation.inputs).map(([name, input]) => <div key={name}><dt>{input.label}</dt><dd>{input.description}<small>{input.formats.join(' / ')}</small></dd></div>)}{Object.keys(model.inputSchema?.properties ?? {}).filter(name => !presentation.inputs[name]).map(name => <div key={name}><dt>{parameterPresentation(name, model.modelId).label}</dt><dd>{model.inputSchema?.required?.includes(name) ? '必填' : '可选'}</dd></div>)}</dl></details><div className="tag-list">{outputs.map(name => <i key={name}>{outputName(name)}</i>)}</div><small>最长执行时间 {model.deadlineSeconds ?? '—'} 秒</small></article>; })}
    {Object.keys(ui.cases).length > 0 && <section><h3>案例与执行档位</h3><dl className="argument-list">{Object.entries(ui.cases).map(([id, item]) => <div key={id}><dt>{item.label}</dt><dd>{Object.values(item.profiles).join(' · ')}</dd></div>)}</dl></section>}
    <section><h3>推荐问题</h3><div className="capability-examples">{ui.examples.map(example => <button key={example.id} onClick={() => onExample(example.prompt)}>{example.title}<Icon name="arrow-up-right" /></button>)}</div></section>
  </div></aside>;
}

const labels: Record<string, string> = { kernel: '核心编排', data: '数据服务', 'knowledge-next': '知识服务', executor: '执行服务', 'agent-provider': '对话模型', 'intent-router-provider': '意图路由模型', embedding: '向量检索' };
export function ReadinessDrawer({ services, onClose }: { services?: string[]; onClose: () => void }) {
  const [snapshot, setSnapshot] = useState<ReadinessSnapshot | null>(null); const [error, setError] = useState<ApiError | null>(null); const [loading, setLoading] = useState(false); const [checkedAt, setCheckedAt] = useState<Date | null>(null);
  async function refresh() { setLoading(true); try { setSnapshot(await readReadiness()); setError(null); setCheckedAt(new Date()); } catch (caught) { if (caught instanceof ApiError) setError(caught); else throw caught; } finally { setLoading(false); } }
  useEffect(() => { void refresh(); }, []);
  const configured = services ?? [];
  const names = [...new Set(['kernel', ...configured, 'agent-provider', 'intent-router-provider', ...(configured.includes('knowledge-next') ? ['embedding'] : []), ...Object.keys(snapshot?.components ?? {})])];
  return <aside className="product-drawer" role="dialog" aria-modal="true" aria-label="服务状态"><header><div><span className="drawer-eyebrow">只读可用性快照</span><h2>服务状态</h2></div><button className="icon-button" aria-label="关闭服务状态" onClick={onClose}><Icon name="close" /></button></header><div className="product-drawer-scroll">{error && <ErrorNotice error={error} title="状态检查未完成" />}<p>总体状态：<strong>{snapshot?.status ?? 'UNKNOWN'}</strong></p><ul className="readiness-list">{names.map(name => { const component = snapshot?.components[name]; return <li key={name}><span className={`status-dot ${component?.status === 'UP' ? 'online' : ''}`} /><strong>{labels[name] ?? name}</strong><span>{component?.status ?? 'UNKNOWN'}</span>{component?.error && <code>{component.error}</code>}</li>; })}</ul>{checkedAt && <p className="muted">检查时间：{checkedAt.toLocaleTimeString()}</p>}<button className="primary" disabled={loading} onClick={() => void refresh()}>{loading ? '正在检查…' : '重新检查'}</button></div></aside>;
}
