import { PlatformUiError } from "./api";

export function retryable(failureCount: number, error: unknown): boolean {
  return error instanceof PlatformUiError
    && failureCount < 3
    && (error.status === 0 || error.status === 429 || error.status === 503);
}

export function retryDelay(attempt: number, error: unknown): number {
  if (error instanceof PlatformUiError && error.retryAfterMs !== null) {
    return Math.min(error.retryAfterMs, 30_000);
  }
  return Math.min(1_000 * (2 ** attempt), 10_000);
}

export function deadlineExceeded(createdAt: string, deadlineSeconds: number): boolean {
  return Date.now() > Date.parse(createdAt) + deadlineSeconds * 1000;
}
