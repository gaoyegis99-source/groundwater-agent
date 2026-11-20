import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from './api';
import { readWorkItemSnapshot } from './work-item-api';
import type { WorkItemLifecycleSnapshot, WorkItemSummary } from './types';

export function workItemAccessLost(error: unknown) {
  return error instanceof ApiError && ([401, 403, 404].includes(error.status) || error.code === 'API_RESPONSE_INVALID');
}

type Page<T, Cursor> = { items: T[]; nextAfter: Cursor | null };

export function useWorkItemPages<T, Cursor extends string | number>(
  getPage: (after: Cursor | undefined, signal: AbortSignal) => Promise<Page<T, Cursor>>,
  identity: (item: T) => string | number,
) {
  const [page, setPage] = useState<Page<T, Cursor>>({ items: [], nextAfter: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const failedAfter = useRef<Cursor | undefined>(undefined);
  const pending = useRef<AbortController | null>(null);

  const load = useCallback(async (after?: Cursor) => {
    if (pending.current !== null) return;
    const controller = new AbortController();
    pending.current = controller;
    setLoading(true);
    setError(null);
    try {
      const incoming = await getPage(after, controller.signal);
      if (controller.signal.aborted) return;
      setPage(previous => {
        const items = new Map((after === undefined ? [] : previous.items).map(item => [identity(item), item]));
        for (const item of incoming.items) items.set(identity(item), item);
        return { items: [...items.values()], nextAfter: incoming.nextAfter === after ? null : incoming.nextAfter };
      });
    } catch (caught) {
      if (controller.signal.aborted) return;
      if (workItemAccessLost(caught)) setPage({ items: [], nextAfter: null });
      failedAfter.current = after;
      setError(caught);
    } finally {
      if (pending.current === controller) {
        pending.current = null;
        setLoading(false);
      }
    }
  }, [getPage, identity]);

  useEffect(() => {
    void load();
    return () => {
      pending.current?.abort();
      pending.current = null;
    };
  }, [load]);

  return {
    ...page, loading, error,
    reload: () => void load(),
    retry: () => void load(failedAfter.current),
    more: () => { if (page.nextAfter !== null) void load(page.nextAfter); },
  };
}

type SnapshotRead = { item: WorkItemSummary; snapshot?: WorkItemLifecycleSnapshot; error?: unknown };

export function useWorkItemRowStates(items: WorkItemSummary[], project: string) {
  const known = useRef(new Map<string, SnapshotRead>());
  const [states, setStates] = useState<Map<string, SnapshotRead>>(known.current);

  useEffect(() => {
    const controller = new AbortController();
    const pending = items.filter(item => known.current.get(item.workItemId)?.item !== item);
    const live = new Set(items.map(item => item.workItemId));
    for (const id of known.current.keys()) {
      if (!live.has(id)) known.current.delete(id);
    }
    let index = 0;

    async function consume() {
      while (!controller.signal.aborted && index < pending.length) {
        const item = pending[index++];
        let state: SnapshotRead;
        try {
          const snapshot = await readWorkItemSnapshot(item.workItemId, project, controller.signal);
          state = { item, snapshot };
        } catch (error) {
          state = { item, error };
        }
        if (controller.signal.aborted) return;
        known.current.set(item.workItemId, state);
        setStates(new Map(known.current));
      }
    }

    for (let worker = 0; worker < Math.min(4, pending.length); worker++) void consume();
    return () => controller.abort();
  }, [items, project]);

  return states;
}
