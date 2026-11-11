import type { RunAction } from "./api";
import { errorText, pretty } from "./api";
import { FieldProfile } from "./DataProfile";
import { PreviewPanel } from "./PreviewPanel";
import { SemanticEditor } from "./SemanticEditor";
import type { useCatalog } from "./use-catalog";

type Props = {
  controller: ReturnType<typeof useCatalog>;
  run: RunAction;
  canWriteSemantics: boolean;
  canPreview: boolean;
  previewDeadlineSeconds: number;
};

function actorLabel(actor: Record<string, unknown>): string {
  return typeof actor.displayName === "string"
    ? actor.displayName
    : typeof actor.display_name === "string"
      ? actor.display_name
      : typeof actor.subject === "string" ? actor.subject : "未知主体";
}

const reasonLabels: Record<string, string> = {
  query: "名称或描述匹配",
  tag: "标签匹配",
  format: "格式匹配",
  field: "字段匹配",
  unit: "单位匹配",
};

export function CatalogPanel({ controller, run, canWriteSemantics, canPreview, previewDeadlineSeconds }: Props) {
  const {
    datasets,
    search,
    setSearch,
    tag,
    setTag,
    format,
    setFormat,
    fieldTerm,
    setFieldTerm,
    unit,
    setUnit,
    sort,
    setSort,
    clearFilters,
    inventory,
    totalMatches,
    cursor,
    selected,
    governance,
    history,
    historyCursor,
    semanticHistory,
    semanticHistoryCursor,
    historicalSemantic,
    loading,
    loadingMore,
    error,
    load,
    inspect,
    loadHistory,
    loadSemanticHistory,
    inspectSemantic,
    saveSemantics,
  } = controller;
  return (
    <div className="catalog-workspace">
      <section className="panel catalog-browser">
        <div className="panel-heading compact">
          <div>
            <p className="step-kicker">任务 1</p>
            <h2>选择数据</h2>
            <p>按名称、描述或字段含义查找，选择后再核对不可变版本。</p>
          </div>
          {!loading && <span className="count-badge">匹配 {totalMatches} · 已加载 {datasets.length}</span>}
        </div>
        <form className="catalog-search" onSubmit={event => { event.preventDefault(); void run(() => load()); }}>
          <label className="catalog-keyword">关键词
            <input aria-label="搜索数据" placeholder="名称、描述或业务术语" value={search} onChange={event => setSearch(event.target.value)} />
          </label>
          <label>标签
            <select aria-label="标签筛选" value={tag} onChange={event => setTag(event.target.value)}>
              <option value="">全部标签</option>
              {inventory?.tags.map(item => <option key={item.name} value={item.name}>{item.name}（{item.count}）</option>)}
            </select>
          </label>
          <label>格式
            <select aria-label="格式筛选" value={format} onChange={event => setFormat(event.target.value)}>
              <option value="">全部格式</option>
              {inventory?.formats.map(item => <option key={item.name} value={item.name}>{item.name.toUpperCase()}（{item.count}）</option>)}
            </select>
          </label>
          <label>字段含义
            <input aria-label="字段含义筛选" placeholder="例如：井号、含水层" value={fieldTerm} onChange={event => setFieldTerm(event.target.value)} />
          </label>
          <label>单位
            <input aria-label="单位筛选" placeholder="例如：m、mg/L" value={unit} onChange={event => setUnit(event.target.value)} />
          </label>
          <label>排序
            <select aria-label="目录排序" value={sort} onChange={event => setSort(event.target.value as typeof sort)}>
              <option value="relevance">相关度</option><option value="title-asc">标题 A–Z</option><option value="title-desc">标题 Z–A</option>
            </select>
          </label>
          <div className="catalog-search-actions"><button>应用筛选</button><button type="button" className="secondary" onClick={clearFilters}>清空</button></div>
        </form>
        <p className="catalog-status" role="status">{loading ? "正在发现可用数据…" : `共匹配 ${totalMatches} 份数据，当前已加载 ${datasets.length} 份。`}</p>
        {error && <p role="alert" className="inline-error">{errorText(error)}</p>}
        {!loading && datasets.length === 0 && (
          <div className="empty-state compact">
            <strong>没有找到匹配的数据</strong>
            <p>{search ? "换一个关键词，或清空搜索查看当前项目中的全部数据。" : "当前项目还没有可浏览的数据，可前往“发布数据”上传第一份资料。"}</p>
          </div>
        )}
        <ul className="catalog">
          {datasets.map(dataset => (
            <li key={dataset.datasetId} className={selected?.datasetId === dataset.datasetId ? "selected" : undefined}>
              <button onClick={() => void run(() => inspect(dataset.currentVersion))}>
                <span>{dataset.title}</span>
                <small>{dataset.description || "尚未填写业务说明"}</small>
                <span className="catalog-meta">{dataset.formats.map(value => value.toUpperCase()).join(" · ") || "格式未知"}{dataset.tags.length > 0 ? ` · ${dataset.tags.join(" · ")}` : ""}</span>
                {dataset.matchReasons.length > 0 && <span className="match-reasons">{dataset.matchReasons.map((reason, index) => <em key={`${reason.kind}-${index}`}>{reasonLabels[reason.kind] ?? reason.kind}</em>)}</span>}
              </button>
              <span aria-hidden="true">›</span>
            </li>
          ))}
        </ul>
        {cursor && <button disabled={loadingMore} onClick={() => void run(() => load(true))}>{loadingMore ? "正在加载…" : "加载更多数据"}</button>}
      </section>
      <section className="panel catalog-detail">
        <div className="panel-heading compact">
          <div><p className="step-kicker">核对</p><h2>版本与字段含义</h2><p>确认行数、字段质量和业务语义，再把数据用于查询或分析。</p></div>
        </div>
        {!selected ? (
          <div className="empty-state detail-empty">
            <span className="empty-icon" aria-hidden="true">✓</span>
            <div><strong>从目录选择一份数据。</strong><p>这里会展示当前版本、字段检查、语义说明和可下载文件。</p></div>
          </div>
        ) : (
          <>
            <div className="resource-heading">
              <div><span className="status-chip">不可变版本</span><h3>{selected.title}</h3><p>{selected.semantics.description || "尚未填写业务说明"}</p></div>
              <label>
                查看版本
                <select value={selected.versionId} onChange={event => void run(() => inspect(event.target.value))}>
                  {!history.some(version => version.versionId === selected.versionId) && <option value={selected.versionId}>{new Date(selected.createdAt).toLocaleString()}</option>}
                  {history.map(version => (
                    <option key={version.versionId} value={version.versionId}>
                      {new Date(version.createdAt).toLocaleString()}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {historyCursor && <button onClick={() => void run(loadHistory)}>更多版本</button>}
            <dl className="summary-grid">
              <div><dt>数据行数</dt><dd>{selected.descriptor.rowCount ?? "未检查"}</dd></div>
              <div><dt>字段数量</dt><dd>{selected.descriptor.fields.length}</dd></div>
              <div><dt>来源版本</dt><dd>{selected.inputVersions.length}</dd></div>
              <div><dt>创建时间</dt><dd>{new Date(selected.createdAt).toLocaleString()}</dd></div>
            </dl>
            {governance && (
              <section className="governance-overview" aria-labelledby="governance-title">
                <div className="section-heading compact">
                  <div><h4 id="governance-title">治理概览</h4><p>所有者、质量变化、来源关系和近期变更来自服务端可追溯记录。</p></div>
                  <span className="owner-chip">所有者：{governance.ownerSubject}</span>
                </div>
                <div className="governance-grid">
                  <section className="quality-trend" aria-labelledby="quality-trend-title">
                    <h5 id="quality-trend-title">质量趋势</h5>
                    {governance.qualityTrend.length === 0 ? <p className="muted">尚无可比较版本。</p> : (
                      <ol>
                        {governance.qualityTrend.map(point => (
                          <li key={point.versionId} className={point.versionId === governance.currentVersion ? "current" : undefined}>
                            <span>{new Date(point.createdAt).toLocaleDateString()}</span>
                            <strong>{point.rowCount ?? "未检查"} 行</strong>
                            <small>{Object.keys(point.quality).length > 0 ? `${Object.keys(point.quality).length} 项质量记录` : "未记录质量异常"}</small>
                            <details className="inline-technical"><summary>查看质量详情</summary><pre>{pretty(point.quality)}</pre></details>
                          </li>
                        ))}
                      </ol>
                    )}
                  </section>
                  <section className="lineage-readable" aria-labelledby="lineage-title">
                    <h5 id="lineage-title">数据来源</h5>
                    {governance.lineage.length === 0 ? <p className="muted">该数据由直接发布生成，没有声明上游数据。</p> : (
                      <ul>
                        {governance.lineage.map(edge => (
                          <li key={`${edge.outputVersionId}-${edge.inputVersionId}`}>
                            <strong>{edge.inputTitle}</strong>
                            <span>{edge.isCurrent ? "用于当前版本" : `用于 ${new Date(edge.createdAt).toLocaleDateString()} 的历史版本`}</span>
                            <details className="inline-technical"><summary>技术标识</summary><code>{edge.inputDatasetId} / {edge.inputVersionId}</code></details>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                </div>
                <details className="dataset-activity">
                  <summary>近期治理记录（{governance.activity.length}）</summary>
                  {governance.activity.length === 0 ? <p className="muted">尚无治理记录。</p> : (
                    <ol className="activity-timeline">
                      {governance.activity.map(item => (
                        <li key={item.operationId}>
                          <span className={`activity-state state-${item.state.toLowerCase()}`}>{item.state}</span>
                          <strong>{item.title}</strong>
                          <small>{item.subject} · {new Date(item.updatedAt).toLocaleString()}</small>
                        </li>
                      ))}
                    </ol>
                  )}
                </details>
              </section>
            )}
            <PreviewPanel
              key={selected.versionId}
              version={selected}
              allowed={canPreview}
              deadlineSeconds={previewDeadlineSeconds}
              run={run}
            />
            <FieldProfile version={selected} />
            <details className="governance-details">
              <summary><span>治理字段含义</span><small>补充描述、单位、限制和字段语义</small></summary>
              <SemanticEditor
                version={selected}
                allowed={canWriteSemantics}
                onSave={draft => run(() => saveSemantics(draft))}
              />
            </details>
            <details>
              <summary>查看语义修订历史</summary>
              <ol className="timeline">
                {semanticHistory.map(revision => (
                  <li key={revision.semanticId}>
                    <button className="link-button" onClick={() => void run(() => inspectSemantic(revision.semanticId))}>
                      {new Date(revision.createdAt).toLocaleString()}
                    </button>
                    <span>{actorLabel(revision.actor)}</span>
                    <code>{revision.semanticId}</code>
                  </li>
                ))}
              </ol>
              {semanticHistoryCursor && (
                <button onClick={() => void run(loadSemanticHistory)}>更多修订</button>
              )}
              {historicalSemantic && (
                <details>
                  <summary>选中修订的高级信息</summary>
                  <pre>{pretty(historicalSemantic.body)}</pre>
                </details>
              )}
            </details>
            <div className="downloads">
              {selected.objects.map(object => (
                <a
                  key={object.objectId}
                  href={`/data/v3/versions/${encodeURIComponent(selected.versionId)}/objects/${encodeURIComponent(object.objectId)}/content`}
                >
                  下载{object.role === "raw" ? "原始" : "查询"}文件 <span>{object.format?.toUpperCase() ?? "文件"}</span>
                </a>
              ))}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
