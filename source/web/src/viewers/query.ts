import type { DataQueryRequest } from '../api';

export type TableFilterOperator =
  | 'EQ'
  | 'NE'
  | 'LT'
  | 'LTE'
  | 'GT'
  | 'GTE'
  | 'CONTAINS';

export type TableFilter = {
  field: string;
  operator: TableFilterOperator;
  value: string | number | boolean | null;
};

export type TableSort = {
  field: string;
  direction: 'ASC' | 'DESC';
};

const filterOperators = {
  EQ: 'eq',
  NE: 'ne',
  LT: 'lt',
  LTE: 'le',
  GT: 'gt',
  GTE: 'ge',
  CONTAINS: 'contains',
} as const;

export function toDataFilters(
  filters: TableFilter[],
): NonNullable<DataQueryRequest['filters']> {
  return filters.map(item => ({
    field: item.field,
    op: filterOperators[item.operator],
    value: item.value,
  }));
}

export function toDataSort(
  sort: TableSort[],
): NonNullable<DataQueryRequest['sort']> {
  return sort.map(item => ({
    field: item.field,
    direction: item.direction.toLowerCase() as 'asc' | 'desc',
  }));
}
