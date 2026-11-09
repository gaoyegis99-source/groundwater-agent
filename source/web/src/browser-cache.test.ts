import { beforeEach, expect, it } from 'vitest';
import { readEnumCache, readFlagCache, readJsonCache, readNumberCache } from './browser-cache';

beforeEach(() => sessionStorage.clear());

it('removes only malformed JSON and invalid shapes', () => {
  sessionStorage.setItem('broken-json', '{');
  sessionStorage.setItem('broken-shape', JSON.stringify({ value: 1 }));
  sessionStorage.setItem('healthy', JSON.stringify({ value: 'kept' }));
  const valid = (value: unknown): value is { value: string } =>
    value !== null && typeof value === 'object'
      && typeof (value as { value?: unknown }).value === 'string';

  expect(readJsonCache('broken-json', valid)).toBeNull();
  expect(readJsonCache('broken-shape', valid)).toBeNull();
  expect(readJsonCache('healthy', valid)).toEqual({ value: 'kept' });
  expect(sessionStorage.getItem('broken-json')).toBeNull();
  expect(sessionStorage.getItem('broken-shape')).toBeNull();
  expect(sessionStorage.getItem('healthy')).not.toBeNull();
});

it('restores safe layout defaults and deletes invalid scalar values', () => {
  sessionStorage.setItem('tab', 'unknown');
  sessionStorage.setItem('width-text', 'wide');
  sessionStorage.setItem('width-small', '120');
  sessionStorage.setItem('flag', 'yes');

  expect(readEnumCache('tab', ['tasks', 'results'] as const, 'tasks')).toBe('tasks');
  expect(readNumberCache('width-text', 420, 360, 760)).toBe(420);
  expect(readNumberCache('width-small', 420, 360, 760)).toBe(420);
  expect(readFlagCache('flag')).toBe(false);
  for (const key of ['tab', 'width-text', 'width-small', 'flag']) {
    expect(sessionStorage.getItem(key)).toBeNull();
  }
});
