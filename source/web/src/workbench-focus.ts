/** Focus order must follow the currently visible pane, including closed details. */
export function workbenchFocusTargets(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>('button,[href],input,select,textarea,summary,[tabindex]')].filter(node => {
    if (node.hasAttribute('disabled') || node.tabIndex < 0) return false;
    for (let parent: HTMLElement | null = node; parent; parent = parent.parentElement) {
      const style = window.getComputedStyle(parent);
      if (parent.hidden || style.display === 'none' || style.visibility === 'hidden') return false;
      if (parent instanceof HTMLDetailsElement && !parent.open && !parent.querySelector(':scope > summary')?.contains(node)) return false;
      if (parent === root) break;
    }
    return true;
  });
}
