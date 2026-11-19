import { Activity, lazy, Suspense, useEffect, useId, useState } from 'react';
import { tableColumnLayout } from '@platform/ui-boundary';
import { type ApiError, type DataQueryRequest, runDataQuery } from '../api';
import { ErrorNotice } from '../ErrorNotice';
import { EmptyState, Loading } from '../ui/Feedback';
import type { DataQueryResponse, ResourceDetail } from '../types';
import { fieldUnit, fieldWithUnit, scientificCell } from './scientific-display';
import {
  toDataFilters,
  toDataSort,
  type TableFilter,
  type TableSort,
} from './query';

const ChartView = lazy(() =>
  import('./ChartView').then(module => ({ default: module.ChartView })),
);

export function TableViewer({
  resource,
  project,
  initialChart = false,
}: {
  resource: ResourceDetail;
  project: string;
  initialChart?: boolean;
}) {
  const available = resource.descriptor.fields ?? [];
  const [read, setRead] = useState<{ key: string; page: DataQueryResponse } | { key: string; error: ApiError }>();
  const [fields, setFields] = useState<string[]>([]);
  const [filters, setFilters] = useState<TableFilter[]>([]);
  const [sort, setSort] = useState<TableSort[]>([]);
  const [offset, setOffset] = useState(0);
  const [view, setView] = useState<'initial-table' | 'table' | 'chart'>(initialChart ? 'chart' : 'initial-table');
  const [retry, setRetry] = useState(0);
  const filterErrorId = useId();
  const { filters: queryFilters, errors: filterErrors } = toDataFilters(filters, available);
  const filtersValid = !filterErrors.some(Boolean);
  const filtersKey = JSON.stringify(queryFilters);
  const [settledFiltersKey, setSettledFiltersKey] = useState(filtersKey);
  const applyingFilters = filtersValid && filtersKey !== settledFiltersKey;
  const chart = view === 'chart';
  const tableKey = JSON.stringify({
    query: {
      versionId: resource.versionId,
      semanticId: resource.semanticId,
      fields,
      filters: queryFilters,
      sort: toDataSort(sort),
    },
    project, offset, retry,
  });
  const currentRead = read?.key === tableKey ? read : undefined;
  const page = currentRead && 'page' in currentRead ? currentRead.page : null;
  const error = currentRead && 'error' in currentRead ? currentRead.error : null;

  useEffect(() => {
    if (!applyingFilters) return;
    const timer = window.setTimeout(() => setSettledFiltersKey(filtersKey), 250);
    return () => window.clearTimeout(timer);
  }, [applyingFilters, filtersKey]);

  useEffect(() => {
    if (chart || !filtersValid || applyingFilters || currentRead) return;
    const controller = new AbortController();
    let finished = false;
    const request = JSON.parse(tableKey) as { query: DataQueryRequest; offset: number };
    void runDataQuery(request.query, request.offset, 100, controller.signal)
      .then(value => {
        finished = true;
        if (!controller.signal.aborted) setRead({ key: tableKey, page: value });
      })
      .catch(caught => {
        finished = true;
        if (!controller.signal.aborted) setRead({ key: tableKey, error: caught as ApiError });
      });
    return () => { if (!finished) controller.abort(); };
  }, [chart, filtersValid, applyingFilters, currentRead, tableKey]);

  const updateFilter = (index: number, update: Partial<TableFilter>) => {
    setOffset(0);
    setFilters(current =>
      current.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...update } : item,
      ),
    );
  };

  const result = page?.result;
  const matchedRows = page?.receipt?.coverage.matchedRows;
  const displayFields =
    result?.fields.map(field => ({
      ...field,
      name: available.find(item => item.fieldId === field.fieldId)?.name ?? field.fieldId,
      layout: tableColumnLayout(field.type),
    })) ?? [];

  return (
    <div className="table-workspace">
      <div className="artifact-toolbar">
        <button
          className={!chart ? 'active' : ''}
          aria-pressed={!chart}
          onClick={() => setView(view === 'initial-table' ? 'initial-table' : 'table')}
        >
          表格
        </button>
        <button
          className={chart ? 'active' : ''}
          aria-pressed={chart}
          onClick={() => setView('chart')}
        >
          图表
        </button>
        {!chart && filtersValid && !applyingFilters && result && <span className="table-row-count">
          {matchedRows == null ? `本页 ${result.rows.length.toLocaleString()} 行` : `匹配 ${matchedRows.toLocaleString()} 行`}
        </span>}
      </div>

      <details className="table-controls">
        <summary>字段、筛选与排序</summary>
        <label>
          显示字段（最多 20 个）
          <select
            aria-label="显示字段"
            multiple
            value={
              fields.length ? fields : available.map(field => field.fieldId)
            }
            onChange={event => {
              setOffset(0);
              setFields(
                [...event.target.selectedOptions]
                  .map(option => option.value)
                  .slice(0, 20),
              );
            }}
          >
            {available.map(field => (
              <option key={field.fieldId} value={field.fieldId}>
                {field.name}
              </option>
            ))}
          </select>
        </label>

        <div className="query-list">
          <strong>筛选</strong>
          {filters.map((filter, index) => (
            <div className="query-row" key={index}>
              <select
                aria-label={`筛选字段 ${index + 1}`}
                value={filter.field}
                onChange={event =>
                  updateFilter(index, {
                    field: event.target.value,
                    value: '',
                  })
                }
              >
                {available.map(field => (
                  <option key={field.fieldId} value={field.fieldId}>
                    {field.name}
                  </option>
                ))}
              </select>
              <select
                aria-label={`筛选运算 ${index + 1}`}
                value={filter.operator}
                onChange={event =>
                  updateFilter(index, {
                    operator: event.target.value as TableFilter['operator'],
                  })
                }
              >
                {[
                  'EQ',
                  'NE',
                  'LT',
                  'LTE',
                  'GT',
                  'GTE',
                  'CONTAINS',
                ].map(operator => (
                  <option key={operator}>{operator}</option>
                ))}
              </select>
              <input
                aria-label={`筛选值 ${index + 1}`}
                value={filter.value}
                aria-invalid={Boolean(filterErrors[index])}
                aria-describedby={filterErrors[index] ? `${filterErrorId}-${index}` : undefined}
                onChange={event =>
                  updateFilter(index, { value: event.target.value })
                }
              />
              <button
                aria-label={`删除筛选 ${index + 1}`}
                onClick={() =>
                  setFilters(current =>
                    current.filter((_, itemIndex) => itemIndex !== index),
                  )
                }
              >
                删除
              </button>
              {filterErrors[index] && <small className="field-error" id={`${filterErrorId}-${index}`} role="alert">
                {filterErrors[index]}
              </small>}
            </div>
          ))}
          <button
            disabled={!available.length || filters.length >= 8}
            onClick={() =>
              setFilters(current => [
                ...current,
                { field: available[0].fieldId, operator: 'EQ', value: '' },
              ])
            }
          >
            添加筛选
          </button>
        </div>

        <div className="query-list">
          <strong>排序</strong>
          {sort.map((item, index) => (
            <div className="query-row" key={index}>
              <select
                aria-label={`排序字段 ${index + 1}`}
                value={item.field}
                onChange={event => {
                  setOffset(0);
                  setSort(current =>
                    current.map((value, itemIndex) =>
                      itemIndex === index
                        ? { ...value, field: event.target.value }
                        : value,
                    ),
                  );
                }}
              >
                {available.map(field => (
                  <option key={field.fieldId} value={field.fieldId}>
                    {field.name}
                  </option>
                ))}
              </select>
              <select
                aria-label={`排序方向 ${index + 1}`}
                value={item.direction}
                onChange={event => {
                  setOffset(0);
                  setSort(current =>
                    current.map((value, itemIndex) =>
                      itemIndex === index
                        ? {
                            ...value,
                            direction: event.target
                              .value as TableSort['direction'],
                          }
                        : value,
                    ),
                  );
                }}
              >
                <option value="ASC">升序</option>
                <option value="DESC">降序</option>
              </select>
              <button
                aria-label={`删除排序 ${index + 1}`}
                onClick={() =>
                  setSort(current =>
                    current.filter((_, itemIndex) => itemIndex !== index),
                  )
                }
              >
                删除
              </button>
            </div>
          ))}
          <button
            disabled={!available.length || sort.length >= 3}
            onClick={() =>
              setSort(current => [
                ...current,
                { field: available[0].fieldId, direction: 'ASC' },
              ])
            }
          >
            添加排序
          </button>
        </div>
      </details>
      {applyingFilters && <Loading>正在更新筛选…</Loading>}
      {!chart && filtersValid && !applyingFilters && error && <div className="workbench-read-error">
        <ErrorNotice title="表格查询未完成" error={error} />
        <button type="button" onClick={() => setRetry(value => value + 1)}>重新查询</button>
      </div>}
      {!chart && filtersValid && !applyingFilters && !currentRead && <Loading>正在读取表格…</Loading>}
      {view !== 'initial-table' && <Activity mode={chart && filtersValid && !applyingFilters ? 'visible' : 'hidden'}>
        <Suspense fallback={<Loading>正在加载图表…</Loading>}>
          <ChartView resource={resource} fields={available} filters={queryFilters} />
        </Suspense>
      </Activity>}
      {!chart && filtersValid && !applyingFilters && page && result && (
        result.rows.length === 0 ? (
          <EmptyState title="没有符合条件的数据。" description="调整筛选条件后可重新查询。" />
        ) : (
          <>
            <div className="table-scroll query-result-table" role="region" aria-label="查询结果表" tabIndex={0}>
              <table>
                <caption>第 {(offset + 1).toLocaleString()}–{(offset + result.rows.length).toLocaleString()} 行。缺测表示空值，0 表示实际零值。</caption>
                <thead>
                  <tr>
                    {displayFields.map(field => (
                      <th key={field.fieldId} scope="col" data-column={field.layout}>
                        {fieldWithUnit(field.name, fieldUnit(resource, field.fieldId))}
                        <small>{field.type}</small>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((row, index) => (
                    <tr key={offset + index}>
                      {displayFields.map((field, column) => (
                        <td key={field.fieldId} data-column={field.layout} data-missing={row[column] == null || undefined}>{scientificCell(row[column])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <nav className="artifact-toolbar table-pagination" aria-label="查询结果分页">
              <button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - 100))}
              >
                上一页
              </button>
              <button
                disabled={result.nextOffset === null}
                onClick={() => setOffset(result.nextOffset!)}
              >
                下一页
              </button>
            </nav>
          </>
        )
      )}
    </div>
  );
}
