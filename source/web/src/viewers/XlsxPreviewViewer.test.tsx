import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import { dataDetail } from '../test-fixtures';
import { XlsxPreviewViewer } from './XlsxPreviewViewer';

const start = vi.fn();
const wait = vi.fn();
const cancel = vi.fn();

vi.mock('../api', async importOriginal => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    startDataPreview: (...args: unknown[]) => start(...args),
    waitForDataPreview: (...args: unknown[]) => wait(...args),
    cancelDataPreview: (...args: unknown[]) => cancel(...args),
  };
});

const resource = dataDetail({
  objects: [{
    objectId: 'xlsx-1', size: 1024, checksum: 'a'.repeat(64), role: 'raw',
    format: 'xlsx', mediaType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  }],
});

beforeEach(() => {
  start.mockReset(); wait.mockReset(); cancel.mockReset();
  start.mockResolvedValue({ previewId: 'preview-1' });
  cancel.mockResolvedValue({ previewId: 'preview-1', state: 'CANCELLED' });
});

it('shows the selected worksheet, typed cells, and truncation warning', async () => {
  wait.mockResolvedValue({
    previewId: 'preview-1', state: 'SUCCEEDED', error: null, receipt: {}, resultAvailable: true, expiresAt: null,
    result: {
      kind: 'xlsx_table',
      metadata: { sheets: [{ name: '观测', state: 'visible', maxRow: 1201, maxColumn: 3 }], selectedSheet: '观测', effectiveRange: 'A1:C1001', sourceRows: 1200 },
      fields: [{ fieldId: 'c0', name: '井号', type: 'string' }, { fieldId: 'c1', name: '水位', type: 'number' }],
      rows: [['W01', 12.5]], features: [], truncated: true,
      warnings: ['仅显示前 1,000 行；原表共有 1,200 行。'], nextOffset: null,
    },
  });
  render(<XlsxPreviewViewer resource={resource} object={resource.objects[0]} url="/download.xlsx" />);
  expect(await screen.findByText('W01')).toBeVisible();
  expect(screen.getByText('仅显示前 1,000 行；原表共有 1,200 行。')).toBeVisible();
  expect(screen.getByLabelText('工作表')).toHaveValue('观测');
  expect(start).toHaveBeenCalledWith(expect.objectContaining({ versionId: 'version-1', semanticId: 'semantics-1', objectId: 'xlsx-1' }), expect.any(AbortSignal));
});

it('keeps the exact-object download available when preview parsing fails', async () => {
  wait.mockRejectedValue(new ApiError(422, 'PREVIEW_FILE_INVALID'));
  render(<XlsxPreviewViewer resource={resource} object={resource.objects[0]} url="/download.xlsx" />);
  expect(await screen.findByRole('alert')).toHaveTextContent('文件结构无法安全解析');
  const download = screen.getByRole('link', { name: '下载原文件' });
  expect(download).toHaveAttribute('href', '/download.xlsx');
  await waitFor(() => expect(screen.queryByText('正在安全读取工作簿…')).not.toBeInTheDocument());
});
