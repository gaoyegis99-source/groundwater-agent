import { Icon } from './Icon';
import { productUi, type ParameterPresentation } from './product-ui';

function humanize(value: string) {
  const leaf = value.split('.').at(-1) ?? value;
  return leaf.replace(/[-_]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').trim() || '未命名项目';
}

export function modelName(name: string) {
  const profile = productUi();
  return profile.models[name]?.displayName ?? '分析任务';
}

export function parameterPresentation(name: string, modelId?: string): ParameterPresentation {
  const profile = productUi();
  const override = modelId ? profile.models[modelId]?.parameterOverrides[name] : undefined;
  if (override) return override;
  const direct = profile.parameterNames[name];
  if (direct) return direct;
  const split = name.indexOf('.');
  if (split > 0) {
    const stage = profile.parameterStages[name.slice(0, split)];
    const base = profile.parameterNames[name.slice(split + 1)];
    if (stage && base) return { ...base, label: `${stage.label} · ${base.label}`, group: stage.group };
  }
  return { label: name, group: '基本参数' };
}
export const argumentLabel = (name: string, modelId?: string) => {
  const presentation = parameterPresentation(name, modelId);
  return presentation.unit ? `${presentation.label}（${presentation.unit}）` : presentation.label;
};
export const argumentGroup = (name: string, modelId?: string) => parameterPresentation(name, modelId).group;
export const outputName = (name: string) => productUi().outputs[name]?.label ?? humanize(name);
export const resourceTitle = (title: string) => productUi().outputs[title]?.label ?? title;

export function ArgumentValue({ value, name, modelId }: { value: unknown; name?: string; modelId?: string }) {
  if (value === null) return <>未设置</>;
  if (Array.isArray(value)) return value.length ? <ol>{value.map((item, index) => <li key={index}><ArgumentValue value={item} /></li>)}</ol> : <>空列表</>;
  if (typeof value === 'object') return <dl className="argument-list readable-arguments">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{key}</dt><dd><ArgumentValue value={item} /></dd></div>)}</dl>;
  if (typeof value === 'boolean') return <>{value ? '是' : '否'}</>;
  const presentation = name ? parameterPresentation(name, modelId) : undefined;
  const label = typeof value === 'string' ? presentation?.values?.[value] : undefined;
  return <>{label ? <>{label}（<span className="wire-value">{String(value)}</span>）</> : value === '' ? '空字符串' : String(value)}</>;
}

export function CardArguments({ values, tool, onResource }: { values: Record<string, unknown>; tool: string; onResource: (id: string) => void }) {
  return <dl className="argument-list readable-arguments">{Object.entries(values).map(([name, value]) => <div key={name}>
    <dt>{argumentLabel(name, tool)}</dt><dd>{(name === 'resourceId' || name.endsWith('ResourceId')) && typeof value === 'string'
      ? <button className="text-button" onClick={() => onResource(value)}>查看此资料的版本与内容<Icon name="arrow-up-right" /></button>
      : <ArgumentValue value={value} name={name} modelId={tool} />}</dd>
  </div>)}</dl>;
}
