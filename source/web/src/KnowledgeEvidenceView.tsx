import type { KnowledgeEvidence, KnowledgeReference } from './types';

const evidenceStatusLabels: Record<string, string> = {
  OK: '有效',
  STALE: '已失效',
  WITHDRAWN: '已撤回',
  EXPIRED: '已过期',
  NOT_FOUND: '未找到',
  NEEDS_EXPANSION: '上下文未完整读取',
};

const evidenceScopeLabels: Record<string, string> = {
  GENERAL: '通用知识',
  DATA_BOUND: '数据绑定知识',
};

const bindingMatchLabels: Record<string, string> = {
  GENERAL: '通用匹配',
  EXACT: '精确匹配',
  NOT_CHECKED: '未核对',
  MISMATCH: '不匹配',
};

const locatorLabels: Record<string, string> = {
  page: '页码',
  section: '章节',
  paragraph: '段落',
  clause: '条款',
  table: '表格',
  figure: '图',
  sheet: '工作表',
  row: '行',
};

const sourceKindLabels: Record<string, string> = {
  OFFICIAL_STANDARD: '官方标准',
  OFFICIAL_GUIDANCE: '官方指南',
  OFFICIAL_POLICY: '官方政策',
  DOMAIN_DEFINITION: '领域定义',
  DEMO_ASSUMPTION: '演示假设',
  PROJECT_TEMPLATE: '项目模板',
};

function localizedLabel(value: string | null | undefined, labels: Record<string, string>) {
  return value ? labels[value] ?? value : '';
}

function displayDateTime(value: string | null | undefined) {
  if (!value) return '';
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? '' : new Date(timestamp).toLocaleString();
}

function exactReference(item: KnowledgeEvidence, reference: KnowledgeReference | undefined) {
  return Boolean(reference
    && item.versionId === reference.versionId
    && item.unitId === reference.unitId
    && item.checksum === reference.checksum);
}

function invalidMessage(status: KnowledgeEvidence['status'], hasContent: boolean) {
  if (status === 'OK' && !hasContent) return '证据内容没有完整返回，不能作为当前回答依据。';
  const messages: Partial<Record<KnowledgeEvidence['status'], string>> = {
    STALE: '该引用已失效，不能作为当前回答依据。',
    WITHDRAWN: '该证据已撤回，不能作为当前回答依据。',
    EXPIRED: '该证据已过期，不能作为当前回答依据。',
    NOT_FOUND: '没有找到该证据，不能作为当前回答依据。',
    NEEDS_EXPANSION: '所需上下文尚未完整读取，不能作为当前回答依据。',
  };
  return messages[status] ?? '该证据当前不可引用。';
}

function Locator({ locator }: { locator: NonNullable<KnowledgeEvidence['locator']> }) {
  return <p className="evidence-locator"><strong>来源定位</strong>{Object.entries(locator).map(([key, value]) => (
    <span key={key}>{localizedLabel(key, locatorLabels)} {value}</span>
  ))}</p>;
}

function Bindings({ item, project }: { item: KnowledgeEvidence; project: string }) {
  if (!item.bindings?.length) return null;
  const verifiedAt = displayDateTime(item.bindingsVerifiedAt);
  return <section className="evidence-bindings" aria-label="精确数据绑定">
    <div className="section-title"><div><span className="drawer-eyebrow">DATA BINDING</span><h4>不可变数据绑定</h4></div><span>{localizedLabel(item.bindingMatch, bindingMatchLabels)}</span></div>
    <p className="muted">以下为证据声明的 project / dataset / version / semantic 身份；匹配结论以本轮服务端核对状态为准。</p>
    {item.bindings.map((binding, index) => <dl className="argument-list evidence-binding" key={`${binding.projectId}:${binding.datasetId}:${binding.versionId}:${binding.semanticId}:${binding.fieldId ?? index}`}>
      <div><dt>项目</dt><dd><code>{binding.projectId}</code>{binding.projectId !== project && <small>（不同于当前项目）</small>}</dd></div>
      <div><dt>数据集</dt><dd><code>{binding.datasetId}</code></dd></div>
      <div><dt>数据版本</dt><dd><code>{binding.versionId}</code></dd></div>
      <div><dt>语义版本</dt><dd><code>{binding.semanticId}</code></dd></div>
      {binding.fieldId && <div><dt>字段</dt><dd><code>{binding.fieldId}</code></dd></div>}
    </dl>)}
    {verifiedAt && <p className="muted">绑定核对时间：{verifiedAt}</p>}
  </section>;
}

function Sources({ sources }: { sources: NonNullable<KnowledgeEvidence['sources']> }) {
  if (!sources.length) return null;
  return <section className="evidence-sources" aria-label="原始来源">
    <div className="section-title"><div><span className="drawer-eyebrow">SOURCES</span><h4>原始来源</h4></div><span>{sources.length} 项</span></div>
    <ul className="source-list">{sources.map(source => {
      const facts = [
        source.publisher,
        source.version ? `版本 ${source.version}` : '',
        source.publishedAt ? `发布 ${source.publishedAt}` : '',
        source.accessedAt ? `访问 ${source.accessedAt}` : '',
        `建议复核 ${source.reviewAfter}`,
      ].filter(Boolean).join(' · ');
      return <li className="source-card" key={source.sourceId}>
        <div className="source-card-heading"><span className="source-kind">{localizedLabel(source.kind, sourceKindLabels)}</span><span className={`status-badge ${source.lifecycleStatus === 'ACTIVE' ? 'status-active' : 'status-invalid'}`}>{source.lifecycleStatus === 'ACTIVE' ? '有效来源' : '来源已撤回'}</span></div>
        <p>{source.url ? <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> : source.title}</p>
        <p className="muted">{facts}</p>
      </li>;
    })}</ul>
  </section>;
}

function EvidenceArticle({ item, primary, project, hasReference }: {
  item: KnowledgeEvidence;
  primary: boolean;
  project: string;
  hasReference: boolean;
}) {
  const content = typeof item.content === 'string' ? item.content.trim() : '';
  const usable = item.status === 'OK' && Boolean(content);
  const statusLabel = item.status === 'OK' && !content
    ? '内容缺失'
    : localizedLabel(item.status, evidenceStatusLabels);
  const relation = hasReference ? (primary ? '引用片段原文' : '必需上下文') : '知识片段';
  const title = item.documentTitle?.trim() || '知识文档';
  return <article className={`knowledge-body evidence-article ${usable ? '' : 'evidence-unavailable'}`} tabIndex={-1} data-primary={primary} aria-label={`${relation}：${title}`}>
    <header className="detail-hero">
      <div className="detail-kicker">
        <span className={`status-badge ${usable ? 'status-active' : 'status-invalid'}`}>{usable ? evidenceStatusLabels.OK : statusLabel}</span>
        <span>{relation}</span>
        <span>{[localizedLabel(item.scope, evidenceScopeLabels), localizedLabel(item.bindingMatch, bindingMatchLabels)].filter(Boolean).join(' · ')}</span>
      </div>
      <h3>{title}</h3>
    </header>
    {usable
      ? <>
        <p className="prose knowledge-content">{content}</p>
        {item.locator && Object.keys(item.locator).length > 0 && <Locator locator={item.locator} />}
        <Bindings item={item} project={project} />
        <Sources sources={item.sources ?? []} />
      </>
      : <div className="stale-notice" role="status"><strong>{invalidMessage(item.status, Boolean(content))}</strong><p>请返回知识目录重新检索或选择最新版本。</p></div>}
    <details className="technical-details evidence-identity"><summary>技术身份</summary>
      <dl className="argument-list">
        <div><dt>知识版本</dt><dd><code>{item.versionId}</code></dd></div>
        <div><dt>证据单元</dt><dd><code>{item.unitId}</code></dd></div>
        <div><dt>内容校验值</dt><dd><code>{item.checksum}</code></dd></div>
      </dl>
    </details>
  </article>;
}

export function KnowledgeEvidenceView({ items, reference, project }: {
  items: KnowledgeEvidence[];
  reference?: KnowledgeReference;
  project: string;
}) {
  const ordered = reference
    ? [...items.filter(item => exactReference(item, reference)), ...items.filter(item => !exactReference(item, reference))]
    : items;
  return <div className="library-list knowledge-evidence-list">{ordered.map(item => (
    <EvidenceArticle
      item={item}
      primary={exactReference(item, reference)}
      project={project}
      hasReference={Boolean(reference)}
      key={`${item.versionId}:${item.unitId}:${item.checksum}`}
    />
  ))}</div>;
}
