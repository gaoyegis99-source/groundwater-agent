import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ArtifactViewer } from './ArtifactViewer';
import { dataDetail } from './test-fixtures';

const receipt = dataDetail({ objects: [{ objectId: 'receipt', role: 'raw', format: 'json', mediaType: 'application/json', size: 100, checksum: 'a'.repeat(64) }] });

afterEach(() => vi.unstubAllGlobals());

it('opens a receipt on request and returns to metadata without losing its identity', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ operation: 'sustainability.evaluate', status: 'SUCCEEDED' })));
  vi.stubGlobal('fetch', fetch);
  const view = render(<ArtifactViewer resource={receipt} project="groundwater-demo" preferredViewer="metadata" />);
  expect(await screen.findByText('产品建议首先查看元数据。')).toBeVisible();
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '查看 JSON 内容' }));
  expect(await screen.findByText('"sustainability.evaluate"')).toBeVisible();
  expect(screen.getByText(receipt.versionId)).toBeVisible();
  expect(fetch.mock.calls[0][0]).toContain(`/versions/${receipt.versionId}/objects/receipt/content`);
  fireEvent.click(screen.getByRole('button', { name: '返回元数据' }));
  expect(await screen.findByText('产品建议首先查看元数据。')).toBeVisible();
  expect(screen.queryByText('"sustainability.evaluate"')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看 JSON 内容' }));
  view.rerender(<ArtifactViewer resource={{ ...receipt, versionId: 'another-version' }} project="groundwater-demo" preferredViewer="metadata" />);
  expect(await screen.findByRole('button', { name: '查看 JSON 内容' })).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByText('"sustainability.evaluate"')).not.toBeInTheDocument();
});

it.each([
  { ...receipt.objects[0], size: 32 * 1024 ** 2 + 1 },
  { ...receipt.objects[0], format: 'pytorch-state-dict', mediaType: 'application/octet-stream' },
])('keeps oversized JSON and model weights in metadata view', async object => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  render(<ArtifactViewer resource={{ ...receipt, objects: [object] }} project="groundwater-demo" preferredViewer="metadata" />);
  expect(await screen.findByRole('link', { name: '下载原文件' })).toBeVisible();
  expect(screen.queryByRole('button', { name: '查看 JSON 内容' })).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

it('does not show the previous JSON under a newly selected data version while loading', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ operation: 'first-operation' })))
    .mockImplementationOnce(() => new Promise(() => {}));
  vi.stubGlobal('fetch', fetch);
  const view = render(<ArtifactViewer resource={receipt} project="groundwater-demo" />);
  expect(await screen.findByText('"first-operation"')).toBeVisible();
  view.rerender(<ArtifactViewer resource={{ ...receipt, versionId: 'second-version' }} project="groundwater-demo" />);
  expect(screen.getByText('second-version')).toBeVisible();
  expect(screen.queryByText('"first-operation"')).not.toBeInTheDocument();
  expect(await screen.findByText('正在读取…')).toBeVisible();
});
