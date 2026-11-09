export function identityStorageScope(productId: string, projectId: string, subject: string) {
  if (!productId || !projectId || !subject) return '';
  return [productId, projectId, subject].map(encodeURIComponent).join(':');
}

export function scopedStorageKey(scope: string, kind: string, id = '') {
  const namespace = scope || 'unbound';
  return `platform-web-v12:${namespace}:${kind}${id ? `:${encodeURIComponent(id)}` : ''}`;
}
