import { sha256 } from "@noble/hashes/sha2.js";

export async function fileSha256(
  file: File,
  onProgress: (completed: number, total: number) => void,
  signal?: AbortSignal,
): Promise<string> {
  const hash = sha256.create();
  const reader = file.stream().getReader();
  let completed = 0;
  const abort = () => { void reader.cancel().catch(() => undefined); };
  signal?.addEventListener("abort", abort, { once: true });
  try {
    signal?.throwIfAborted();
    while (true) {
      const { done, value } = await reader.read();
      signal?.throwIfAborted();
      if (done) break;
      hash.update(value);
      completed += value.byteLength;
      onProgress(completed, file.size);
    }
    return Array.from(hash.digest(), value => value.toString(16).padStart(2, "0")).join("");
  } finally {
    signal?.removeEventListener("abort", abort);
    reader.releaseLock();
  }
}
