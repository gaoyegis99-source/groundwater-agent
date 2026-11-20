import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { scopedStorageKey } from './browser-scope';
import { inputReferences } from './message-routing';
import { capabilitiesFixture, historyFixture, instant, messageReceiptFixture, sessionResultsFixture } from './test-fixtures';
import type { BusinessAssetReference, WorkItemDetail, WorkItemSessionContext } from './types';
import { WorkItemResultReuse } from './WorkItemResultReuse';

type ResultReference = Extract<BusinessAssetReference, { kind: 'RESULT' }>;
const scope = 'groundwater:demo:test-user';
const sourceHash = '#/work-items/work-1';
const draftKey = (name: string, sessionId = 'session-target', identity = scope) => scopedStorageKey(identity, name, sessionId);
const reference: ResultReference = {
  kind: 'RESULT', runId: 'run-1', outputName: 'surface', publicationId: 'pub-1',
  dataRef: {
    kind: 'data_version', service: 'data-primary', datasetId: 'dataset-1', versionId: 'version-1',
    semanticId: 'semantic-1', objectId: 'object-1', outputName: 'surface',
    format: 'geotiff', mediaType: 'image/tiff', sizeBytes: 120, checksum: 'sha256:' + 'a'.repeat(64),
  },
};
const asset = { linkId: 'result-link-1', reference, state: 'AVAILABLE' as const, linkedAt: instant };
const detail: WorkItemDetail = {
  workItem: {
    workItemId: 'work-1', projectId: 'demo', ownerSubject: 'test-user', title: '枯水期监测',
    description: null, status: 'ACTIVE', revision: 2, createdAt: instant, updatedAt: instant, archivedAt: null,
  },
  sessions: [
    { sessionId: 'session-source', title: '源分析', linkedAt: instant, archivedAt: null },
    { sessionId: 'session-target', title: '复核会话', linkedAt: instant, archivedAt: null },
    { sessionId: 'session-old', title: '已归档会话', linkedAt: instant, archivedAt: instant },
  ],
  workflowRuns: [{ runId: 'run-1', agentRunId: 'agent-1', sessionId: 'session-source', workflowRef: {
    id: 'workflow-1', version: '1', checksum: 'sha256:' + 'b'.repeat(64),
  } }],
  assets: [asset], objects: [], tasks: [], taskRuns: [], agentRuns: [],
};
const targetContext: WorkItemSessionContext = {
  workItem: detail.workItem, sessionId: 'session-target', linkedAt: instant,
  assets: [asset], objects: [], approvedActions: [], openTasks: [],
};
const otherAttachment = { ...reference.dataRef, objectId: 'other-object', name: '原有资料', familyId: 'dataset-1' };
const props = { detail, scope, sourceSessionId: 'session-source', assetLinkId: asset.linkId, reference, disabled: false };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}
function useAuthority({
  current = detail, context = targetContext, detailResponse,
}: {
  current?: WorkItemDetail;
  context?: WorkItemSessionContext;
  detailResponse?: () => Promise<Response>;
} = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url, window.location.origin);
    expect(init?.method).toBe('GET');
    expect(path.searchParams.get('projectId')).toBe('demo');
    if (path.pathname === '/api/v12/work-items/work-1') return detailResponse?.() ?? json(current);
    if (path.pathname === '/api/v12/sessions/session-target/work-item') return json({ context });
    throw new Error(`Unexpected request ${url}`);
  }));
}
function startReuse() {
  fireEvent.change(screen.getByRole('combobox', { name: '复用目标会话' }), { target: { value: 'session-target' } });
  fireEvent.click(screen.getByRole('button', { name: '添加到会话草稿' }));
}

beforeEach(() => {
  window.history.replaceState(null, '', `/${sourceHash}`);
  sessionStorage.clear();
});
it('adds the exact named result to a chosen session without altering its existing draft and bindings', async () => {
  useAuthority();
  sessionStorage.setItem(draftKey('draft'), '已有未发送的分析说明');
  sessionStorage.setItem(draftKey('draft-attachments'), JSON.stringify([otherAttachment]));
  const bindings = JSON.stringify({ bindings: { comparison: 'key-for-other-object' } });
  sessionStorage.setItem(draftKey('draft-workflow'), bindings);
  render(<WorkItemResultReuse {...props} />);
  const picker = screen.getByRole('combobox', { name: '复用目标会话' });
  expect(within(picker).getAllByRole('option').map(option => (option as HTMLOptionElement).value)).toEqual(['', 'session-target']);
  expect(screen.getByRole('button', { name: '添加到会话草稿' })).toBeDisabled();
  startReuse();

  await waitFor(() => expect(window.location.hash).toBe('#/sessions/session-target?workItem=work-1'));
  expect(sessionStorage.getItem(draftKey('draft'))).toBe('已有未发送的分析说明');
  expect(sessionStorage.getItem(draftKey('draft-workflow'))).toBe(bindings);
  expect(sessionStorage.getItem(draftKey('draft-attachments', 'session-source'))).toBeNull();
  const attached = JSON.parse(sessionStorage.getItem(draftKey('draft-attachments'))!);
  expect(attached).toHaveLength(2);
  expect(attached[0]).toEqual(otherAttachment);
  expect(attached[1].resultProvenance).toEqual({
    workItemId: 'work-1', sourceSessionId: 'session-source', runId: 'run-1',
    outputName: 'surface', publicationId: 'pub-1',
  });
  expect(inputReferences(attached)[1]).toEqual({
    kind: 'data_version', service: 'data-primary', datasetId: 'dataset-1', versionId: 'version-1',
    semanticId: 'semantic-1', objectId: 'object-1', outputName: 'surface',
    format: 'geotiff', mediaType: 'image/tiff', sizeBytes: 120, checksum: 'sha256:' + 'a'.repeat(64),
  });
});

it.each([
  ['unavailable result', { ...detail, assets: [{ ...asset, state: 'UNAVAILABLE', reference: null }] }, targetContext],
  ['changed publication', { ...detail, assets: [{ ...asset, reference: { ...reference, publicationId: 'pub-other' } }] }, targetContext],
  ['changed semantic version', { ...detail, assets: [{ ...asset, reference: { ...reference,
    dataRef: { ...reference.dataRef, semanticId: 'semantic-new' } } }] }, targetContext],
  ['newly archived destination', { ...detail, sessions: detail.sessions.map(item => item.sessionId === 'session-target'
    ? { ...item, archivedAt: instant } : item) }, targetContext],
  ['destination in another work item', detail, { ...targetContext, workItem: { ...detail.workItem, workItemId: 'work-other' } }],
  ['destination belonging to another subject', detail, { ...targetContext, workItem: { ...detail.workItem, ownerSubject: 'different-user' } }],
] as const)('leaves the destination draft untouched for %s', async (_case, current, context) => {
  useAuthority({ current, context });
  render(<WorkItemResultReuse {...props} />);
  startReuse();

  await screen.findByRole('alert');
  expect(window.location.hash).toBe(sourceHash);
  expect(sessionStorage.getItem(draftKey('draft-attachments'))).toBeNull();
});

it('keeps a conflicting attached semantic revision and its original order', async () => {
  useAuthority();
  const cachedAttachments = JSON.stringify([otherAttachment, {
    ...reference.dataRef, semanticId: 'semantic-old', name: '之前绑定的成果', familyId: 'dataset-1',
  }]);
  sessionStorage.setItem(draftKey('draft-attachments'), cachedAttachments);
  render(<WorkItemResultReuse {...props} />);
  startReuse();

  await screen.findByRole('alert');
  expect(window.location.hash).toBe(sourceHash);
  expect(sessionStorage.getItem(draftKey('draft-attachments'))).toBe(cachedAttachments);
});

it('adds the output identity to an identical unnamed attachment in place', async () => {
  useAuthority();
  const unnamed = { ...reference.dataRef, outputName: undefined, name: '原有附件', familyId: 'dataset-1' };
  sessionStorage.setItem(draftKey('draft-attachments'), JSON.stringify([unnamed, otherAttachment]));
  render(<WorkItemResultReuse {...props} />);
  startReuse();

  await waitFor(() => expect(window.location.hash).toBe('#/sessions/session-target?workItem=work-1'));
  const items = JSON.parse(sessionStorage.getItem(draftKey('draft-attachments'))!);
  expect(items).toHaveLength(2);
  expect(items[0]).toMatchObject({ name: '原有附件', outputName: 'surface', resultProvenance: { publicationId: 'pub-1' } });
  expect(items[1]).toEqual(otherAttachment);
});

it('keeps a full 20-attachment draft intact when the result needs a new slot', async () => {
  useAuthority();
  const cached = JSON.stringify(Array.from({ length: 20 }, (_, index) => ({ ...otherAttachment, objectId: `object-old-${index}` })));
  sessionStorage.setItem(draftKey('draft-attachments'), cached);
  render(<WorkItemResultReuse {...props} />);
  startReuse();

  await screen.findByRole('alert');
  expect(window.location.hash).toBe(sourceHash);
  expect(sessionStorage.getItem(draftKey('draft-attachments'))).toBe(cached);
});

it('uses the work detail when a result falls outside the bounded session context', async () => {
  useAuthority({ context: { ...targetContext, assets: [] } });
  render(<WorkItemResultReuse {...props} />);
  startReuse();

  await waitFor(() => expect(window.location.hash).toBe('#/sessions/session-target?workItem=work-1'));
  expect(JSON.parse(sessionStorage.getItem(draftKey('draft-attachments'))!)).toHaveLength(1);
});

it('leaves the existing draft intact when work access is revoked before reuse', async () => {
  useAuthority({ detailResponse: async () => json({ code: 'FORBIDDEN' }, 403) });
  const cached = JSON.stringify([otherAttachment]);
  sessionStorage.setItem(draftKey('draft-attachments'), cached);
  render(<WorkItemResultReuse {...props} />);
  startReuse();

  await screen.findByRole('alert');
  expect(window.location.hash).toBe(sourceHash);
  expect(sessionStorage.getItem(draftKey('draft-attachments'))).toBe(cached);
});

it.each(['route', 'identity'] as const)('discards an in-flight result when the work %s changes', async change => {
  let finishDetail!: (response: Response) => void;
  useAuthority({ detailResponse: () => new Promise(resolve => { finishDetail = resolve; }) });
  const view = render(<WorkItemResultReuse {...props} />);
  startReuse();
  await waitFor(() => expect(finishDetail).toBeTypeOf('function'));

  const nextScope = 'groundwater:demo:other-user';
  const nextHash = change === 'route' ? '#/work-items/work-other' : sourceHash;
  if (change === 'route') window.history.replaceState(null, '', `/${nextHash}`);
  else view.rerender(<WorkItemResultReuse {...props} scope={nextScope} />);
  await act(async () => { finishDetail(json(detail)); });

  expect(window.location.hash).toBe(nextHash);
  expect(sessionStorage.getItem(draftKey('draft-attachments'))).toBeNull();
  expect(sessionStorage.getItem(draftKey('draft-attachments', 'session-target', nextScope))).toBeNull();
});

const reused = {
  ...reference.dataRef, name: '已发布水位面', familyId: 'dataset-1',
  resultProvenance: { workItemId: 'work-1', sourceSessionId: 'session-source', runId: 'run-1',
    outputName: 'surface', publicationId: 'pub-1' },
};
const outgoing = { requestId: 'previous-request-id', message: '复核水位面', requestedMode: 'AUTO', inputReferences: inputReferences([reused]) };
const revoked = '成果附件当前无法核验';

function openDestination() {
  window.history.replaceState(null, '', '/#/sessions/session-target');
  const resultReads = vi.fn();
  const posts: unknown[] = [];
  let readable = false;
  let acceptedRequestId = '';
  let feed: FakeEvents | undefined;
  class FakeEvents extends EventTarget {
    onopen?: () => void;
    constructor() { super(); feed = this; }
    close() {}
  }
  vi.stubGlobal('EventSource', FakeEvents);
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url, window.location.origin).pathname;
    if (path.endsWith('/capabilities')) return json(capabilitiesFixture());
    if (path.endsWith('/sessions')) return json({ items: [], nextCursor: null });
    if (path.endsWith('/results')) return json(sessionResultsFixture([], { sessionId: 'session-target' }));
    if (path.endsWith('/work-item')) return json({ context: { ...targetContext, assets: readable ? [asset] : [] } });
    if (path.endsWith('/work-items/work-1')) {
      resultReads();
      return json(readable ? detail : { ...detail, assets: [{ ...asset, state: 'UNAVAILABLE', reference: null }] });
    }
    if (path.endsWith('/messages')) {
      if (init?.method === 'POST') {
        const body = JSON.parse(String(init.body));
        posts.push(body);
        return json(messageReceiptFixture({ requestId: body.requestId, sessionId: 'session-target' }));
      }
      return json(historyFixture({ sessionId: 'session-target', items: [], turns: acceptedRequestId ? [{ requestId: acceptedRequestId }] : [] }));
    }
    throw new Error(`Unexpected request ${url}`);
  }));
  return { posts, resultReads, restoreAccess: () => { readable = true; },
    accept: (requestId: string) => { acceptedRequestId = requestId; },
    connect: () => { feed?.onopen?.(); } };
}

it('keeps the draft after revocation and submits only the exact reference after access is restored', async () => {
  const authority = openDestination();
  sessionStorage.setItem(draftKey('draft'), outgoing.message);
  sessionStorage.setItem(draftKey('draft-attachments'), JSON.stringify([reused]));
  render(<App />);
  const send = await screen.findByRole('button', { name: '发送消息' });
  await waitFor(() => expect(send).toBeEnabled());
  fireEvent.click(send);

  await screen.findByText(new RegExp(revoked));
  expect(authority.posts).toHaveLength(0);
  expect(sessionStorage.getItem(draftKey('draft'))).toBe(outgoing.message);
  expect(JSON.parse(sessionStorage.getItem(draftKey('draft-attachments'))!)).toEqual([reused]);
  expect(sessionStorage.getItem(draftKey('message-request'))).toBeNull();

  authority.restoreAccess();
  await waitFor(() => expect(send).toBeEnabled());
  fireEvent.click(send);
  await waitFor(() => expect(authority.posts).toEqual([{ ...outgoing, requestId: expect.any(String) }]));
});

it('pauses an unsent queue item after revocation and only retries on request', async () => {
  const authority = openDestination();
  const queued = { id: 'queue-item', createdAt: instant, text: outgoing.message, attachments: [reused] };
  const queueKey = draftKey('message-queue');
  localStorage.setItem(queueKey, JSON.stringify([queued]));
  try {
    render(<App />);
    await screen.findByTitle('项目：demo');
    await waitFor(() => expect(screen.queryByText('正在恢复对话…')).not.toBeInTheDocument());
    act(() => authority.connect());
    await screen.findByText(new RegExp(revoked));
    expect(authority.posts).toHaveLength(0);
    expect(JSON.parse(localStorage.getItem(queueKey)!)).toEqual([{ ...queued, resultCheckBlocked: true }]);
    expect(sessionStorage.getItem(draftKey('message-request'))).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '关闭提示' }));
    expect(authority.resultReads).toHaveBeenCalledTimes(1);

    authority.restoreAccess();
    fireEvent.click(screen.getByRole('button', { name: '重新核对成果' }));
    await waitFor(() => expect(authority.posts).toHaveLength(1));
  } finally { localStorage.removeItem(queueKey); }
});

it('checks a saved retry without discarding a recoverable original receipt', async () => {
  const authority = openDestination();
  const saved = { body: outgoing, draftText: outgoing.message, attachments: [reused] };
  sessionStorage.setItem(draftKey('message-request'), JSON.stringify(saved));
  const view = render(<App />);
  const retry = await screen.findByRole('button', { name: '重试发送' });
  await waitFor(() => expect(retry).toBeEnabled());
  fireEvent.click(retry);

  await screen.findByText(new RegExp(revoked));
  expect(authority.posts).toHaveLength(0);
  expect(JSON.parse(sessionStorage.getItem(draftKey('message-request'))!)).toEqual(saved);

  authority.accept(outgoing.requestId);
  view.unmount();
  render(<App />);
  await waitFor(() => expect(sessionStorage.getItem(draftKey('message-request'))).toBeNull());
  expect(authority.posts).toHaveLength(0);
});
