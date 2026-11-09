/// <reference lib="webworker" />
import { sha256 } from "@noble/hashes/sha2.js";

type HashRequest = { file: File };

self.onmessage = async (event: MessageEvent<HashRequest>) => {
  try {
    const hash = sha256.create();
    let completed = 0;
    for await (const chunk of event.data.file.stream() as unknown as AsyncIterable<Uint8Array>) {
      hash.update(chunk);
      completed += chunk.byteLength;
      self.postMessage({ type: "progress", completed, total: event.data.file.size });
    }
    const digest = Array.from(hash.digest()).map(value => value.toString(16).padStart(2, "0")).join("");
    self.postMessage({ type: "result", digest });
  } catch (error) {
    self.postMessage({ type: "error", message: error instanceof Error ? error.message : "HASH_WORKER_FAILED" });
  }
};

export {};
