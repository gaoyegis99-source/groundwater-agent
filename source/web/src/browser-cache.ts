export function readJsonCache<T>(key: string, validate: (value: unknown) => value is T): T | null {
  const raw = sessionStorage.getItem(key);
  if (raw === null) return null;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    sessionStorage.removeItem(key);
    return null;
  }
  if (!validate(value)) {
    sessionStorage.removeItem(key);
    return null;
  }
  return value;
}

export function readEnumCache<T extends string>(
  key: string,
  allowed: readonly T[],
  fallback: T,
): T {
  const value = sessionStorage.getItem(key);
  if (value === null) return fallback;
  if ((allowed as readonly string[]).includes(value)) return value as T;
  sessionStorage.removeItem(key);
  return fallback;
}

export function readNumberCache(
  key: string,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  const raw = sessionStorage.getItem(key);
  if (raw === null) return fallback;
  const value = Number(raw);
  if (Number.isFinite(value) && value >= minimum && value <= maximum) return value;
  sessionStorage.removeItem(key);
  return fallback;
}

export function readFlagCache(key: string): boolean {
  const value = sessionStorage.getItem(key);
  if (value === null || value === '0') return false;
  if (value === '1') return true;
  sessionStorage.removeItem(key);
  return false;
}
