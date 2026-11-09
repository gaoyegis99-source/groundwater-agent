// @vitest-environment node
import { expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import { HASH_CHUNK_BYTES, hashFile } from './hash';

it('hashes across the 4 MiB boundary without buffering the full file', async () => {
  const bytes = new Uint8Array(HASH_CHUNK_BYTES + 127).fill(97);
  const blob = new Blob([bytes]);
  const progress: number[] = [];
  expect(await hashFile(blob, processed => progress.push(processed))).toBe(`sha256:${createHash('sha256').update(bytes).digest('hex')}`);
  expect(progress).toEqual([HASH_CHUNK_BYTES, bytes.length]);
});
it('hashes an empty file', async () => {
  expect(await hashFile(new Blob([]), () => {})).toBe('sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
});
