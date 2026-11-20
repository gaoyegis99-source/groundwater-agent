import { useEffect, useRef, useState } from 'react';
import { ApiError } from './api';
import type { WorkItemReceipt } from './types';

export function useWorkItemCommand<Command>(
  send: (command: Command, signal: AbortSignal) => Promise<WorkItemReceipt>,
  onReceipt: (receipt: WorkItemReceipt, command: Command) => void,
  options: {
    restored?: Command | null;
    remember?: (command: Command | null) => void;
    onRejected?: (error: unknown) => void;
  } = {},
) {
  const pending = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [unsettled, setUnsettled] = useState<Command | null>(options.restored ?? null);

  useEffect(() => () => {
    pending.current?.abort();
    pending.current = null;
  }, []);

  async function submit(proposed: Command) {
    if (pending.current) return;
    const command = unsettled ?? proposed;
    const controller = new AbortController();
    pending.current = controller;
    options.remember?.(command);
    setUnsettled(command);
    setBusy(true);
    setError(null);
    let receipt: WorkItemReceipt | undefined;
    try {
      receipt = await send(command, controller.signal);
    } catch (caught) {
      if (controller.signal.aborted) return;
      const uncertain = caught instanceof ApiError && (
        caught.code === 'NETWORK_UNCERTAIN' || caught.status === 429 || caught.status >= 500
        || (caught.status >= 200 && caught.status < 300)
      );
      if (!uncertain) {
        setUnsettled(null);
        options.remember?.(null);
        options.onRejected?.(caught);
      }
      setError(caught);
    } finally {
      if (pending.current === controller) {
        pending.current = null;
        setBusy(false);
      }
    }
    if (receipt && !controller.signal.aborted) {
      setUnsettled(null);
      options.remember?.(null);
      onReceipt(receipt, command);
    }
  }

  return { submit, unsettled, busy, error, clearError: () => setError(null) };
}
