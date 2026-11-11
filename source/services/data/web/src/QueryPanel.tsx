import { ListBox, ListBoxItem, type Selection } from "react-aria-components";
import { errorText, pretty } from "./api";
import type { RunAction } from "./api";
import type { AggregateDraft, FilterDraft, SortDraft, useQuery } from "./use-query";

type Props = {
  controller: ReturnType<typeof useQuery>;
  run: RunAction;
  allowed: boolean;
  onChooseDataset: () => void;
};

const operatorLabels: Record<string, string> = {
  eq: "等于", ne: "不等于", lt: "小于", le: "小于或等于", gt: "大于", ge: "大于或等于",
  contains: "包含文本", is_null: "为空",
};
const aggregateLabels: Record<string, string> = { count: "计数", min: "最小值", max: "最大值", sum: "求和", avg: "平均值" };
const stateLabels: Record<string, string> = { QUEUED: "等待执行", RUNNING: "正在查询", SUCCEEDED: "查询完成", FAILED: "查询失败", CANCELLED: "已取消" };

function operatorsFor(type: string): Array<FilterDraft["op"]> {
  if (/(int|float|double|decimal|number|date|time)/i.test(type)) return ["eq", "ne", "lt", "le", "gt", "ge", "is_null"];
  if (/bool/i.test(type)) return ["eq", "ne", "is_null"];
  return ["eq", "ne", "contains", "is_null"];
}

export function QueryPanel({ controller, run, allowed, onChooseDataset }: Props) {
  const {
    selected, versions, queryId, result, error, offset, setOffset, pageSize, setPageSize,
    selectedFields, setSelectedFields, filters, setFilters, aggregates, setAggregates,
    groupBy, setGroupBy, sorts, setSorts, metricName, setMetricName, metricVersions,
    setMetricVersions, requestSnapshot, activeHistoryEntry, historyEntries, openHistory, clearHistory,
    submitQuery, compareMetrics, cancel, deadlineStopped, checkNow,
  } = controller;
  const fields = selected?.descriptor.fields ?? [];
  const resultFields = activeHistoryEntry?.versionId === selected?.versionId ? fields : [];
  const fieldName = (fieldId: string) => resultFields.find(item => item.fieldId === fieldId)?.name ?? fieldId;
  const onVersionSelection = (selection: Selection) => {
    setMetricVersions(selection === "all" ? versions.map(item => item.versionId) : [...selection].map(String));
  };
  const updateFilter = (index: number, patch: Partial<FilterDraft>) => setFilters(current => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  const updateAggregate = (index: number, patch: Partial<AggregateDraft>) => setAggregates(current => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  const updateSort = (index: number, patch: Partial<SortDraft>) => setSorts(current => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  return (
    <section className="panel task-panel">
      {!allowed && <p className="muted">当前身份没有创建查询的权限。</p>}
      {!selected && (
        <div className="empty-state actionable"><strong>先选择一份数据</strong><button onClick={onChooseDataset}>前往数据目录</button></div>
      )}
      {selected && fields.length > 0 && (
        <div className="query-builder">
          <div className="selected-resource"><span>正在查询</span><strong>{selected.title}</strong><small>不可变版本 {selected.versionId} · 语义 {selected.semanticId}</small></div>

          <details className="builder-section" open>
            <summary>1. 选择返回字段 <small>{selectedFields.length === 0 ? "当前返回全部字段" : `已选 ${selectedFields.length} 个字段`}</small></summary>
            <div className="builder-actions"><button type="button" className="secondary" onClick={() => setSelectedFields(fields.map(item => item.fieldId))}>全选</button><button type="button" className="secondary" onClick={() => setSelectedFields([])}>返回全部字段</button></div>
            <div className="field-checklist" aria-label="查询字段">
              {fields.map(item => <label key={item.fieldId}><input type="checkbox" checked={selectedFields.includes(item.fieldId)} onChange={event => setSelectedFields(current => event.target.checked ? [...current, item.fieldId] : current.filter(value => value !== item.fieldId))} />{item.name}<small>{item.type}</small></label>)}
            </div>
          </details>

          <details className="builder-section" open>
            <summary>2. 筛选条件 <small>{filters.filter(item => item.op === "is_null" || item.value).length} 条生效</small></summary>
            <div className="builder-rows">
              {filters.map((filter, index) => {
                const descriptor = fields.find(item => item.fieldId === filter.field) ?? fields[0]!;
                const operators = operatorsFor(descriptor.type);
                return <fieldset className="builder-row" key={filter.id}><legend>条件 {index + 1}</legend>
                  <label>字段<select aria-label={index === 0 ? "查询字段" : `查询字段 ${index + 1}`} value={filter.field} onChange={event => { const nextField = event.target.value; const nextType = fields.find(item => item.fieldId === nextField)?.type ?? "string"; const nextOperators = operatorsFor(nextType); updateFilter(index, { field: nextField, op: nextOperators.includes(filter.op) ? filter.op : nextOperators[0], value: "" }); }}>{fields.map(item => <option key={item.fieldId} value={item.fieldId}>{item.name} · {item.type}</option>)}</select></label>
                  <label>运算符<select aria-label={index === 0 ? "筛选条件" : `筛选条件 ${index + 1}`} value={filter.op} onChange={event => updateFilter(index, { op: event.target.value as FilterDraft["op"] })}>{operators.map(operator => <option key={operator} value={operator}>{operatorLabels[operator]}</option>)}</select></label>
                  <label>值{/(bool)/i.test(descriptor.type) && filter.op !== "is_null" ? <select aria-label={index === 0 ? "筛选值" : `筛选值 ${index + 1}`} value={filter.value} onChange={event => updateFilter(index, { value: event.target.value })}><option value="">选择布尔值</option><option value="true">是 / true</option><option value="false">否 / false</option></select> : <input aria-label={index === 0 ? "筛选值" : `筛选值 ${index + 1}`} type={/(int|float|double|decimal|number)/i.test(descriptor.type) ? "number" : /date/i.test(descriptor.type) ? "date" : "text"} disabled={filter.op === "is_null"} placeholder={filter.op === "is_null" ? "此条件无需填写值" : "输入比较值"} value={filter.value} onChange={event => updateFilter(index, { value: event.target.value })} />}</label>
                  <button type="button" className="secondary remove-row" disabled={filters.length === 1} onClick={() => setFilters(current => current.filter(item => item.id !== filter.id))}>移除</button>
                </fieldset>;
              })}
            </div>
            <button type="button" className="secondary" onClick={() => setFilters(current => [...current, { id: `filter-${Date.now()}-${current.length}`, field: fields[0]!.fieldId, op: "eq", value: "" }])}>＋ 添加筛选条件</button>
          </details>

          <details className="builder-section">
            <summary>3. 分组与汇总 <small>{aggregates.length === 0 ? "未启用汇总" : `${aggregates.length} 个汇总`}</small></summary>
            <div className="field-checklist" aria-label="分组字段">{fields.map(item => <label key={item.fieldId}><input type="checkbox" checked={groupBy.includes(item.fieldId)} onChange={event => setGroupBy(current => event.target.checked ? [...current, item.fieldId] : current.filter(value => value !== item.fieldId))} />{item.name}</label>)}</div>
            <div className="builder-rows">{aggregates.map((aggregate, index) => <fieldset className="builder-row" key={aggregate.id}><legend>汇总 {index + 1}</legend>
              <label>结果形式<select value={aggregate.function} onChange={event => updateAggregate(index, { function: event.target.value as AggregateDraft["function"] })}>{Object.entries(aggregateLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
              <label>汇总字段<select value={aggregate.field} onChange={event => updateAggregate(index, { field: event.target.value })}><option value="">全部记录</option>{fields.map(item => <option key={item.fieldId} value={item.fieldId}>{item.name}</option>)}</select></label>
              <label>结果名称<input value={aggregate.alias} placeholder="例如：平均水位" onChange={event => updateAggregate(index, { alias: event.target.value })} /></label>
              <button type="button" className="secondary remove-row" onClick={() => setAggregates(current => current.filter(item => item.id !== aggregate.id))}>移除</button>
            </fieldset>)}</div>
            <button type="button" className="secondary" onClick={() => setAggregates(current => [...current, { id: `aggregate-${Date.now()}-${current.length}`, function: "count", field: "", alias: "" }])}>＋ 添加汇总</button>
          </details>

          <details className="builder-section">
            <summary>4. 排序与分页 <small>{sorts.length} 个排序 · 每页 {pageSize} 行</small></summary>
            <div className="builder-rows">{sorts.map((sort, index) => <fieldset className="builder-row compact" key={sort.id}><legend>排序 {index + 1}</legend><label>排序字段<select value={sort.field} onChange={event => updateSort(index, { field: event.target.value })}>{fields.map(item => <option key={item.fieldId} value={item.fieldId}>{item.name}</option>)}</select></label><label>方向<select value={sort.direction} onChange={event => updateSort(index, { direction: event.target.value as SortDraft["direction"] })}><option value="asc">升序</option><option value="desc">降序</option></select></label><button type="button" className="secondary remove-row" onClick={() => setSorts(current => current.filter(item => item.id !== sort.id))}>移除</button></fieldset>)}</div>
            <div className="builder-actions"><button type="button" className="secondary" onClick={() => setSorts(current => [...current, { id: `sort-${Date.now()}-${current.length}`, field: fields[0]!.fieldId, direction: "asc" }])}>＋ 添加排序</button><label>每页行数<select value={pageSize} onChange={event => { setPageSize(Number(event.target.value)); setOffset(0); }}><option value="25">25</option><option value="50">50</option><option value="100">100</option><option value="250">250</option></select></label></div>
          </details>

          <div className="query-request-summary"><strong>提交摘要</strong><span>{filters.filter(item => item.op === "is_null" || item.value).length} 个筛选 · {groupBy.length} 个分组字段 · {aggregates.length} 个汇总 · {sorts.length} 个排序</span></div>
          <div className="form-actions"><span className="muted">查询绑定当前不可变版本，不会修改原始数据。</span><button disabled={!allowed} onClick={() => void run(submitQuery)}>执行查询</button></div>
        </div>
      )}

      <details className="secondary-workflow" hidden={!selected}>
        <summary>需要跨版本核对？比较不可变版本中的指标</summary>
        <label>指标名称<input value={metricName} onChange={event => setMetricName(event.target.value)} /></label>
        <p id="metric-version-help" className="muted">从当前 Dataset 的版本历史中选择，不再手填版本 ID。</p>
        <ListBox aria-label="比较版本" aria-describedby="metric-version-help" selectionMode="multiple" selectedKeys={new Set(metricVersions)} onSelectionChange={onVersionSelection} className="version-list">
          {versions.map(item => <ListBoxItem id={item.versionId} key={item.versionId} textValue={item.versionId}>{item.versionId}<small>{new Date(item.createdAt).toLocaleString()}</small></ListBoxItem>)}
        </ListBox>
        <button disabled={!allowed || !metricName || metricVersions.length === 0} onClick={() => void run(compareMetrics)}>比较</button>
      </details>

      {error && <p role="alert" className="inline-error">{errorText(error)}</p>}
      {deadlineStopped && <p role="status" className="notice">已超过服务期限，自动检查已停止；操作 ID 已保留。<button onClick={() => void checkNow()}>手动检查</button></p>}
      {result && (
        <div className="result-surface">
          <div className="result-heading"><div><span className={`state-badge state-${result.state.toLowerCase()}`}>{stateLabels[result.state] ?? result.state}</span><h3>查询结果</h3></div><details><summary>操作标识</summary><code>{result.queryId}</code></details></div>
          {activeHistoryEntry && <div className="selected-resource result-context"><span>结果数据</span><strong>{activeHistoryEntry.title}</strong><small>不可变版本 {activeHistoryEntry.versionId}</small></div>}
          {requestSnapshot && <details className="request-snapshot"><summary>本次查询请求</summary><pre>{pretty(requestSnapshot)}</pre></details>}
          {["RUNNING", "QUEUED"].includes(result.state) && <button onClick={() => void run(cancel)}>取消查询</button>}
          {Boolean(result.error) && <p className="inline-error">{String((result.error as { code?: unknown }).code ?? "QUERY_FAILED")}</p>}
          {result.result && <><div className="table-scroll" role="region" tabIndex={0} aria-label="查询结果表格"><table><thead><tr>{result.result.fields.map((item, index) => { const fieldId = item.fieldId ?? item.id ?? ""; return <th key={index}>{fieldId ? fieldName(fieldId) : `字段 ${index + 1}`}</th>; })}</tr></thead><tbody>{result.result.rows.map((row, rowIndex) => <tr key={offset + rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell === null ? "∅" : String(cell)}</td>)}</tr>)}</tbody></table></div>{result.result.truncated && <p>返回结果已截断；计算覆盖范围见查询回执。</p>}<div className="pagination"><button className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - pageSize))}>上一页</button><span>第 {Math.floor(offset / pageSize) + 1} 页</span><button className="secondary" disabled={result.result.nextOffset === null} onClick={() => setOffset(result.result!.nextOffset!)}>下一页</button></div><a href={`/data/v3/queries/${encodeURIComponent(queryId)}/content`}>下载查询结果</a></>}
          {result.state === "SUCCEEDED" && !result.resultAvailable && <p>结果已过期，可保留回执或重新查询。</p>}
          <details><summary>查询回执与计算范围</summary><pre>{pretty(result.receipt)}</pre></details>
        </div>
      )}

      <details className="query-history">
        <summary>最近查询记录（{historyEntries.length}）</summary>
        <div className="history-heading"><p className="muted">浏览器保存操作 ID 和对应请求快照，便于准确恢复；服务端仍是结果和回执的权威来源。</p><button type="button" className="secondary" disabled={historyEntries.length === 0} onClick={clearHistory}>清空本地记录</button></div>
        {historyEntries.length === 0 ? <p className="muted">暂无查询记录。</p> : <div className="table-scroll" role="region" tabIndex={0} aria-label="查询历史表格"><table><thead><tr><th>时间</th><th>数据</th><th>摘要</th><th>操作</th></tr></thead><tbody>{historyEntries.map(entry => <tr key={entry.queryId}><td>{new Date(entry.createdAt).toLocaleString()}</td><td>{entry.title}<small>{entry.versionId}</small></td><td>{entry.summary}</td><td><button type="button" className="link-button" onClick={() => void openHistory(entry.queryId)}>查看结果</button></td></tr>)}</tbody></table></div>}
      </details>
    </section>
  );
}
