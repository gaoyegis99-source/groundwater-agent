/// <reference lib="webworker" />
import { fileSha256 } from "./file-hash";

type HashRequest = { file: File };

self.onmessage = async (event: MessageEvent<HashRequest>) => {
  try {
    const digest = await fileSha256(event.data.file, (completed, total) => {
      self.postMessage({ type: "progress", completed, total });
    });
    self.postMessage({ type: "result", digest });
  } catch (error) {
    self.postMessage({ type: "error", message: error instanceof Error ? error.message : "HASH_WORKER_FAILED" });
  }
};

export {};
