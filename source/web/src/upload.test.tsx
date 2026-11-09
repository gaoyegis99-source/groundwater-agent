import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import * as apiClient from './api';
import { ApiError, callApi, observeApi } from './api';
import { scopedStorageKey } from './browser-scope';
import { MessageContent } from './MessageContent';
import { UploadDialog } from './UploadDialog';
import * as upload from './upload';
import { messageBody } from './message-routing';
import { dataDetail, messageReceiptFixture } from './test-fixtures';

afterEach(() => {
  localStorage.clear();
  vi.useRealTimers();
});

it.each([
  [new ApiError(422, 'RESOURCE_FORMAT_INVALID'), false],
  [new ApiError(0, 'FILE_READ_FAILED'), false],
  [new ApiError(0, 'HASH_WORKER_FAILED'), false],
  [new ApiError(0, 'UPLOAD_UNCERTAIN'), true],
  [new ApiError(200, 'UPLOAD_RESPONSE_INVALID'), true],
  [new ApiError(503, 'SERVICE_UNAVAILABLE'), true],
])('classifies upload outcome without retrying: %o', (error, expected) => {
  expect(upload.uploadOutcomeUncertain(error)).toBe(expected);
});

it('binds upload idempotency to content and publication metadata without secure-context Web Crypto', async () => {
  const base = {
    checksum: `sha256:${'a'.repeat(64)}`, name: '监测.csv', format: 'csv',
    mediaType: 'text/csv', description: '监测数据',
  };
  vi.stubGlobal('crypto', undefined);
  try {
    const first = await upload.publicationIdempotencyKey(base);
    expect(first).toMatch(/^web-[0-9a-f]{48}$/);
    expect(await upload.publicationIdempotencyKey({ ...base })).toBe(first);
    expect(await upload.publicationIdempotencyKey({ ...base, name: '另一个名称.csv' })).not.toBe(first);
    expect(await upload.publicationIdempotencyKey({ ...base, description: '不同说明' })).not.toBe(first);
    expect(await upload.publicationIdempotencyKey(base, 'other-user')).not.toBe(first);
  } finally {
    vi.unstubAllGlobals();
  }
});

it('derives a deterministic retry identity from the failed publication', () => {
  const first = upload.publicationRetryIdempotencyKey('web-upload', 'publication-1');
  expect(first).toMatch(/^web-retry-[0-9a-f]{48}$/);
  expect(upload.publicationRetryIdempotencyKey('web-upload', 'publication-1')).toBe(first);
  expect(upload.publicationRetryIdempotencyKey('web-upload', 'publication-2')).not.toBe(first);
});

it('does not resume a checksum-only publication whose metadata identity differs', async () => {
  const checksum = `sha256:${'a'.repeat(64)}`;
  const scope = 'groundwater:demo:alice';
  const metadata = {
    checksum,
    name: '新名称.csv',
    format: 'csv',
    mediaType: 'text/csv',
    description: '新说明',
  };
  const identity = await upload.publicationIdempotencyKey(metadata, scope);
  const receipt = {
    publicationId: 'publication-new',
    state: 'PUBLISHED' as const,
    outputs: [{ name: 'file', datasetId: 'dataset-1', versionId: 'version-1', semanticId: 'semantics-1' }],
  };
  const oldPublication = {
    publicationId: 'publication-old', state: 'PUBLISHED', receipt,
    idempotencyKey: 'web-old-metadata', manifestChecksum: `sha256:${'b'.repeat(64)}`,
  };
  const newPublication = {
    publicationId: 'publication-new', state: 'PUBLISHED', receipt,
    idempotencyKey: identity, manifestChecksum: `sha256:${'c'.repeat(64)}`,
  };
  localStorage.setItem(
    scopedStorageKey(scope, 'upload', checksum.replace('sha256:', '')),
    oldPublication.publicationId,
  );
  const calls = vi.spyOn(apiClient, 'callApi').mockImplementation((async (
    operationId: string,
    path: string,
    body?: unknown,
  ) => {
    if (operationId === 'data_get_publication' && path.includes('publication-old')) return oldPublication;
    if (operationId === 'data_begin_publication') {
      expect(body).toMatchObject({
        idempotencyKey: identity,
        outputs: [{ title: '新名称.csv', semantics: { description: '新说明' } }],
      });
      return newPublication;
    }
    if (operationId === 'data_get_publication') return newPublication;
    if (operationId === 'data_get_version') return dataDetail({
      title: '新名称.csv',
      objects: [{
        objectId: 'object-1', size: 1, checksum, role: 'raw', format: 'csv', mediaType: 'text/csv',
      }],
    });
    throw new Error(`unexpected operation: ${operationId}`);
  }) as typeof callApi);

  const result = await upload.uploadFile(
    new File(['x'], 'file.csv', { type: 'text/csv' }),
    metadata,
    scope,
    vi.fn(),
  );

  expect(result.name).toBe('新名称.csv');
  expect(calls.mock.calls.filter(([operationId]) => operationId === 'data_begin_publication')).toHaveLength(1);
  expect(localStorage.length).toBe(0);
});

it('waits past the former publication polling budget for the server terminal state', async () => {
  vi.useFakeTimers();
  const checksum = `sha256:${'a'.repeat(64)}`;
  const metadata = {
    checksum,
    name: '监测.csv',
    format: 'csv',
    mediaType: 'text/csv',
    description: '',
  };
  const identity = await upload.publicationIdempotencyKey(metadata, 'alice');
  const receipt = {
    publicationId: 'publication-long',
    state: 'PUBLISHED' as const,
    outputs: [{ name: 'file', datasetId: 'dataset-1', versionId: 'version-1', semanticId: 'semantics-1' }],
  };
  const preparing = {
    publicationId: 'publication-long', state: 'PREPARING', receipt: null,
    idempotencyKey: identity, manifestChecksum: `sha256:${'b'.repeat(64)}`,
  };
  let polls = 0;
  vi.spyOn(apiClient, 'callApi').mockImplementation((async (operationId: string) => {
    if (operationId === 'data_begin_publication') return preparing;
    if (operationId === 'data_get_publication') {
      polls += 1;
      return polls <= 120 ? preparing : { ...preparing, state: 'PUBLISHED', receipt };
    }
    if (operationId === 'data_get_version') return dataDetail({
      title: '监测.csv',
      objects: [{
        objectId: 'object-1', size: 1, checksum, role: 'raw', format: 'csv', mediaType: 'text/csv',
      }],
    });
    throw new Error(`unexpected operation: ${operationId}`);
  }) as typeof callApi);

  const pending = upload.uploadFile(
    new File(['x'], 'file.csv', { type: 'text/csv' }),
    metadata,
    'alice',
    vi.fn(),
  );
  await vi.runAllTimersAsync();

  await expect(pending).resolves.toMatchObject({ versionId: 'version-1' });
  expect(polls).toBe(121);
});

it('compares Data checksums with or without the algorithm prefix', () => {
  const digest = 'a'.repeat(64);
  expect(upload.checksumsEqual(`sha256:${digest}`, digest)).toBe(true);
  expect(upload.checksumsEqual(digest, `sha256:${digest}`)).toBe(true);
  expect(upload.checksumsEqual(`sha256:${digest}`, 'b'.repeat(64))).toBe(false);
});

it('allows correcting an explicitly rejected upload and does not resubmit automatically', async () => {
  vi.spyOn(upload, 'checksumInWorker').mockResolvedValue('sha256:checksum');
  const post = vi.spyOn(upload, 'uploadFile').mockRejectedValue(new ApiError(422, 'RESOURCE_FORMAT_INVALID'));
  render(<UploadDialog file={new File(['x'], 'file.csv', { type: 'text/csv' })} project="demo" onClose={vi.fn()} onUploaded={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '上传并附加' }));
  await screen.findByText(/RESOURCE_FORMAT_INVALID/);
  expect(screen.getByRole('button', { name: '上传并附加' })).toBeEnabled();
  fireEvent.click(screen.getByText('CSV 表格 · 高级设置'));
  fireEvent.change(screen.getByLabelText('文件格式'), { target: { value: 'json' } });
  expect(post).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: '上传并附加' }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
});

it('keeps recognized format metadata out of the main upload flow', () => {
  render(<UploadDialog file={new File(['x'], '水位.csv', { type: 'text/csv' })} project="demo" onClose={vi.fn()} onUploaded={vi.fn()} />);
  expect(screen.getByText('CSV 表格 · 高级设置')).toBeVisible();
  expect(screen.getByLabelText('媒体类型')).not.toBeVisible();
  expect(screen.getByRole('button', { name: '上传并附加' })).toBeEnabled();
  fireEvent.click(screen.getByText('CSV 表格 · 高级设置'));
  expect(screen.getByLabelText('媒体类型')).toBeVisible();
});

it.each(['observations.unknown', 'csv'])('requires explicit metadata for an unrecognized file %s without inventing a format', filename => {
  render(<UploadDialog file={new File(['x'], filename)} project="demo" onClose={vi.fn()} onUploaded={vi.fn()} />);
  expect(screen.getByText(/无法从文件名确定格式/)).toBeVisible();
  expect(screen.getByLabelText('文件格式')).toBeVisible();
  expect(screen.getByLabelText('文件格式')).toHaveValue('');
  expect(screen.getByLabelText('媒体类型')).toHaveValue('');
  expect(screen.getByRole('button', { name: '上传并附加' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('文件格式'), { target: { value: 'csv' } });
  expect(screen.getByRole('button', { name: '上传并附加' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('媒体类型'), { target: { value: 'text/csv' } });
  expect(screen.getByRole('button', { name: '上传并附加' })).toBeEnabled();
});

it('blocks another upload and provides library reconciliation guidance when the outcome is unknown', async () => {
  vi.spyOn(upload, 'checksumInWorker').mockResolvedValue('sha256:checksum');
  const post = vi.spyOn(upload, 'uploadFile').mockRejectedValue(new ApiError(0, 'UPLOAD_UNCERTAIN'));
  render(<UploadDialog file={new File(['x'], 'file.csv', { type: 'text/csv' })} project="demo" onClose={vi.fn()} onUploaded={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '上传并附加' }));
  await screen.findByText(/系统会先查询已记录的 publication 状态/);
  expect(screen.getByRole('button', { name: '上传并附加' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '关闭上传' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '上传并附加' }));
  expect(post).toHaveBeenCalledTimes(1);
});

it('distinguishes complete file transfer from successful server storage', async () => {
  vi.spyOn(upload, 'checksumInWorker').mockResolvedValue('sha256:checksum');
  vi.spyOn(upload, 'uploadFile').mockImplementation((_file, _metadata, _scope, progress) => {
    progress(1, 1);
    return new Promise(() => {});
  });
  const onUploaded = vi.fn();
  const close = vi.fn();
  render(<UploadDialog file={new File(['x'], 'file.csv', { type: 'text/csv' })} project="demo" onClose={close} onUploaded={onUploaded} />);
  fireEvent.click(screen.getByRole('button', { name: '上传并附加' }));
  await screen.findByText('文件已传送，等待校验和保存');
  expect(screen.getByRole('progressbar', { name: '正在校验和保存' })).not.toHaveAttribute('value');
  expect(screen.queryByText('100%')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '关闭上传' })).toBeDisabled();
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
  expect(close).not.toHaveBeenCalled();
  expect(onUploaded).not.toHaveBeenCalled();
});

it('focuses the upload name, supports Escape before submitting and restores focus on close', () => {
  const opener = document.createElement('button');
  document.body.append(opener); opener.focus();
  const close = vi.fn();
  const view = render(<UploadDialog file={new File(['x'], 'file.csv')} project="demo" onClose={close} onUploaded={vi.fn()} />);
  expect(screen.getByLabelText('资料名称')).toHaveFocus();
  fireEvent.keyDown(screen.getByLabelText('资料名称'), { key: 'Escape' });
  expect(close).toHaveBeenCalledTimes(1);
  view.unmount();
  expect(opener).toHaveFocus();
  opener.remove();
});

it('opens saved attachments by name without rendering the internal identity', () => {
  const checksum = 'a'.repeat(64);
  const open = vi.fn();
  render(<MessageContent content={`附加数据：\n- 水位.csv（数据集: dataset-1；版本: exact-id；语义: semantics-1；校验和: ${checksum}）`} onResource={open} />);
  fireEvent.click(screen.getByRole('button', { name: '查看附件 水位.csv' }));
  expect(open).toHaveBeenCalledExactlyOnceWith('exact-id');
  expect(screen.queryByText(new RegExp(checksum))).not.toBeInTheDocument();
  expect(screen.queryByText('附件标识与校验信息')).not.toBeInTheDocument();
});

it('opens legacy English attachments with a prefixed checksum without leaking metadata', () => {
  const checksum = 'a'.repeat(64);
  const open = vi.fn();
  render(<MessageContent content={`附加数据：\n- 水位.csv（Dataset: dataset-1；Version: exact-id；Semantics: semantics-1；checksum: sha256:${checksum}）`} onResource={open} />);
  fireEvent.click(screen.getByRole('button', { name: '查看附件 水位.csv' }));
  expect(open).toHaveBeenCalledExactlyOnceWith('exact-id');
  expect(screen.queryByText(/Dataset: dataset-1/)).not.toBeInTheDocument();
  expect(screen.queryByText(new RegExp(checksum))).not.toBeInTheDocument();
});

it('records request timing and response request ID without any body or query', async () => {
  const body = messageBody('excluded body');
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(messageReceiptFixture({ requestId: body.requestId })), { status: 202, headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'receipt-123' } }));
  const entries = vi.fn();
  const stop = observeApi(entries);
  try {
    await callApi('kernel_create_message', '/sessions/private/messages?private=excluded', body);
    expect(entries).toHaveBeenCalledWith(expect.objectContaining({ name: 'POST /sessions/private/messages', status: 202, requestId: 'receipt-123', elapsedMs: expect.any(Number) }));
    expect(JSON.stringify(entries.mock.calls)).not.toContain('excluded');
  } finally { stop(); }
});
