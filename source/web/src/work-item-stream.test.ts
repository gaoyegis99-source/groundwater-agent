import { afterEach, describe, expect, it, vi } from 'vitest';

import { subscribeWorkItem } from './work-item-stream';
import type { WorkItemLifecycleSnapshot } from './types';

const { readSnapshot, decodeActivity } = vi.hoisted(() => ({
  readSnapshot: vi.fn(),
  decodeActivity: vi.fn(async (data: string) => JSON.parse(data)),
}));
vi.mock('./work-item-api', async importOriginal => ({
  ...await importOriginal<typeof import('./work-item-api')>(),
  readWorkItemSnapshot: readSnapshot,
  decodeWorkItemActivity: decodeActivity,
}));

class TestEventSource extends EventTarget {
  static connections: TestEventSource[] = [];
  static readonly CLOSED = 2;
  readyState = 0;
  closed = false;

  constructor(readonly url: string) {
    super();
    TestEventSource.connections.push(this);
  }

  close() { this.closed = true; this.readyState = TestEventSource.CLOSED; }

  activity(sequence: number, eventId = String(sequence)) {
    this.dispatchEvent(new MessageEvent('work_item.activity', {
      lastEventId: eventId,
      data: JSON.stringify({ kind: 'SESSION', sequence, createdAt: '2026-01-01', sessionId: 'session', eventType: 'task.revised' }),
    }));
  }

  signal(name: string) { this.dispatchEvent(new Event(name)); }
}

function snapshot(eventCursor: number): WorkItemLifecycleSnapshot {
  return { workItemId: 'work/item', revision: 1, eventCursor, state: 'DRAFT', reason: 'NO_BUSINESS_TARGET', tracks: [] };
}

function pendingSnapshot() {
  let resolve!: (value: WorkItemLifecycleSnapshot) => void;
  const result = new Promise<WorkItemLifecycleSnapshot>(done => { resolve = done; });
  readSnapshot.mockReturnValueOnce(result);
  return resolve;
}

afterEach(() => {
  TestEventSource.connections = [];
  readSnapshot.mockReset();
  decodeActivity.mockClear();
  vi.unstubAllGlobals();
});

describe('WorkItem snapshot subscription', () => {
  it('retries a snapshot overtaken by an event without showing stale state', async () => {
    vi.stubGlobal('EventSource', TestEventSource);
    readSnapshot.mockResolvedValueOnce(snapshot(4));
    const missed = pendingSnapshot();
    readSnapshot.mockResolvedValueOnce(snapshot(7));
    const show = vi.fn();
    const stop = subscribeWorkItem('work/item', 'lab & team', { onSnapshot: show });
    try {
      await vi.waitFor(() => expect(TestEventSource.connections).toHaveLength(1));
      const [stream] = TestEventSource.connections;
      expect(stream.url).toBe('/api/v12/work-items/work%2Fitem/events?projectId=lab+%26+team&afterEventId=4');
      stream.activity(6);
      await vi.waitFor(() => expect(readSnapshot).toHaveBeenCalledTimes(2));
      stream.activity(7);
      stream.activity(6);
      stream.activity(7);
      missed(snapshot(6));
      await vi.waitFor(() => expect(show.mock.calls.map(([value]) => value.eventCursor)).toEqual([4, 7]));
      expect(readSnapshot).toHaveBeenCalledTimes(3);
    } finally { stop(); }
  });

  it('does not schedule another read when the in-flight snapshot already covers the latest event', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('EventSource', TestEventSource);
    readSnapshot.mockResolvedValueOnce(snapshot(4));
    const caughtUp = pendingSnapshot();
    const show = vi.fn();
    const stop = subscribeWorkItem('work/item', 'lab', { onSnapshot: show });
    try {
      await vi.advanceTimersByTimeAsync(0);
      const [stream] = TestEventSource.connections;
      stream.activity(5);
      await vi.advanceTimersByTimeAsync(200);
      expect(readSnapshot).toHaveBeenCalledTimes(2);
      stream.activity(6);
      await Promise.resolve();
      caughtUp(snapshot(6));
      await vi.advanceTimersByTimeAsync(500);
      expect(show.mock.calls.map(([value]) => value.eventCursor)).toEqual([4, 6]);
      expect(readSnapshot).toHaveBeenCalledTimes(2);
    } finally { stop(); vi.useRealTimers(); }
  });

  it('invalidates on reconnect, focus and refresh without requiring a durable event', async () => {
    vi.stubGlobal('EventSource', TestEventSource);
    readSnapshot.mockResolvedValue(snapshot(8));
    const show = vi.fn();
    const stop = subscribeWorkItem('work/item', 'lab', { onSnapshot: show });
    try {
      await vi.waitFor(() => expect(TestEventSource.connections).toHaveLength(1));
      const [stream] = TestEventSource.connections;
      stream.signal('open');
      expect(readSnapshot).toHaveBeenCalledTimes(1);
      stream.signal('error');
      stream.signal('open');
      await vi.waitFor(() => expect(show).toHaveBeenCalledTimes(2));
      stream.signal('work_item.refresh');
      await vi.waitFor(() => expect(show).toHaveBeenCalledTimes(3));
      window.dispatchEvent(new Event('focus'));
      await vi.waitFor(() => expect(show).toHaveBeenCalledTimes(4));
    } finally { stop(); }
  });

  it('reopens from the latest snapshot when EventSource fails without a native retry', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('EventSource', TestEventSource);
    readSnapshot.mockResolvedValueOnce(snapshot(4)).mockResolvedValueOnce(snapshot(5));
    const stop = subscribeWorkItem('work/item', 'lab', { onSnapshot: vi.fn() });
    try {
      await vi.advanceTimersByTimeAsync(0);
      const [old] = TestEventSource.connections;
      old.signal('open');
      old.readyState = TestEventSource.CLOSED;
      old.signal('error');
      await vi.advanceTimersByTimeAsync(5_000);
      expect(TestEventSource.connections).toHaveLength(2);
      expect(TestEventSource.connections[1].url).toContain('afterEventId=5');
    } finally { stop(); vi.useRealTimers(); }
  });

  it('rebuilds after a cursor reset and discards late results from the closed stream', async () => {
    vi.stubGlobal('EventSource', TestEventSource);
    readSnapshot.mockResolvedValueOnce(snapshot(9));
    const stale = pendingSnapshot();
    readSnapshot.mockResolvedValueOnce(snapshot(2));
    const show = vi.fn();
    const stop = subscribeWorkItem('work/item', 'lab', { onSnapshot: show });
    try {
      await vi.waitFor(() => expect(TestEventSource.connections).toHaveLength(1));
      const [old] = TestEventSource.connections;
      old.activity(10);
      await vi.waitFor(() => expect(readSnapshot).toHaveBeenCalledTimes(2));
      const previousSignal = readSnapshot.mock.calls[1][2] as AbortSignal;
      old.signal('work_item.reset');
      expect(old.closed).toBe(true);
      expect(previousSignal.aborted).toBe(true);
      await vi.waitFor(() => expect(TestEventSource.connections).toHaveLength(2));
      expect(TestEventSource.connections[1].url).toContain('afterEventId=2');
      old.activity(99);
      old.signal('work_item.reset');
      stale(snapshot(11));
      await Promise.resolve();
      expect(show.mock.calls.map(([value]) => value.eventCursor)).toEqual([9, 2]);
      expect(readSnapshot).toHaveBeenCalledTimes(3);
    } finally { stop(); }
  });

  it('checks for reset when a previous snapshot had advanced beyond the stream cursor', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('EventSource', TestEventSource);
    readSnapshot.mockResolvedValueOnce(snapshot(3)).mockResolvedValueOnce(snapshot(5)).mockResolvedValue(snapshot(4));
    const show = vi.fn();
    const stop = subscribeWorkItem('work/item', 'lab', { onSnapshot: show });
    try {
      await vi.advanceTimersByTimeAsync(0);
      const [old] = TestEventSource.connections;
      window.dispatchEvent(new Event('focus'));
      await vi.advanceTimersByTimeAsync(100);
      expect(show).toHaveBeenLastCalledWith(snapshot(5));
      expect(old.url).toContain('afterEventId=3');

      window.dispatchEvent(new Event('focus'));
      await vi.advanceTimersByTimeAsync(100);
      expect(show).toHaveBeenLastCalledWith(snapshot(5));
      expect(old.closed).toBe(true);
      const [, probe] = TestEventSource.connections;
      expect(probe.url).toContain('afterEventId=5');
      probe.signal('work_item.reset');
      await vi.advanceTimersByTimeAsync(0);
      expect(show).toHaveBeenLastCalledWith(snapshot(4));
      expect(TestEventSource.connections[2].url).toContain('afterEventId=4');
    } finally { stop(); vi.useRealTimers(); }
  });

  it('reopens a closed stream to recover when the database is restored behind its starting cursor', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('EventSource', TestEventSource);
    readSnapshot.mockResolvedValueOnce(snapshot(9)).mockResolvedValue(snapshot(4));
    const show = vi.fn();
    const stop = subscribeWorkItem('work/item', 'lab', { onSnapshot: show });
    try {
      await vi.advanceTimersByTimeAsync(0);
      const [old] = TestEventSource.connections;
      expect(old.url).toContain('afterEventId=9');
      old.signal('open');
      old.close();
      old.signal('error');
      await vi.advanceTimersByTimeAsync(200);
      expect(readSnapshot).toHaveBeenCalledTimes(2);
      expect(show.mock.calls.map(([value]) => value.eventCursor)).toEqual([9]);
      expect(TestEventSource.connections).toHaveLength(2);
      const [, probe] = TestEventSource.connections;
      expect(probe.url).toContain('afterEventId=9');

      probe.signal('work_item.reset');
      await vi.advanceTimersByTimeAsync(1_000);
      expect(probe.closed).toBe(true);
      expect(TestEventSource.connections[2].url).toContain('afterEventId=4');
      expect(show.mock.calls.map(([value]) => value.eventCursor)).toEqual([9, 4]);
      expect(readSnapshot).toHaveBeenCalledTimes(3);
    } finally { stop(); vi.useRealTimers(); }
  });

  it('refreshes after a mismatched or invalid SSE ID without using it as the cursor', async () => {
    vi.stubGlobal('EventSource', TestEventSource);
    readSnapshot.mockResolvedValueOnce(snapshot(2)).mockResolvedValueOnce(snapshot(3)).mockResolvedValueOnce(snapshot(4));
    const show = vi.fn();
    const stop = subscribeWorkItem('work/item', 'lab', { onSnapshot: show });
    try {
      await vi.waitFor(() => expect(TestEventSource.connections).toHaveLength(1));
      const [stream] = TestEventSource.connections;
      stream.activity(3, '999');
      await vi.waitFor(() => expect(show).toHaveBeenLastCalledWith(snapshot(3)));
      stream.activity(4, '4.0');
      await vi.waitFor(() => expect(show).toHaveBeenLastCalledWith(snapshot(4)));
      expect(readSnapshot).toHaveBeenCalledTimes(3);
    } finally { stop(); }
  });
});
