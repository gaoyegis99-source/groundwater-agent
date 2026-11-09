import { sha256 } from '@noble/hashes/sha2.js';
import { bytesToHex } from '@noble/hashes/utils.js';
import { ApiError, callApi, reportApi, researchCsrfToken } from './api';
import { decodeResourceDetail } from './api-contract';
import type { ResourceItem } from './types';
import { scopedStorageKey } from './browser-scope';
import { newRequestId } from './message-routing';

export function checksumInWorker(file: File, progress: (processed: number) => void, signal?: AbortSignal) {
  const worker = new Worker(new URL('./hash.worker.ts', import.meta.url), { type: 'module' });
  return new Promise<string>((resolve, reject) => {
    const cancel = () => { worker.terminate(); reject(new ApiError(0, 'UPLOAD_CANCELLED')); };
    if (signal?.aborted) { cancel(); return; }
    signal?.addEventListener('abort', cancel, { once: true });
    worker.onmessage = ({ data }: MessageEvent<{ processed?: number; checksum?: string; error?: string }>) => {
      if (data.processed !== undefined) progress(data.processed);
      if (data.checksum) { signal?.removeEventListener('abort', cancel); worker.terminate(); resolve(data.checksum); }
      if (data.error) { signal?.removeEventListener('abort', cancel); worker.terminate(); reject(new ApiError(0, data.error)); }
    };
    worker.onerror = () => { signal?.removeEventListener('abort', cancel); worker.terminate(); reject(new ApiError(0, 'HASH_WORKER_FAILED')); };
    worker.postMessage(file);
  });
}

type Publication = {
  publicationId: string;
  state: string;
  receipt?: Receipt | null;
  idempotencyKey: string;
  manifestChecksum: string;
};
type Receipt = { publicationId: string; state: 'PUBLISHED'; outputs: { name: string; datasetId: string; versionId: string; semanticId: string }[] };

export async function publicationIdempotencyKey(metadata: Record<string, unknown>, storageScope = '') {
  const identity = JSON.stringify([
    storageScope,
    String(metadata.checksum),
    String(metadata.name),
    String(metadata.format),
    String(metadata.mediaType),
    String(metadata.description ?? ''),
  ]);
  const digest = sha256(new TextEncoder().encode(identity));
  return `web-${bytesToHex(digest).slice(0, 48)}`;
}

export function publicationRetryIdempotencyKey(idempotencyKey: string, publicationId: string) {
  const digest = sha256(new TextEncoder().encode(`${idempotencyKey}\u0000${publicationId}`));
  return `web-retry-${bytesToHex(digest).slice(0, 48)}`;
}

export function checksumsEqual(left: string, right: string) {
  return left.replace(/^sha256:/, '').toLowerCase() === right.replace(/^sha256:/, '').toLowerCase();
}

async function uploadObject(publicationId: string, file: File, checksum: string, progress: (loaded: number, total: number) => void, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const path = `/data/publications/${encodeURIComponent(publicationId)}/items/file`;
    const started = performance.now();
    const requestId = newRequestId();
    xhr.open('PUT', `/api/v12${path}`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.setRequestHeader('X-Request-ID', requestId);
    const csrf = researchCsrfToken();
    if (csrf) xhr.setRequestHeader('X-CSRF-Token', csrf);
    xhr.setRequestHeader('X-Content-SHA256', checksum.startsWith('sha256:') ? checksum : `sha256:${checksum}`);
    xhr.upload.onprogress = event => { if (event.lengthComputable) progress(event.loaded, event.total); };
    const uncertain = () => { reportApi('PUT', path, 0, performance.now() - started, requestId, 'UPLOAD_UNCERTAIN'); reject(new ApiError(0, 'UPLOAD_UNCERTAIN', [], requestId)); };
    const cancel = () => { xhr.abort(); reportApi('PUT', path, 0, performance.now() - started, requestId, 'UPLOAD_CANCELLED'); reject(new ApiError(0, 'UPLOAD_CANCELLED', [], requestId)); };
    if (signal?.aborted) { cancel(); return; }
    signal?.addEventListener('abort', cancel, { once: true });
    xhr.onerror = uncertain;
    xhr.onabort = () => { if (!signal?.aborted) uncertain(); };
    xhr.onload = () => {
      signal?.removeEventListener('abort', cancel);
      const responseRequestId = xhr.getResponseHeader('X-Request-ID') ?? requestId;
      if (xhr.status >= 200 && xhr.status < 300) { reportApi('PUT', path, xhr.status, performance.now() - started, responseRequestId); resolve(); return; }
      let code = `HTTP_${xhr.status}`;
      try { const value = JSON.parse(xhr.responseText); code = value?.code ?? code; } catch { /* status is sufficient */ }
      reportApi('PUT', path, xhr.status, performance.now() - started, responseRequestId, code);
      const retry = xhr.getResponseHeader('Retry-After');
      reject(new ApiError(xhr.status, code, [], responseRequestId, retry && /^\d+$/.test(retry) ? Number(retry) : null));
    };
    xhr.send(file);
  });
}

async function waitForReady(publicationId: string, signal?: AbortSignal) {
  while (true) {
    const publication = await callApi('data_get_publication', `/data/publications/${publicationId}`, undefined, signal) as Publication;
    if (publication.state === 'READY' || publication.state === 'PUBLISHED') return publication;
    if (publication.state === 'FAILED' || publication.state === 'ABORTED') throw new ApiError(409, `PUBLICATION_${publication.state}`);
    await new Promise<void>((resolve, reject) => {
      if (signal?.aborted) { reject(new ApiError(0, 'UPLOAD_CANCELLED')); return; }
      const abort = () => { window.clearTimeout(timer); reject(new ApiError(0, 'UPLOAD_CANCELLED')); };
      const timer = window.setTimeout(() => {
        signal?.removeEventListener('abort', abort);
        resolve();
      }, 500);
      signal?.addEventListener('abort', abort, { once: true });
    });
  }
}

type PendingPublication = {
  publicationId: string;
  uploadIdempotencyKey: string;
  publicationIdempotencyKey: string;
  manifestChecksum: string;
};

export const publicationPendingStorageKey = (storageScope: string, idempotencyKey: string) => scopedStorageKey(
  storageScope, 'upload-publication', idempotencyKey,
);

const legacyPendingKey = (storageScope: string, checksum: string) => scopedStorageKey(
  storageScope, 'upload', checksum.replace(/^sha256:/, '').toLowerCase(),
);

function readPendingPublication(value: string): PendingPublication | null {
  try {
    const record = JSON.parse(value) as Partial<PendingPublication>;
    if (
      typeof record.publicationId !== 'string'
      || typeof record.uploadIdempotencyKey !== 'string'
      || typeof record.publicationIdempotencyKey !== 'string'
      || typeof record.manifestChecksum !== 'string'
    ) return null;
    return record as PendingPublication;
  } catch {
    return null;
  }
}

function requirePublicationIdentity(publication: Publication, idempotencyKey: string) {
  if (
    publication.idempotencyKey !== idempotencyKey
    || !/^sha256:[0-9a-f]{64}$/.test(publication.manifestChecksum)
  ) throw new ApiError(502, 'PUBLICATION_RESPONSE_INVALID');
}

function storePendingPublication(storageScope: string, uploadIdempotencyKey: string, publication: Publication) {
  requirePublicationIdentity(publication, publication.idempotencyKey);
  const record: PendingPublication = {
    publicationId: publication.publicationId,
    uploadIdempotencyKey,
    publicationIdempotencyKey: publication.idempotencyKey,
    manifestChecksum: publication.manifestChecksum,
  };
  localStorage.setItem(
    publicationPendingStorageKey(storageScope, uploadIdempotencyKey),
    JSON.stringify(record),
  );
}

async function readPublication(publicationId: string, signal?: AbortSignal) {
  try {
    return await callApi(
      'data_get_publication',
      `/data/publications/${encodeURIComponent(publicationId)}`,
      undefined,
      signal,
    ) as Publication;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

async function resumePendingPublication(
  storageScope: string,
  checksum: string,
  uploadIdempotencyKey: string,
  signal?: AbortSignal,
) {
  const key = publicationPendingStorageKey(storageScope, uploadIdempotencyKey);
  const cached = localStorage.getItem(key);
  if (cached !== null) {
    const record = readPendingPublication(cached);
    if (record?.uploadIdempotencyKey === uploadIdempotencyKey) {
      const publication = await readPublication(record.publicationId, signal);
      if (
        publication
        && publication.idempotencyKey === record.publicationIdempotencyKey
        && publication.manifestChecksum === record.manifestChecksum
      ) return publication;
    }
    localStorage.removeItem(key);
  }

  const legacyKey = legacyPendingKey(storageScope, checksum);
  const legacyPublicationId = localStorage.getItem(legacyKey);
  if (legacyPublicationId) {
    const publication = await readPublication(legacyPublicationId, signal);
    localStorage.removeItem(legacyKey);
    if (publication?.idempotencyKey === uploadIdempotencyKey) {
      storePendingPublication(storageScope, uploadIdempotencyKey, publication);
      return publication;
    }
  }
  return null;
}

export async function uploadFile(
  file: File,
  metadata: Record<string, unknown>,
  storageScope: string,
  progress: (loaded: number, total: number) => void,
  signal?: AbortSignal,
  onPublication?: (publicationId: string, idempotencyKey: string) => void,
): Promise<ResourceItem> {
  const checksum = String(metadata.checksum);
  const idempotencyKey = await publicationIdempotencyKey(metadata, storageScope);
  const publicationRequest = (key: string) => ({
    idempotencyKey: key,
    outputs: [{
      name: 'file', title: String(metadata.name), format: String(metadata.format), mediaType: String(metadata.mediaType),
      policy: ['csv', 'parquet'].includes(String(metadata.format)) ? 'FILE_QUERYABLE' : 'FILE_ONLY', tags: [],
      semantics: { description: String(metadata.description ?? ''), source: 'web_upload' },
    }],
    inputVersions: [], provenance: { client: 'platform-web-v12' },
  });
  let begin = await resumePendingPublication(storageScope, checksum, idempotencyKey, signal);
  if (begin === null) {
    begin = await callApi(
      'data_begin_publication',
      '/data/publications',
      publicationRequest(idempotencyKey),
      signal,
    ) as Publication;
    requirePublicationIdentity(begin, idempotencyKey);
  }
  storePendingPublication(storageScope, idempotencyKey, begin);
  if (begin.state === 'FAILED' || begin.state === 'ABORTED') {
    const retryIdempotencyKey = publicationRetryIdempotencyKey(
      idempotencyKey,
      begin.publicationId,
    );
    begin = await callApi(
      'data_begin_publication', '/data/publications',
      publicationRequest(retryIdempotencyKey), signal,
    ) as Publication;
    requirePublicationIdentity(begin, retryIdempotencyKey);
    storePendingPublication(storageScope, idempotencyKey, begin);
  }
  onPublication?.(begin.publicationId, idempotencyKey);
  if (signal?.aborted) throw new ApiError(0, 'UPLOAD_CANCELLED');
  if (begin.state === 'OPEN') {
    await uploadObject(begin.publicationId, file, checksum, progress, signal);
    await callApi('data_prepare_publication', `/data/publications/${begin.publicationId}/prepare`, {}, signal);
  }
  const ready = await waitForReady(begin.publicationId, signal);
  const receipt = ready.state === 'PUBLISHED' && ready.receipt ? ready.receipt : await callApi('data_commit_publication', `/data/publications/${begin.publicationId}/commit`, {}, signal) as Receipt;
  const published = receipt.outputs.find(item => item.name === 'file');
  if (!published) throw new ApiError(502, 'PUBLICATION_RECEIPT_INVALID');
  const version = await callApi('data_get_version', `/data/versions/${published.versionId}`, undefined, signal, decodeResourceDetail);
  const object = version.objects.find(item => item.role === 'raw');
  if (!object || !checksumsEqual(object.checksum, checksum)) throw new ApiError(502, 'PUBLICATION_RECEIPT_INVALID');
  localStorage.removeItem(publicationPendingStorageKey(storageScope, idempotencyKey));
  localStorage.removeItem(legacyPendingKey(storageScope, checksum));
  return {
    kind: 'data_version', service: 'data-primary', datasetId: version.datasetId, versionId: version.versionId,
    semanticId: version.semanticId, objectId: object.objectId, checksum: object.checksum, mediaType: object.mediaType,
    format: object.format, sizeBytes: object.size, familyId: version.datasetId, name: version.title,
    descriptionPreview: version.semantics.description,
  };
}

export async function abortUpload(publicationId: string | null, idempotencyKey: string | null, storageScope: string) {
  if (publicationId) {
    await callApi('data_abort_publication', `/data/publications/${encodeURIComponent(publicationId)}/abort`, {});
  }
  if (idempotencyKey) {
    localStorage.removeItem(publicationPendingStorageKey(storageScope, idempotencyKey));
  }
}

export function uploadOutcomeUncertain(error: unknown) {
  return error instanceof ApiError && (error.code === 'UPLOAD_UNCERTAIN' || error.status >= 500 ||
    (error.code === 'UPLOAD_RESPONSE_INVALID' && error.status >= 200 && error.status < 300));
}
