import { ApiError, callApi, readDataVersion } from './api';
import { resourceItemFromDetail } from './resource-item';
import type { ModelPresentation } from './product-ui';
import type { ResourceItem } from './types';

export async function readWorkflowInputCandidates(
  tag: string, inputs: ModelPresentation['inputs'], signal: AbortSignal,
): Promise<Record<string, ResourceItem>> {
  const params = new URLSearchParams({ tag, limit: '100' });
  const { items, nextCursor } = await callApi('data_list_datasets', `/data/datasets?${params}`, undefined, signal);
  // A partial catalog cannot establish unique role or batch membership.
  if (nextCursor) throw new ApiError(409, 'WORKFLOW_INPUT_CATALOG_INCOMPLETE');
  const rows = items.flatMap(item => {
    const tags = item.tags ?? [];
    const roles = tags.filter(value => value.startsWith('input:'));
    const batches = tags.filter(value => value.startsWith('case-batch:'));
    const parameter = `${roles[0]?.slice('input:'.length)}VersionId`;
    return roles.length === 1 && batches.length === 1 && Object.hasOwn(inputs, parameter)
      && item.currentVersion ? [{ parameter, batch: batches[0], item, version: item.currentVersion }] : [];
  });
  if (new Set(rows.map(row => row.batch)).size !== 1) return {};
  const candidates: Record<string, ResourceItem> = {};
  const unique = rows.filter(row => rows.filter(other => other.parameter === row.parameter).length === 1);
  for (let index = 0; index < unique.length; index += 6) {
    await Promise.all(unique.slice(index, index + 6).map(async row => {
      try {
        const detail = await readDataVersion(row.version, signal);
        if (detail.datasetId !== row.item.datasetId || detail.currentVersion !== row.version) return;
        const raw = detail.objects.filter(object => object.role === 'raw');
        if (raw.length === 1 && inputs[row.parameter].formats.includes(raw[0].format)) {
          candidates[row.parameter] = resourceItemFromDetail(detail, raw[0]);
        }
      } catch (error) {
        if (signal.aborted || !(error instanceof ApiError)) throw error;
      }
    }));
  }
  return candidates;
}
