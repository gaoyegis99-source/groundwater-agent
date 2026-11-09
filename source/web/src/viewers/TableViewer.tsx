import { lazy, Suspense, useEffect, useState } from 'react';
import { ApiError, runDataQuery } from '../api';
import { ErrorNotice } from '../ErrorNotice';
import type { DataQueryResponse, ResourceDetail } from '../types';
import {
  toDataFilters,
  toDataSort,
  type TableFilter,
  type TableSort,
} from './query';

const ChartView = lazy(() =>
  import('./ChartView').then(module => ({ default: module.ChartView })),
);

type Field = NonNullable<ResourceDetail['descriptor']['fields']>[number];

function typedValue(
  available: Field[],
  field: string,
  value: string,
): string | number | boolean {
  const type = available.find(item => item.fieldId === field)?.type ?? '';
  if (/bool/.test(type)) return value === 'true';
  if (/int|float|double|decimal/.test(type)) return Number(value);
  return value;
}

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
  const [page, setPage] = useState<DataQueryResponse | null>(null);
  const [fields, setFields] = useState<string[]>([]);
  const [filters, setFilters] = useState<TableFilter[]>([]);
  const [sort, setSort] = useState<TableSort[]>([]);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [chart, setChart] = useState(initialChart);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void runDataQuery(
      {
        versionId: resource.versionId,
        semanticId: resource.semanticId,
        fields,
        filters: toDataFilters(filters),
        sort: toDataSort(sort),
      },
      offset,
      100,
      controller.signal,
    )
      .then(value => {
        if (controller.signal.aborted) return;
        setPage(value);
        setLoading(false);
      })
      .catch(caught => {
        if (controller.signal.aborted) return;
        if (caught instanceof DOMException && caught.name === 'AbortError') return;
        setPage(null);
        setError(caught as ApiError);
        setLoading(false);
      });
    return () => controller.abort();
  }, [
    resource.versionId,
    resource.semanticId,
    project,
    offset,
    JSON.stringify(fields),
    JSON.stringify(filters),
    JSON.stringify(sort),
  ]);

  const updateFilter = (index: number, update: Partial<TableFilter>) => {
    setOffset(0);
    setFilters(current =>
      current.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...update } : item,
      ),
    );
  };

  const result = page?.result;
  const displayFields =
    result?.fields.map(field => ({
      ...field,
      name: available.find(item => item.fieldId === field.fieldId)?.name ?? field.fieldId,
    })) ?? [];

  return (
    <div className="table-workspace">
      {error && <ErrorNotice error={error} />}
      {loading && !page && <p>正在读取表格…</p>}
      {page && result && (
        <>
          <div className="artifact-toolbar">
            <button
              className={!chart ? 'active' : ''}
              onClick={() => setChart(false)}
            >
              表格
            </button>
            <button
              className={chart ? 'active' : ''}
              onClick={() => setChart(true)}
            >
              图表
            </button>
            <span>{page.receipt?.coverage.matchedRows ?? 0} 行</span>
            {loading && <span className="muted">正在更新…</span>}
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
                    value={String(filter.value ?? '')}
                    onChange={event =>
                      updateFilter(index, {
                        value: typedValue(
                          available,
                          filter.field,
                          event.target.value,
                        ),
                      })
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

          {chart ? (
            <Suspense fallback={<p className="muted">正在加载图表…</p>}>
              <ChartView
                resource={resource}
                fields={available}
                filters={filters}
              />
            </Suspense>
          ) : result.rows.length === 0 ? (
            <p>没有符合条件的数据。</p>
          ) : (
            <>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      {displayFields.map(field => (
                        <th key={field.fieldId}>
                          {field.name}
                          <small>{field.type}</small>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.rows.map((row, index) => (
                      <tr key={offset + index}>
                        {displayFields.map((field, column) => (
                          <td key={field.fieldId}>{String(row[column] ?? '∅')}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="artifact-toolbar">
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
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
