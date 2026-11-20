import { ApiError, projectQuery } from './api';
import { decodeWorkItemActivity, readWorkItemSnapshot, workItemPath } from './work-item-api';
import type { WorkItemLifecycleSnapshot } from './types';

type WorkItemStreamHandlers = {
  onSnapshot: (snapshot: WorkItemLifecycleSnapshot) => void;
  onError?: (error: unknown) => void;
};

export function subscribeWorkItem(workItemId: string, project: string, handlers: WorkItemStreamHandlers): () => void {
  let view = openView(reset);

  function reset() {
    view.close();
    view = openView(reset);
  }

  function onFocus() { view.refresh(); }
  window.addEventListener('focus', onFocus);
  return () => {
    window.removeEventListener('focus', onFocus);
    view.close();
  };

  function openView(onReset: () => void) {
    const controller = new AbortController();
    let source: EventSource | undefined;
    let cursor = 0;
    let snapshotCursor = 0;
    let streamStartCursor = 0;
    let reading = false;
    let refreshRequested = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let retryDelay = 100;

    function backOff() { retryDelay = Math.min(retryDelay * 2, 5000); }

    function scheduleRead() {
      if (controller.signal.aborted || reading || timer !== undefined) return;
      timer = setTimeout(() => {
        timer = undefined;
        void read();
      }, retryDelay);
    }

    function refresh() {
      refreshRequested = true;
      scheduleRead();
    }

    function connect() {
      streamStartCursor = cursor;
      const query = projectQuery(project, { afterEventId: streamStartCursor });
      const stream = new EventSource(`/api/v12${workItemPath(workItemId)}/events?${query}`);
      source = stream;
      const current = () => !controller.signal.aborted && source === stream;
      let opened = false;
      stream.addEventListener('open', () => {
        if (!current()) return;
        retryDelay = 100;
        if (opened) refresh();
        opened = true;
      });
      stream.addEventListener('error', () => {
        if (!current() || stream.readyState !== EventSource.CLOSED) return;
        backOff();
        refresh();
      });
      stream.addEventListener('work_item.refresh', () => { if (current()) refresh(); });
      stream.addEventListener('work_item.reset', () => { if (current()) onReset(); });
      stream.addEventListener('work_item.activity', async (event: MessageEvent<string>) => {
        if (!current()) return;
        const id = Number(event.lastEventId);
        if (!/^[1-9]\d*$/.test(event.lastEventId) || !Number.isSafeInteger(id)) { refresh(); return; }
        if (id <= cursor) return;
        try {
          const entry = await decodeWorkItemActivity(event.data);
          if (!current() || id <= cursor) return;
          if (entry.sequence === id) {
            cursor = id;
            scheduleRead();
            return;
          }
        } catch {
          // A malformed frame may still change the browser's resume cursor.
          // Fetch the authority before using any of its payload.
        }
        if (current()) refresh();
      });
    }

    async function read() {
      refreshRequested = false;
      reading = true;
      let retry = false;
      try {
        const snapshot = await readWorkItemSnapshot(workItemId, project, controller.signal);
        if (controller.signal.aborted) return;
        const needsStream = !source || source.readyState === EventSource.CLOSED;
        if (snapshot.eventCursor < cursor) {
          // Ask for a reset if the last stream closed or its starting cursor
          // is too old to detect the restore.
          if (needsStream || (snapshot.eventCursor < snapshotCursor && streamStartCursor < snapshotCursor)) {
            source?.close();
            connect();
          }
          retry = true;
          return;
        }
        snapshotCursor = snapshot.eventCursor;
        cursor = snapshotCursor;
        if (source?.readyState !== EventSource.CLOSED) retryDelay = 100;
        if (needsStream) connect();
        handlers.onSnapshot(snapshot);
      } catch (error) {
        if (controller.signal.aborted) return;
        handlers.onError?.(error);
        if (error instanceof ApiError && (error.status === 0 || error.status === 429 || error.status >= 500)) {
          backOff();
          retry = true;
        }
      } finally {
        reading = false;
        if (retry || refreshRequested) scheduleRead();
      }
    }

    void read();
    return {
      refresh,
      close() {
        controller.abort();
        source?.close();
        if (timer !== undefined) clearTimeout(timer);
      },
    };
  }
}
