export function sizeLabel(bytes: number) {
  return bytes < 1024 ? `${bytes} B` : bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(1)} KiB` : `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
}

/** A display hint only; actions must keep the complete immutable version ID. */
export function shortVersion(version: string) {
  return version.length <= 16 ? version : `${version.slice(0, 8)}…${version.slice(-4)}`;
}
