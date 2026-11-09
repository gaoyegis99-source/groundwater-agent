import { expect, it } from 'vitest';
import appSource from './App.tsx?raw';

it('keeps transport and browser persistence out of the application shell', () => {
  expect(appSource).not.toMatch(/\brequest\s*\(/);
  expect(appSource).not.toContain('new EventSource');
  expect(appSource).not.toContain('localStorage');
  expect(appSource).not.toContain('sessionStorage');
  expect(appSource).toContain('useProjectSessions');
  expect(appSource).toContain('useSessionStream');
  expect(appSource).toContain('useMessageActions');
});
