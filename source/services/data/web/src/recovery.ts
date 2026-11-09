import { scopedStorageKey } from "@platform/ui-boundary";

const LEGACY_KEYS = ["data-query", "data-publication"] as const;

export type RecoveryRecord = {
  version: 1;
  id: string;
  createdAt: string;
};

export function clearLegacyRecoveryKeys(storage: Storage = localStorage): void {
  for (const key of LEGACY_KEYS) storage.removeItem(key);
}

export function recoveryKey(
  kind: "query" | "publication",
  productId: string,
  project: string,
  subject: string,
): string {
  return scopedStorageKey(`data-${kind}`, productId, project, subject);
}

export function readRecovery(storage: Storage, key: string): RecoveryRecord | null {
  const raw = storage.getItem(key);
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (
      !value
      || typeof value !== "object"
      || (value as RecoveryRecord).version !== 1
      || typeof (value as RecoveryRecord).id !== "string"
      || typeof (value as RecoveryRecord).createdAt !== "string"
      || !Number.isFinite(Date.parse((value as RecoveryRecord).createdAt))
    ) {
      storage.removeItem(key);
      return null;
    }
    return value as RecoveryRecord;
  } catch {
    storage.removeItem(key);
    return null;
  }
}

export function saveRecovery(storage: Storage, key: string, id: string, createdAt?: string): RecoveryRecord {
  const value: RecoveryRecord = {
    version: 1,
    id,
    createdAt: createdAt && Number.isFinite(Date.parse(createdAt)) ? createdAt : new Date().toISOString(),
  };
  storage.setItem(key, JSON.stringify(value));
  return value;
}
