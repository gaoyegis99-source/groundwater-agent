import { scopedStorageKey } from "@platform/ui-boundary";

export type ImportRecovery = { version: 1; id: string; createdAt: string };

export function clearLegacyImport(storage: Storage = sessionStorage): void {
  storage.removeItem("knowledge-import");
}

export function importRecoveryKey(productId: string, project: string, subject: string): string {
  return scopedStorageKey("knowledge-import", productId, project, subject);
}

export function readImportRecovery(storage: Storage, key: string): ImportRecovery | null {
  const raw = storage.getItem(key);
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (
      !value
      || typeof value !== "object"
      || (value as ImportRecovery).version !== 1
      || typeof (value as ImportRecovery).id !== "string"
      || typeof (value as ImportRecovery).createdAt !== "string"
      || !Number.isFinite(Date.parse((value as ImportRecovery).createdAt))
    ) throw new Error("invalid");
    return value as ImportRecovery;
  } catch {
    storage.removeItem(key);
    return null;
  }
}

export function saveImportRecovery(storage: Storage, key: string, id: string, createdAt?: string): ImportRecovery {
  const timestamp = createdAt && Number.isFinite(Date.parse(createdAt)) ? createdAt : new Date().toISOString();
  const value: ImportRecovery = { version: 1, id, createdAt: timestamp };
  storage.setItem(key, JSON.stringify(value));
  return value;
}
