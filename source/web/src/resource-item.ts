import type { ResourceDetail, ResourceItem, ResourceReference } from './types';

export function isResourceReference(value: unknown): value is ResourceReference {
  if (value === null || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  return item.kind === 'data_version' && item.service === 'data-primary'
    && typeof item.datasetId === 'string' && typeof item.versionId === 'string'
    && typeof item.semanticId === 'string' && typeof item.objectId === 'string'
    && typeof item.checksum === 'string' && typeof item.format === 'string'
    && typeof item.mediaType === 'string' && typeof item.sizeBytes === 'number'
    && Number.isFinite(item.sizeBytes)
    && (item.outputName === undefined || typeof item.outputName === 'string');
}

/** Convert a validated immutable Data version into the attachment contract. */
export function resourceItemFromDetail(version: ResourceDetail, object: ResourceDetail['objects'][number]): ResourceItem {
  return {
    kind: 'data_version', service: 'data-primary', datasetId: version.datasetId,
    versionId: version.versionId, semanticId: version.semanticId, objectId: object.objectId,
    checksum: object.checksum, mediaType: object.mediaType, format: object.format,
    sizeBytes: object.size, name: version.title,
    familyId: version.datasetId,
  };
}
