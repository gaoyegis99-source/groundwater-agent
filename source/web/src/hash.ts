import { sha256 } from '@noble/hashes/sha2.js';
import { bytesToHex } from '@noble/hashes/utils.js';

export const HASH_CHUNK_BYTES = 4 * 1024 * 1024;
export async function hashFile(file: Blob, progress: (processed: number) => void) {
  const hash = sha256.create();
  for (let offset = 0; offset < file.size; offset += HASH_CHUNK_BYTES) {
    hash.update(new Uint8Array(await file.slice(offset, offset + HASH_CHUNK_BYTES).arrayBuffer()));
    progress(Math.min(offset + HASH_CHUNK_BYTES, file.size));
  }
  return `sha256:${bytesToHex(hash.digest())}`;
}
