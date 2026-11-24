import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from './api';
import { readWorkflowInputCandidates } from './workflow-input-candidates';
import { ResourceLink } from './ResourceLink';
import { preparationCheckFacts, preparationIssues } from './preparation-display';
import { argumentLabel, modelName, parameterPresentation } from './card-presentation';
import { caseRequestFields } from './case-request';
import { productUi } from './product-ui';
import type { KnowledgeReference, ResourceItem, ResourceReference, TaskPreparationEntry } from './types';
import { workflowFileKey, type WorkflowModel, type WorkflowSelection } from './workflow-selection';

function group(formats: string[]) {
  if (formats.every(format => format === 'xlsx')) return '工作簿';
  if (formats.every(format => format === 'json')) return 'JSON 资料';
  if (formats.some(format => ['geotiff', 'geopackage', 'geojson', 'cog'].includes(format))) return '空间与影像资料';
  return '其他资料';
}

export function WorkflowPreparation({ selection, model, attachments, prepared, project, expanded = false, onResource, onKnowledge, onChange, onPick, onApplyCandidates }: {
  selection: WorkflowSelection; model?: WorkflowModel; attachments: ResourceItem[]; prepared?: TaskPreparationEntry;
  project: string; expanded?: boolean; onResource: (id: string, reference?: ResourceReference) => void;
  onKnowledge?: (reference: KnowledgeReference) => void;
  onApplyCandidates?: (candidates: Record<string, ResourceItem>, parameters?: WorkflowSelection['parameters']) => void;
  onChange: (value: WorkflowSelection | null) => void; onPick: (name: string, formats: string[]) => void;
}) {
  const profile = productUi().models[selection.target.modelId];
  const inputs = Object.entries(profile?.inputs ?? {});
  const names = new Set(attachments.map(workflowFileKey));
  const lockedInput = selection.continuation && Object.values(productUi().models).find(item =>
    item.continuation?.output === selection.continuation?.outputName && item.continuation?.targetModel === selection.target.modelId,
  )?.continuation?.targetInput;
  const [candidates, setCandidates] = useState<Record<string, ResourceItem>>();
  const [matching, setMatching] = useState(false);
  const [matchFailed, setMatchFailed] = useState(false);
  const matchRequest = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => matchRequest.current?.abort(), []);
  const matchCase = useCallback(async () => {
    if (!profile?.inputCaseTag) return;
    const controller = new AbortController();
    matchRequest.current?.abort();
    matchRequest.current = controller;
    setMatching(true);
    setMatchFailed(false);
    setCandidates(undefined);
    try {
      const found = await readWorkflowInputCandidates(profile.inputCaseTag, profile.inputs, controller.signal);
      if (!controller.signal.aborted) setCandidates(found);
    } catch (error) {
      if (controller.signal.aborted) return;
      if (!(error instanceof ApiError)) throw error;
      setMatchFailed(true);
    } finally {
      if (!controller.signal.aborted) setMatching(false);
    }
  }, [profile]);
  const boundCount = inputs.filter(([name]) => names.has(selection.bindings[name])).length;
  const autoMatch = selection.casePreparation && !!model && !!onApplyCandidates && boundCount === 0;
  useEffect(() => {
    if (autoMatch) void matchCase();
  }, [autoMatch, matchCase]);
  const suggestions = Object.entries(candidates ?? {}).filter(([name]) => name !== lockedInput && !names.has(selection.bindings[name]));
  const completeCase = inputs.length > 0 && inputs.every(([name]) => {
    const candidate = candidates?.[name];
    if (!candidate) return false;
    const bound = selection.bindings[name];
    return names.has(bound) ? workflowFileKey(candidate) === bound : name !== lockedInput;
  });
  const caseParameters = completeCase && profile?.inputCaseParameters &&
    Object.keys(profile.inputCaseParameters).some(name => !Object.hasOwn(selection.parameters, name))
    ? profile.inputCaseParameters : undefined;
  const [compare, setCompare] = useState<string>();
  const issues = prepared ? preparationIssues(prepared) : [];
  const problems = (name: string) => prepared?.checks.filter(check =>
    (check.input === name || check.parameter === name) && check.status !== 'PASS') ?? [];
  const needsAttention = (name: string) => problems(name).length > 0
    || issues.some(issue => issue.input === name || issue.parameter === name);
  const issueText = (name: string) => {
    const issue = issues.find(item => item.input === name || item.parameter === name);
    if (issue?.kind === 'MISSING_DATA') return '需要选择资料';
    if (issue?.kind === 'MISSING_PARAMETER') return '需要填写参数';
    if (issue?.kind === 'INVALID_PARAMETER') return '请核对参数取值';
    if (issue?.kind === 'DATA_VERSION_NOT_CURRENT') return '请核对数据版本';
    return needsAttention(name) ? '需要复核' : null;
  };
  const orderedInputs = [...inputs].sort(([left], [right]) =>
    Number(needsAttention(right) || !names.has(selection.bindings[right]))
    - Number(needsAttention(left) || !names.has(selection.bindings[left])));
  const textField = selection.parameters.valueField ?? selection.parameters.column
    ?? prepared?.parameters.valueField ?? prepared?.parameters.column ?? model?.defaultParameters?.valueField;
  const [open, setOpen] = useState<boolean>();


  const dataNames = new Set(inputs.map(([name]) => name));
  const required = model?.inputSchema?.required?.filter(name => !dataNames.has(name)) ?? [];
  const business = caseRequestFields(selection, profile?.caseRequest);
  const parameters = [...new Set([
    ...required, ...(profile?.confirmationSummary.flatMap(summary => summary.parameters) ?? []),
    ...business.fields,
    ...Object.keys(profile?.inputCaseParameters ?? {}).filter(name => Object.hasOwn(selection.parameters, name)),
  ])].filter(name => !dataNames.has(name));
  const defaults = model?.defaultParameters ?? {};
  const missing = [...required.filter(name => !Object.hasOwn(selection.parameters, name) && !Object.hasOwn(defaults, name) && !Object.hasOwn(prepared?.parameters ?? {}, name)),
    ...business.missing];
  return <section id="workflow-preparation" tabIndex={-1} className="workflow-selection" aria-label="选中的工作流">
    <div className="workflow-selection-heading"><strong>{modelName(selection.target.modelId)}</strong>
      <button type="button" className="text-button" onClick={() => onChange(null)}>移除工作流</button></div>
    {!model ? <p role="status">此工作流的装配版本已变化，请重新选择。</p> :
      <details open={open ?? (expanded || selection.casePreparation || prepared?.status === 'BLOCKED')} onToggle={event => setOpen(event.currentTarget.open)}><summary>资料已绑定 {boundCount}/{inputs.length}{missing.length ? ` · 缺少 ${missing.length} 个必填参数` : ''}{prepared ? prepared.status === 'READY' ? ' · 最近一次准备检查已通过' : ' · 需要补充或核验' : ''}</summary>
        {profile.inputCaseTag && onApplyCandidates && <div className="workflow-suggestions">
          <button type="button" disabled={matching} onClick={() => void matchCase()}>{matching ? '正在核对案例资料…' : '匹配案例资料'}</button>
          {matchFailed && <p role="status">暂时无法读取完整案例目录，可重试或逐项选资料。</p>}
          {candidates && (suggestions.length || caseParameters ? <div>
            {suggestions.length > 0 && <ul>{suggestions.map(([name, item]) => <li key={name}>{profile.inputs[name].label} · {item.name}
              <button type="button" className="text-button" onClick={() => setCandidates(current =>
                Object.fromEntries(Object.entries(current ?? {}).filter(([key]) => key !== name)))}>移除此候选</button>
            </li>)}</ul>}
            {caseParameters && <p>案例参数可修改，已填写的参数会保留。</p>}
            <button type="button" onClick={() => { onApplyCandidates(Object.fromEntries(suggestions), caseParameters); setCandidates(undefined); }}>
              {suggestions.length ? `应用 ${suggestions.length} 份候选资料${caseParameters ? '和案例参数' : ''}` : '填入案例参数'}
            </button>
          </div> : <p role="status">当前未找到可应用的唯一角色候选。</p>)}
        </div>}
        {business.missing.length > 0 && <p role="status">请补充{business.missing.map(name => argumentLabel(name, model.modelId)).join('、')}。
          {business.hints.join(' ')}</p>}
        <div className="workflow-selection-fields">
          {[...new Set(orderedInputs.map(([, input]) => inputs.length > 8 ? group(input.formats) : '输入资料'))].map(label => <fieldset key={label}>
            <legend>{label}</legend>
            {orderedInputs.filter(([, input]) => inputs.length <= 8 || group(input.formats) === label).map(([name, input]) => {
              const value = selection.bindings[name] ?? '';
              const bound = attachments.find(item => workflowFileKey(item) === value);
              const options = attachments.filter(item => !input.formats.length || input.formats.includes(item.format));
              const evidenceField = typeof textField === 'string' && inputs.length === 1 ? textField : undefined;
              const duplicateNames = new Set(options.map(item => item.name)).size < options.length;
              return <div className="workflow-binding" data-needs-input={needsAttention(name) || !bound} key={name}>
                <label>{input.label}<select disabled={name === lockedInput} value={bound ? value : ''} onChange={event => onChange({
                  ...selection, bindings: { ...selection.bindings, [name]: event.target.value },
                })}>
                  <option value="">待补资料</option>
                  {options.map(item => <option key={workflowFileKey(item)} value={workflowFileKey(item)}>{item.name}</option>)}
                </select></label>
                <button type="button" aria-label={`选择${input.label}`} disabled={name === lockedInput} onClick={() => onPick(name, input.formats)}>选资料</button>
                {issueText(name) && <small>{issueText(name)}</small>}
                {!needsAttention(name) && bound && <small>{prepared?.inputs[name] ? '已核验' : '已绑定，提交后核验'}</small>}
                {problems(name).map((check, index) => <p className="workflow-check" key={index}>{preparationCheckFacts(check) ?? (check.fieldName ? `请核对字段 ${check.fieldName} 的定义和量类型` : '请核对资料的语义定义与适用范围')}</p>)}
                {duplicateNames && <button type="button" className="text-button" aria-expanded={compare === name}
                  onClick={() => setCompare(compare === name ? undefined : name)}>核对同名资料</button>}
                {compare === name && <div className="workflow-candidates">{options.map(item => <article key={workflowFileKey(item)}>
                  <ResourceLink id={item.versionId} project={project} reference={item} fieldName={evidenceField} onResource={onResource} onKnowledge={onKnowledge} showCoverage />
                  <button type="button" disabled={name === lockedInput} aria-label={`选择 ${item.name} 作为${input.label}`}
                    onClick={() => onChange({ ...selection, bindings: { ...selection.bindings, [name]: workflowFileKey(item) } })}>选择此版本</button>
                </article>)}</div>}
              </div>;
            })}
          </fieldset>)}
          {parameters.length > 0 && <fieldset><legend>关键参数</legend>
            {parameters.map(name => {
              const schema = model.inputSchema?.properties?.[name];
              const value = business.missing.includes(name) ? undefined : selection.parameters[name] ?? prepared?.parameters[name] ?? defaults[name];
              const label = argumentLabel(name, model.modelId);
              const origin = Object.hasOwn(selection.parameters, name) ? '用户草稿' : prepared?.parameterSources?.[name] ? '本轮准备' : Object.hasOwn(defaults, name) ? '模型默认值' : null;
              const set = (next: string | number | boolean | null | undefined) => {
                const parameters = { ...selection.parameters };
                if (next === undefined) delete parameters[name];
                else parameters[name] = next;
                onChange({ ...selection, parameters });
              };
              const scalarEnums = schema?.enum?.filter(item => typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean') as (string | number | boolean)[] | undefined;
              const options = scalarEnums?.length ? scalarEnums : schema?.type === 'boolean' ? [true, false] : undefined;
              return <label className="workflow-parameter" data-needs-input={missing.includes(name) || needsAttention(name)} key={name}>{label}{(missing.includes(name) || needsAttention(name)) && <small>{issueText(name) ?? '需要填写参数'}</small>}{origin && <small>{origin}</small>}
                {problems(name).map((check, index) => <small key={index}>{preparationCheckFacts(check) ?? '请核对取值'}</small>)}
                {options ? <select value={String(value ?? '')} onChange={event => set(options.find(option => String(option) === event.target.value))}>
                  <option value="">待设置</option>{options.map(option => <option key={String(option)} value={String(option)}>{typeof option === 'boolean' ? option ? '启用' : '关闭' : parameterPresentation(name, model.modelId).values?.[String(option)] ?? option}</option>)}
                </select> : typeof schema?.type === 'string' && ['number', 'integer', 'string'].includes(schema.type) ?
                  <input type={schema.type === 'string' ? 'text' : 'number'} step={schema.type === 'integer' ? 1 : 'any'}
                    value={typeof value === 'number' || typeof value === 'string' ? value : ''}
                    onChange={event => set(event.target.value === '' ? undefined : schema.type === 'string' ? event.target.value : event.target.valueAsNumber)} />
                  : <small>{value === undefined ? '在消息中说明取值' : '使用已声明的默认值'}</small>}
              </label>;
            })}
          </fieldset>}
        </div>
      </details>}
  </section>;
}
