import type { DataQueryValue, ResourceDetail } from '../types';

export function fieldUnit(resource: ResourceDetail, fieldId: string) {
  return resource.semantics.fields?.[fieldId]?.unit?.trim() || null;
}

export function fieldWithUnit(name: string, unit: string | null) {
  return unit ? `${name}（${unit}）` : name;
}

export function scientificCell(value: DataQueryValue | undefined) {
  return value == null ? '缺测' : value === '' ? '空文本' : String(value);
}
