import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { DataVersionQuality } from './DataVersionQuality';
import type { components } from './generated/public-api-v12';

type QualityRun = components['schemas']['data__QualityRunResponse'];
type QualityPage = components['schemas']['data__QualityRunPage'];

const version = { datasetId: 'dataset-1', versionId: 'version-1', semanticId: 'semantics-frozen' };
const endpoint = '/api/v12/data/datasets/dataset-1/quality-runs';
const createdAt = '2026-11-18T04:12:00Z';

function run(overrides: Partial<QualityRun> = {}): QualityRun {
  return {
    ...version,
    qualityRunId: 'quality-run-1',
    suiteVersionId: 'suite-version-1',
    bindingRevision: 1,
    operatorRunId: 'operator-run-1',
    operatorId: 'table.validate',
    operatorVersion: '1.0.0',
    implementationDigest: 'digest-1',
    status: 'FAILED',
    createdAt,
    actor: {},
    summary: {},
    results: [{ ruleId: 'water-level-range', severity: 'ERROR', status: 'FAIL', violationCount: 7, code: null, evidence: {} }],
    ...overrides,
  };
}

function page(items: QualityRun[], nextCursor: string | null = null) {
  return Response.json({ items, nextCursor } satisfies QualityPage);
}

it('shows the newest run matching all three identities and its Data rule results', async () => {
  const response = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    expect(url.pathname).toBe(endpoint);
    switch (url.searchParams.get('cursor')) {
      case null: return page([
        run({ datasetId: 'dataset-2', status: 'PASSED', createdAt: '2026-11-20T04:12:00Z' }),
        run({ versionId: 'version-2', status: 'PASSED', createdAt: '2026-11-19T04:12:00Z' }),
        run({ semanticId: 'semantics-current', status: 'PASSED', createdAt: '2026-11-18T05:12:00Z' }),
      ], '3');
      case '3': return page([
        run(),
        run({ qualityRunId: 'older-exact-run', status: 'PASSED', createdAt: '2026-11-17T04:12:00Z' }),
      ], '5');
      default: throw new Error('The search should stop at the newest exact run');
    }
  });
  vi.stubGlobal('fetch', response);
  render(<DataVersionQuality version={version} />);

  expect(await screen.findByText('未通过')).toBeVisible();
  const quality = screen.getByRole('region', { name: '质量检查' });
  expect(within(quality).getByText(/检查时间/).querySelector('time')).toHaveAttribute('datetime', createdAt);
  expect(within(quality).getByText('quality-run-1')).not.toBeVisible();
  fireEvent.click(within(quality).getByText('运行与规则详情'));
  expect(within(quality).getByText('quality-run-1')).toBeVisible();
  expect(within(quality).getByText('suite-version-1')).toBeVisible();
  expect(within(quality).queryByText('older-exact-run')).not.toBeInTheDocument();
  const rule = within(quality).getByRole('row', { name: /water-level-range/ });
  expect(within(rule).getByText('ERROR')).toBeVisible();
  expect(within(rule).getByText('FAIL')).toBeVisible();
  expect(within(rule).getByText('7')).toBeVisible();
  expect(response).toHaveBeenCalledTimes(2);
});

it('keeps a version without an exact quality run unverified', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => page([
    run({ semanticId: 'semantics-current', qualityRunId: 'different-semantic-run', status: 'PASSED' }),
  ])));
  render(<DataVersionQuality version={version} />);

  expect(await screen.findByText('待核验')).toBeVisible();
  expect(screen.queryByText(/^(通过|PASS(?:ED)?)$/)).not.toBeInTheDocument();
  expect(screen.queryByText('different-semantic-run')).not.toBeInTheDocument();
});

it('bounds automatic history reads and continues the exact search only on request', async () => {
  const response = vi.fn()
    .mockResolvedValueOnce(page([], '20'))
    .mockResolvedValueOnce(page([run({ semanticId: 'current', status: 'PASSED' })], '40'))
    .mockResolvedValueOnce(page([run({ status: 'FAILED' })]));
  vi.stubGlobal('fetch', response);
  render(<DataVersionQuality version={version} />);
  const more = await screen.findByRole('button', { name: '查找更早的质量记录' });
  expect(response).toHaveBeenCalledTimes(2);
  expect(screen.getByText('待核验')).toBeVisible();
  expect(screen.queryByText('通过')).not.toBeInTheDocument();
  fireEvent.click(more);
  expect(await screen.findByText('未通过')).toBeVisible();
  expect(String(response.mock.calls[2][0])).toContain('cursor=40');
});

it('keeps an unknown result unverified even beside an older passing run', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({
    items: [{ ...run(), status: 'UNKNOWN' }, run({ status: 'PASSED', createdAt: '2026-11-17T04:12:00Z' })],
    nextCursor: null,
  })));
  render(<DataVersionQuality version={version} />);

  expect(await screen.findByRole('alert')).toHaveTextContent('质量状态待核验');
  expect(screen.queryByText(/^(通过|PASS(?:ED)?)$/)).not.toBeInTheDocument();
});

it.each([401, 403, 404])('keeps inaccessible quality records private after HTTP %s', async status => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({
    status, title: 'Hidden dataset', detail: 'Private rule or evidence', code: 'FORBIDDEN',
  }, { status })));
  render(<DataVersionQuality version={version} />);

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('当前无法访问质量检查记录');
  expect(alert).not.toHaveTextContent(/hidden dataset|private rule|forbidden/i);
  expect(screen.queryByText('通过')).not.toBeInTheDocument();
});

it('lets the user retry a failed page from the newest records', async () => {
  const response = vi.fn()
    .mockResolvedValueOnce(page([], '2'))
    .mockResolvedValueOnce(Response.json({ status: 503, code: 'DATA_UNAVAILABLE' }, { status: 503 }))
    .mockResolvedValueOnce(page([run({
      status: 'PASSED_WITH_WARNINGS',
      results: [{ ruleId: 'water-level-range', severity: 'WARN', status: 'FAIL', violationCount: 7, code: null, evidence: {} }],
    })]));
  vi.stubGlobal('fetch', response);
  render(<DataVersionQuality version={version} />);

  expect(screen.getByRole('status')).toHaveTextContent('正在读取质量检查');
  expect(await screen.findByRole('alert')).toHaveTextContent('质量检查服务暂不可用');
  fireEvent.click(screen.getByRole('button', { name: '重新读取质量检查' }));
  expect(await screen.findByText('通过（有警告）')).toBeVisible();
  const cursors = response.mock.calls.map(([url]) => new URL(String(url), 'http://localhost').searchParams.get('cursor'));
  expect(cursors).toEqual([null, '2', null]);
});

it('aborts the prior request and rejects its late result when the semantic identity changes', async () => {
  const next = { ...version, semanticId: 'semantics-new' };
  let finishPrevious: (value: Response) => void = () => { throw new Error('Previous request not started'); };
  const previous = new Promise<Response>(resolve => { finishPrevious = resolve; });
  const response = vi.fn()
    .mockReturnValueOnce(previous)
    .mockResolvedValueOnce(page([run(next)]));
  vi.stubGlobal('fetch', response);
  const view = render(<DataVersionQuality version={version} />);
  await vi.waitFor(() => expect(response).toHaveBeenCalledTimes(1));

  view.rerender(<DataVersionQuality version={next} />);
  expect(response.mock.calls[0][1].signal.aborted).toBe(true);
  expect(await screen.findByText('未通过')).toBeVisible();
  await act(async () => finishPrevious(page([run({
    qualityRunId: 'late-passing-run', status: 'PASSED',
  })])));
  expect(screen.getByText('未通过')).toBeVisible();
  expect(screen.queryByText('通过')).not.toBeInTheDocument();
  expect(screen.queryByText('late-passing-run')).not.toBeInTheDocument();
});
