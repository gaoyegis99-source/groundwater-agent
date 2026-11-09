import type { ResourceDetail, ResourceItem } from './types';

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
