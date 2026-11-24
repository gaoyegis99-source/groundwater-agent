import { beforeEach, expect, it, vi } from 'vitest';
import { callApi, readDataVersion } from './api';
import { dataDetail } from './test-fixtures';
import { readWorkflowInputCandidates } from './workflow-input-candidates';

vi.mock('./api', async importOriginal => ({
  ...await importOriginal<typeof import('./api')>(),
  callApi: vi.fn(),
  readDataVersion: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

it.each([
  ['versionId', 'versionId'],
  ['workbook', 'workbookVersionId'],
])('matches the %s case role to %s', async (role, input) => {
  const detail = dataDetail({ currentVersion: 'version-1' });
  const raw = detail.objects.find(object => object.role === 'raw')!;
  vi.mocked(callApi).mockResolvedValue({
    items: [{ datasetId: detail.datasetId, currentVersion: detail.versionId, tags: ['case-batch:batch-1', `input:${role}`] }],
    nextCursor: null,
  });
  vi.mocked(readDataVersion).mockResolvedValue(detail);

  const candidates = await readWorkflowInputCandidates('case:example', {
    [input]: { label: '资料', description: '指定版本', formats: [raw.format] },
  }, new AbortController().signal);

  expect(candidates[input]).toMatchObject({ datasetId: detail.datasetId, versionId: detail.versionId, objectId: raw.objectId });
});
