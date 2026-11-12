import { afterEach, expect, it, vi } from 'vitest';
import { runDataQuery, waitForDataPreview } from './api';

const jsonResponse = (value: unknown, status = 200) => new Response(
  JSON.stringify(value),
  { status, headers: { 'Content-Type': 'application/json' } },
);

afterEach(() => {
  vi.unstubAllGlobals();
});

function runPollTimersImmediately() {
  vi.spyOn(window, 'setTimeout').mockImplementation(((handler: TimerHandler) => {
    queueMicrotask(() => {
      if (typeof handler === 'function') handler();
    });
    return 1;
  }) as typeof window.setTimeout);
}

it('waits for a query terminal state beyond the former client polling budget', async () => {
  runPollTimersImmediately();
  let polls = 0;
  vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === 'POST') {
      return jsonResponse({ operationId: 'operation-1', queryId: 'query-1' }, 202);
    }
    polls += 1;
    if (polls <= 240) {
      return jsonResponse({
        queryId: 'query-1', state: 'RUNNING', error: null, expiresAt: null,
        resultAvailable: false, receipt: null, result: null,
      });
    }
    const digest = `sha256:${'a'.repeat(64)}`;
    return jsonResponse({
      queryId: 'query-1', state: 'SUCCEEDED', error: null, expiresAt: null,
      resultAvailable: true,
      receipt: {
        queryId: 'query-1', inputs: [], plan: {}, planDigest: digest, coverage: {},
        truncated: false, returnedRows: 0, resultChecksum: digest, engine: {},
      },
      result: { fields: [], rows: [], nextOffset: null, truncated: false, coverage: {}, engine: {} },
    });
  }));

  const result = await runDataQuery(
    { versionId: 'version-1', semanticId: 'semantic-1' },
    0,
    100,
  );

  expect(result).toMatchObject({ state: 'SUCCEEDED' });
  expect(polls).toBe(241);
});

it('cancels the accepted Data query when the caller aborts polling', async () => {
  const controller = new AbortController();
  const cancel = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith('/cancel')) {
      cancel(init?.method, init?.signal);
      return jsonResponse({ queryId: 'query-1', state: 'CANCELLED' });
    }
    if (init?.method === 'POST') {
      return jsonResponse({ operationId: 'operation-1', queryId: 'query-1' }, 202);
    }
    controller.abort();
    throw controller.signal.reason;
  }));

  await expect(runDataQuery(
    { versionId: 'version-1', semanticId: 'semantic-1' }, 0, 100, controller.signal,
  )).rejects.toMatchObject({ name: 'AbortError' });

  await vi.waitFor(() => expect(cancel).toHaveBeenCalledExactlyOnceWith('POST', undefined));
});

it('waits for a preview terminal state beyond the former client polling budget', async () => {
  runPollTimersImmediately();
  let polls = 0;
  vi.stubGlobal('fetch', vi.fn(async () => {
    polls += 1;
    if (polls <= 240) {
      return jsonResponse({
        previewId: 'preview-1', state: 'RUNNING', error: null, expiresAt: null,
        resultAvailable: false, receipt: null, result: null,
      });
    }
    return jsonResponse({
      previewId: 'preview-1', state: 'SUCCEEDED', error: null, expiresAt: null,
      resultAvailable: true, receipt: {},
      result: {
        kind: 'xlsx_table', metadata: {}, fields: [], rows: [], features: [],
        truncated: false, warnings: [], nextOffset: null,
      },
    });
  }));

  const result = await waitForDataPreview('preview-1', 0, 100);

  expect(result).toMatchObject({ state: 'SUCCEEDED' });
  expect(polls).toBe(241);
});

it('recovers a lost submission receipt with the same payload before polling', async () => {
  const bodyAttempts: unknown[] = [];
  const requested: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    requested.push(path);
    if (init?.method === 'POST') {
      bodyAttempts.push(JSON.parse(String(init.body)));
      if (bodyAttempts.length === 1) throw new TypeError('connection lost after commit');
      return jsonResponse({ operationId: 'query-1', queryId: 'query-1' }, 202);
    }
    return jsonResponse({
      queryId: 'query-1', state: 'CANCELLED', error: null, expiresAt: null,
      resultAvailable: false, receipt: null, result: null,
    });
  }));

  await expect(runDataQuery({ versionId: 'v1', semanticId: 's1' }, 10, 20))
    .rejects.toMatchObject({ code: 'QUERY_CANCELLED' });
  expect(bodyAttempts).toHaveLength(2);
  expect(bodyAttempts[0]).toEqual(bodyAttempts[1]);
  expect(requested[2]).toBe('/api/v12/data/queries/query-1?offset=10&limit=20');
});

it('cancels the same Data query when the view closes before a lost receipt is recovered', async () => {
  const controller = new AbortController();
  const bodies: unknown[] = [];
  const cancel = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith('/cancel')) {
      cancel(path);
      return jsonResponse({ queryId: 'query-1', state: 'CANCELLED' });
    }
    expect(path).toBe('/api/v12/data/queries');
    bodies.push(JSON.parse(String(init?.body)));
    if (bodies.length === 1) {
      controller.abort();
      throw new TypeError('receipt lost');
    }
    return jsonResponse({ operationId: 'query-1', queryId: 'query-1' }, 202);
  }));

  await expect(runDataQuery({ versionId: 'v1', semanticId: 's1' }, 0, 50, controller.signal))
    .rejects.toMatchObject({ name: 'AbortError' });
  await vi.waitFor(() => expect(cancel).toHaveBeenCalledExactlyOnceWith('/api/v12/data/queries/query-1/cancel'));
  expect(bodies).toHaveLength(2);
  expect(bodies[0]).toEqual(bodies[1]);
});

it('does not submit a query if the caller already aborted', async () => {
  const fetch = vi.fn(async () => jsonResponse({}, 500));
  vi.stubGlobal('fetch', fetch);
  await expect(runDataQuery({ versionId: 'v1', semanticId: 's1' }, 0, 50, AbortSignal.abort()))
    .rejects.toMatchObject({ name: 'AbortError' });
  expect(fetch).not.toHaveBeenCalled();
});

it('does not retry a query rejected by the server', async () => {
  const fetch = vi.fn(async () => jsonResponse({ code: 'SEMANTIC_VERSION_MISMATCH' }, 409));
  vi.stubGlobal('fetch', fetch);
  await expect(runDataQuery({ versionId: 'v1', semanticId: 's1' }, 0, 50))
    .rejects.toMatchObject({ code: 'SEMANTIC_VERSION_MISMATCH' });
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('reports uncertainty after one recovery attempt without creating another logical query', async () => {
  const fetch = vi.fn(async () => { throw new TypeError('connection unavailable'); });
  vi.stubGlobal('fetch', fetch);

  await expect(runDataQuery({ versionId: 'v1', semanticId: 's1' }, 0, 50))
    .rejects.toMatchObject({ code: 'QUERY_SUBMISSION_UNCERTAIN' });
  expect(fetch).toHaveBeenCalledTimes(2);
});
