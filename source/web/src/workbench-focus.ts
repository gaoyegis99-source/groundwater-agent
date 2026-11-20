import { useEffect, type KeyboardEvent, type RefObject } from 'react';

/** Focus order must follow the currently visible pane, including closed details. */
export function workbenchFocusTargets(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>('button,[href],input,select,textarea,summary,[tabindex]')].filter(node => {
    if (node.hasAttribute('disabled') || node.tabIndex < 0) return false;
    for (let parent: HTMLElement | null = node; parent; parent = parent.parentElement) {
      const style = window.getComputedStyle(parent);
      if (parent.hidden || parent.inert || style.display === 'none' || style.visibility === 'hidden') return false;
      if (parent instanceof HTMLDetailsElement && !parent.open && !parent.querySelector(':scope > summary')?.contains(node)) return false;
      if (parent === root) break;
    }
    return true;
  });
}

export function containTabFocus(event: Pick<KeyboardEvent, 'key' | 'shiftKey' | 'preventDefault'>, root: HTMLElement) {
  if (event.key !== 'Tab') return;
  const targets = workbenchFocusTargets(root);
  const focused = document.activeElement;
  const first = targets[0];
  const last = targets.at(-1);
  const outsideOrder = !targets.some(target => target === focused);
  if (!first || (event.shiftKey ? focused === first || outsideOrder : focused === last || outsideOrder)) {
    event.preventDefault();
    (event.shiftKey ? last ?? root : first ?? root).focus();
  }
}

export function useWorkbenchModal(
  ref: RefObject<HTMLElement | null>,
  open: boolean,
  initialSelector?: string,
) {
  useEffect(() => {
    const root = ref.current;
    if (!open || !root) return;
    const opener = document.activeElement;
    const shell = root.closest('.app-shell, .work-item-shell');
    const backgrounds: HTMLElement[] = [];
    for (let pane: HTMLElement | null = root; shell && pane && pane !== shell; pane = pane.parentElement) {
      for (const sibling of pane.parentElement?.children ?? []) {
        if (sibling instanceof HTMLElement && sibling !== pane && !sibling.hasAttribute('data-overlay-dismiss') && !sibling.inert) {
          backgrounds.push(sibling);
        }
      }
    }
    backgrounds.forEach(child => { child.inert = true; });

    const initial = initialSelector ? root.querySelector<HTMLElement>(initialSelector) : null;
    (initial ?? root).focus({ preventScroll: true });

    const keepFocusOnResize = () => {
      const target = document.activeElement;
      if (!(target instanceof HTMLElement) || !root.contains(target) || !target.getClientRects().length) {
        root.focus({ preventScroll: true });
      }
    };
    const resumeKeyboardFocus = (event: globalThis.KeyboardEvent) => {
      if (!event.defaultPrevented && document.activeElement === document.body && root.getClientRects().length) containTabFocus(event, root);
    };
    document.addEventListener('keydown', resumeKeyboardFocus);
    window.addEventListener('resize', keepFocusOnResize);
    return () => {
      document.removeEventListener('keydown', resumeKeyboardFocus);
      window.removeEventListener('resize', keepFocusOnResize);
      backgrounds.forEach(child => { child.inert = false; });
      if (opener instanceof HTMLElement && opener.isConnected && !root.contains(opener)) opener.focus({ preventScroll: true });
    };
  }, [ref, open, initialSelector]);
}
