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
  canQueryContent?: boolean;
  previewDeadlineSeconds: number;
  queryDeadlineSeconds?: number;
  onQuery?: () => void;
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

export function CatalogPanel({ controller, run, canWriteSemantics, canPreview, canQueryContent = false, previewDeadlineSeconds, queryDeadlineSeconds, onQuery }: Props) {
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
    appliedFilterCount,
    clearAdvancedFilters,
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
    refreshing,
    catalogError,
    filterError,
    selectedVersionId,
    detailLoading,
    detailRefreshing,
    detailError,
    saveError,
    retryCatalog,
    retryFilters,
    retryDetail,
    load,
    inspect,
    loadHistory,
    loadSemanticHistory,
    inspectSemantic,
    saveSemantics,
  } = controller;
  return (
    <div className="catalog-workspace" data-selected={Boolean(selectedVersionId)}>
      <section className="panel catalog-browser" aria-label="数据目录">
        <form className="catalog-search" onSubmit={event => { event.preventDefault(); void run(() => load()); }}>
          <div className="catalog-search-row">
          <label className="catalog-keyword"><span className="sr-only">搜索数据</span>
            <input aria-label="搜索数据" placeholder="名称、描述或业务术语" value={search} onChange={event => setSearch(event.target.value)} />
          </label>
          <button disabled={refreshing}>{refreshing ? "搜索中…" : "搜索"}</button>
          </div>
          <div className="filter-toolbar">
          <details className="advanced-filters">
          <summary><span>高级筛选</span>{appliedFilterCount > 0 && <span className="filter-count">已应用 {appliedFilterCount} 项</span>}</summary>
          <div className="advanced-filter-fields">
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
          <div className="catalog-search-actions"><button disabled={refreshing}>应用筛选</button></div>
          </div>
          </details>
          {appliedFilterCount > 0 && <button type="button" className="link-button" onClick={clearAdvancedFilters}>清除筛选</button>}
          </div>
        </form>
        <p className="catalog-status" role="status">{refreshing ? "正在发现可用数据…" : catalogError ? "数据目录读取未完成。" : totalMatches > 0 ? `${totalMatches} 份数据${cursor ? ` · 已加载 ${datasets.length} 份` : ""}` : ""}</p>
        {catalogError && <div className="read-feedback"><p role="alert" className="inline-error">{errorText(catalogError)}</p><button className="secondary" disabled={refreshing} onClick={() => void run(retryCatalog)}>重试读取数据目录</button></div>}
        {filterError && <div className="read-feedback"><p role="alert" className="inline-error">筛选选项读取未完成。{errorText(filterError)}</p><button className="secondary" onClick={() => void run(retryFilters)}>重试读取筛选选项</button></div>}
        {!loading && !catalogError && datasets.length === 0 && (
          <div className="empty-state compact">
            <strong>没有找到匹配的数据</strong>
            <p>{search || appliedFilterCount ? "调整关键词或筛选条件。" : "可从“发布数据”添加资料。"}</p>
          </div>
        )}
        <ul className="catalog" aria-label="数据目录结果" aria-busy={refreshing || loadingMore}>
          {datasets.map(dataset => (
            <li key={dataset.datasetId} className={selected?.datasetId === dataset.datasetId ? "selected" : undefined}>
              <button title={dataset.title} aria-pressed={selected?.datasetId === dataset.datasetId || selectedVersionId === dataset.currentVersion} onClick={() => void run(() => inspect(dataset.currentVersion))}>
                <span>{dataset.title}</span>
                <span className="catalog-meta">{dataset.formats.map(value => value.toUpperCase()).join(" · ") || "格式未知"}{dataset.tags.length > 0 ? ` · ${dataset.tags.join(" · ")}` : ""}</span>
                {dataset.matchReasons.length > 0 && <span className="match-reasons">{dataset.matchReasons.map((reason, index) => <em key={`${reason.kind}-${index}`}>{reasonLabels[reason.kind] ?? reason.kind}</em>)}</span>}
              </button>
              <span aria-hidden="true">›</span>
            </li>
          ))}
        </ul>
        {cursor && <button disabled={loadingMore} onClick={() => void run(() => load(true))}>{loadingMore ? "正在加载…" : "加载更多数据"}</button>}
      </section>
      <section className={`panel catalog-detail${selectedVersionId ? "" : " is-empty"}`}>
        {selectedVersionId && !selected && <h2 className="pending-title">数据内容</h2>}
        {detailLoading && <p className="catalog-status" role="status">正在读取不可变数据版本…</p>}
        {Boolean(detailError) && <div className="read-feedback"><p role="alert" className="inline-error">{errorText(detailError)}</p><button className="secondary" disabled={detailRefreshing} onClick={() => void run(retryDetail)}>{detailRefreshing ? "正在重试…" : "重试读取版本详情"}</button></div>}
        {!selected && !selectedVersionId && datasets.length > 0 ? (
          <p className="detail-placeholder">选择一份数据，查看内容。</p>
        ) : selected ? (
          <>
            <div className="resource-heading">
              <div><h2>{selected.title}</h2><p>{selected.descriptor.rowCount == null ? "行数未检查" : `${selected.descriptor.rowCount} 行`} · {selected.descriptor.fields.length} 个字段</p></div>
              {onQuery && <button className="secondary" onClick={onQuery}>查询这份数据</button>}
            </div>
            <PreviewPanel
              key={selected.versionId}
              version={selected}
              allowed={canPreview}
              queryAllowed={canQueryContent}
              deadlineSeconds={previewDeadlineSeconds}
              queryDeadlineSeconds={queryDeadlineSeconds}
              run={run}
            />
            <details className="catalog-properties">
              <summary>版本与来源</summary>
              <div className="version-selector">
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
                  <div><h4 id="governance-title">治理记录</h4></div>
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
            </details>
            <details className="catalog-properties"><summary>字段概览</summary><FieldProfile version={selected} /></details>
            <details className="governance-details">
              <summary>治理字段含义</summary>
              <SemanticEditor
                version={selected}
                allowed={canWriteSemantics}
                onSave={draft => run(() => saveSemantics(draft))}
              />
              {saveError && <p role="alert" className="inline-error">{errorText(saveError)}</p>}
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
        ) : null}
      </section>
    </div>
  );
}
