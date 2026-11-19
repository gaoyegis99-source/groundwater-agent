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
  value: string;
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

export type DataFilters = NonNullable<DataQueryRequest['filters']>;

export function toDataFilters(drafts: TableFilter[], fields: readonly { fieldId: string; type: string }[]) {
  const filters: DataFilters = [];
  const errors = drafts.map(({ field, operator, value }) => {
    const type = fields.find(item => item.fieldId === field)?.type.toLowerCase() ?? '';
    const text = value.trim();
    let parsed: string | number | boolean = value;
    if (operator === 'CONTAINS') {
      if (!text) return null;
    } else if (/^(?:u?int64|u?bigint)$/.test(type)) {
      if (!text) return null;
      if (!/^[+-]?\d+$/.test(text)) return '请输入整数。';
      const unsigned = type.startsWith('u');
      const minimum = unsigned ? 0n : -(2n ** 63n);
      const maximum = unsigned ? 2n ** 64n - 1n : 2n ** 63n - 1n;
      const number = BigInt(text);
      if (number < minimum || number > maximum) return `请输入 ${minimum} 至 ${maximum} 之间的整数。`;
      const approximate = Number(number);
      // Data accepts bound strings and compares them against the integer column without rounding.
      parsed = Number.isSafeInteger(approximate) ? approximate : String(number);
    } else if (/^(?:u?int|float|double|decimal)/.test(type)) {
      if (!text) return null;
      parsed = Number(text);
      if (!Number.isFinite(parsed)) return '请输入有限数值。';
    } else if (type === 'bool' || type === 'boolean') {
      if (!text) return null;
      if (text !== 'true' && text !== 'false') return '请输入 true 或 false。';
      parsed = text === 'true';
    }
    filters.push({ field, op: filterOperators[operator], value: parsed });
    return null;
  });
  return { filters, errors };
}

export function toDataSort(
  sort: TableSort[],
): NonNullable<DataQueryRequest['sort']> {
  return sort.map(item => ({
    field: item.field,
    direction: item.direction.toLowerCase() as 'asc' | 'desc',
  }));
}
