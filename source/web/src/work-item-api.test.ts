import { describe, expect, it, vi } from 'vitest';

import { decodeWorkItemActivity, listWorkItemActivity, readWorkItemSnapshot, reviewWorkItem, updateWorkItem } from './work-item-api';

const activity = {
  kind: 'SESSION', sequence: 5, createdAt: '2026-11-20T00:00:00.000Z', sessionId: 'session', eventType: 'task.revised',
};

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), { headers: { 'Content-Type': 'application/json' } });
}

describe('WorkItem API transport', () => {
  it('validates individual activity frames with the published activity page schema', async () => {
    await expect(decodeWorkItemActivity(JSON.stringify(activity))).resolves.toEqual(activity);
    const review = {
      kind: 'COMMAND', sequence: 6, createdAt: activity.createdAt, command: 'REVIEW', requestId: 'request',
      actorSubject: 'researcher', revision: 2, changed: true, linkId: null, reviewSessionId: 'session',
    };
    await expect(decodeWorkItemActivity(JSON.stringify(review))).resolves.toEqual(review);
    for (const payload of ['{', JSON.stringify({ ...activity, sequence: true }), JSON.stringify({ ...activity, runId: 'unexpected' })]) {
      await expect(decodeWorkItemActivity(payload)).rejects.toMatchObject({ code: 'API_RESPONSE_INVALID' });
    }
  });

  it('validates snapshot identity and forwards paging options and the abort signal', async () => {
    const snapshot = { workItemId: 'work item', revision: 2, eventCursor: 5, state: 'READY', reason: 'TASK_READY', tracks: [] };
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(json(snapshot))
      .mockResolvedValueOnce(json({ ...snapshot, workItemId: 'different' }))
      .mockResolvedValueOnce(json({ entries: [activity], nextAfter: null }));
    const { signal } = new AbortController();
    await expect(readWorkItemSnapshot('work item', 'one & two', signal)).resolves.toEqual(snapshot);
    expect(fetch).toHaveBeenNthCalledWith(1, '/api/v12/work-items/work%20item/snapshot?projectId=one+%26+two', expect.objectContaining({ method: 'GET', signal }));
    await expect(readWorkItemSnapshot('work item', 'one & two')).rejects.toMatchObject({ code: 'API_RESPONSE_INVALID' });
    await expect(listWorkItemActivity('work item', 'one & two', { after: 0, limit: 25, signal })).resolves.toEqual({ entries: [activity], nextAfter: null });
    expect(fetch).toHaveBeenNthCalledWith(3, '/api/v12/work-items/work%20item/activity?projectId=one+%26+two&after=0&limit=25', expect.objectContaining({ method: 'GET', signal }));
  });

  it('preserves the command request identifier, revision and review token', async () => {
    const receipt = { workItemId: 'work', revision: 3, changed: true, linkId: null };
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json(receipt));
    const command = { projectId: 'p', expectedRevision: 2 };
    const update = { ...command, requestId: 'update', title: '新标题', description: null };
    await expect(updateWorkItem('work', update)).resolves.toEqual(receipt);
    expect(fetch).toHaveBeenNthCalledWith(1, '/api/v12/work-items/work/update', expect.objectContaining({ method: 'POST', body: JSON.stringify(update) }));
    const review = { ...command, requestId: 'review', sessionId: 'session', reviewToken: `sha256:${'a'.repeat(64)}` };
    await expect(reviewWorkItem('work', review)).resolves.toEqual(receipt);
    expect(fetch).toHaveBeenNthCalledWith(2, '/api/v12/work-items/work/review', expect.objectContaining({ method: 'POST', body: JSON.stringify(review) }));
  });
});
