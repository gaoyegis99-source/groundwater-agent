import { afterEach, expect, it, vi } from 'vitest';
import { loginResearch, logoutResearch, researchCsrfToken } from './api';

const key = 'platform.v12.auth.csrf';
const currentUser = { subject: 'researcher', displayName: '研究员', roles: ['researcher'] };

afterEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

it('shares the write token with another tab that uses the same login cookie', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(
    JSON.stringify({ currentUser, csrfToken: 'issued-token' }),
    { headers: { 'Content-Type': 'application/json' } },
  )));
  await loginResearch('researcher', 'fixture-password');

  window.sessionStorage.clear();
  expect(researchCsrfToken()).toBe('issued-token');
});

it('does not revive an old tab token after signing out in another tab', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 204 })));
  window.localStorage.setItem(key, 'current-token');
  window.sessionStorage.setItem(key, 'old-token');

  await logoutResearch();

  window.sessionStorage.setItem(key, 'old-token');
  expect(researchCsrfToken()).toBe('');
});
