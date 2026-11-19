import { Icon } from './Icon';
import { shortVersion } from './format';
import type { ResourceItem } from './types';
import { workflowFileKey } from './workflow-selection';

export function PendingAttachments({ items, onOpen, onRemove }: {
  items: readonly ResourceItem[];
  onOpen: (versionId: string, item: ResourceItem) => void;
  onRemove: (item: ResourceItem) => void;
}) {
  if (!items.length) return null;
  return <section className="pending-attachments" data-testid="pending-attachments" aria-label="待发送的附件">
    <p className="attachment-heading">待发送 · {items.length} 份资料</p>
    <ul className="attachment-list">{items.map(item => <li className="attachment-chip" key={workflowFileKey(item)}>
      <button type="button" className="text-button" title={`${item.name} · 查看资料内容与版本`} onClick={() => onOpen(item.versionId, item)}>
        <Icon name="file" />
        <span><strong>{item.name}</strong>{' '}<small>{item.format.toUpperCase()} · 版本 {shortVersion(item.versionId)}</small></span>
      </button>
      <button type="button" aria-label={`移除附件 ${item.name}`} onClick={() => onRemove(item)}><Icon name="close" /></button>
    </li>)}</ul>
    <details className="attachment-identities technical-details"><summary>附件版本详情</summary>
      {items.map(item => <div key={workflowFileKey(item)}><strong>{item.name}</strong><dl className="argument-list">
        <div><dt>数据版本</dt><dd>{item.versionId}</dd></div><div><dt>数据集</dt><dd>{item.datasetId}</dd></div>
        <div><dt>语义版本</dt><dd>{item.semanticId}</dd></div><div><dt>数据对象</dt><dd>{item.objectId}</dd></div>
        <div><dt>SHA-256</dt><dd>{item.checksum}</dd></div>
      </dl></div>)}
    </details>
  </section>;
}
