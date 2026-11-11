import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { AssistantContent, MessageContent } from './MessageContent';
import { ErrorNotice } from './ErrorNotice';
import { ApiError, errorMessage } from './api';
import type { ReplyPresentation } from './types';

const checksum = 'a'.repeat(64);
const source = { title: '方法说明', versionId: 'knowledge-version-2', unitId: 'unit-2', checksum };
function reply(overrides: Partial<ReplyPresentation> = {}): ReplyPresentation {
  return { conclusion: { markdown: '结论：井号 **GWTEST-0A5D92EF** 当前无异常。', sourceIndexes: [0] },
    details: [], executionSummary: null, unresolvedItems: [], sources: [source], ...overrides };
}

it('shows the conclusion first and keeps deduplicated details and sources in one disclosure', () => {
  const read = vi.fn();
  render(<AssistantContent reply={reply({
    details: [
      { markdown: '可展开的详细依据。', sourceIndexes: [0] },
      { markdown: '同一来源支持的限制说明。', sourceIndexes: [0] },
    ],
  })} onKnowledge={read} onResource={vi.fn()} />);

  expect(screen.getByText('GWTEST-0A5D92EF')).toBeVisible();
  expect(screen.getByText('可展开的详细依据。')).not.toBeVisible();
  expect(screen.queryByText(checksum)).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('更多信息'));
  expect(screen.getByText('可展开的详细依据。')).toBeVisible();
  expect(screen.getAllByRole('button', { name: '打开依据：方法说明' })).toHaveLength(1);
  expect(screen.getByRole('button', { name: '打开依据：方法说明' })).not.toHaveTextContent('unit-2');
  fireEvent.click(screen.getByRole('button', { name: '打开依据：方法说明' }));
  expect(read).toHaveBeenCalledExactlyOnceWith({ versionId: source.versionId, unitId: source.unitId, checksum });
  expect(screen.queryByText('原始回复与引用标识')).not.toBeInTheDocument();
});

it('separates adjacent evidence labels in copied text', () => {
  render(<AssistantContent reply={reply({ sources: [source, { ...source, unitId: 'unit-3' }] })} onKnowledge={vi.fn()} onResource={vi.fn()} />);
  fireEvent.click(screen.getByText('更多信息'));
  expect(screen.getByRole('region', { name: '回答依据' })).toHaveTextContent('方法说明；方法说明');
  expect(screen.getByRole('region', { name: '回答依据' })).not.toHaveTextContent('unit-2');
});

it('keeps execution summaries and unresolved questions visible without a conclusion', () => {
  render(<AssistantContent reply={reply({
    conclusion: null, sources: [], details: [{ markdown: '执行过程中的补充说明。', sourceIndexes: [] }],
    executionSummary: { markdown: '已完成 1 项分析；生成 2 份结果。', sourceIndexes: [] },
    unresolvedItems: [{ markdown: '请确认监测值使用米还是厘米。', sourceIndexes: [] }],
  })} onKnowledge={vi.fn()} onResource={vi.fn()} />);

  expect(screen.getByText('已完成 1 项分析；生成 2 份结果。')).toBeVisible();
  expect(screen.getByText('请确认监测值使用米还是厘米。')).toBeVisible();
  expect(screen.getByText('执行过程中的补充说明。')).toBeVisible();
  expect(screen.getByRole('region', { name: '结果说明' })).toBeVisible();
  expect(screen.queryByText('更多信息')).not.toBeInTheDocument();
});

it('does not infer a knowledge action from Markdown text or links', () => {
  const read = vi.fn();
  const markdown = `旧格式（knowledge_id: document-1, chunk_index: 0, checksum: ${checksum}）。\n\n[旧链接](/api/v12/knowledge/documents/document-1/chunks/0?expectedChecksum=${encodeURIComponent(checksum)})`;
  render(<AssistantContent reply={reply({ conclusion: { markdown, sourceIndexes: [] }, sources: [] })} onKnowledge={read} onResource={vi.fn()} />);
  expect(screen.getByText(/knowledge_id: document-1/)).toBeVisible();
  expect(screen.queryByRole('button', { name: '旧链接' })).not.toBeInTheDocument();
  expect(read).not.toHaveBeenCalled();
});

it('renders tables and emphasis without executing HTML, unsafe links or remote images', () => {
  const markdown = '**水位**\n\n| 井号 | 数值 |\n| --- | --- |\n| W01 | 12 |\n\n<script>alert(1)</script>\n\n[危险链接](javascript:alert%281%29)\n\n![远端图片](https://example.com/tracker.png)';
  const { container } = render(<AssistantContent reply={reply({ conclusion: { markdown, sourceIndexes: [] }, sources: [] })} onKnowledge={vi.fn()} onResource={vi.fn()} />);
  expect(screen.getByRole('table')).toBeVisible();
  expect(container.querySelector('strong')).toHaveTextContent('水位');
  expect(container.querySelector('script')).toBeNull();
  expect(container.querySelector('img')).toBeNull();
  expect(screen.queryByRole('link', { name: '危险链接' })).not.toBeInTheDocument();
});

it('makes wide analysis tables reachable by keyboard without losing scientific text', () => {
  const markdown = '## 监测结果\n\n| 监测井 | 适用范围与限制 |\n| --- | --- |\n| W01 | 插值估计不代表实际观测，单位为米，缺测值未填零。 |';
  render(<AssistantContent reply={reply({ conclusion: { markdown, sourceIndexes: [] }, sources: [] })} onKnowledge={vi.fn()} onResource={vi.fn()} />);
  const tableRegion = screen.getByRole('region', { name: '分析表格，可横向滚动' });
  expect(tableRegion).toHaveAttribute('tabindex', '0');
  tableRegion.focus();
  expect(tableRegion).toHaveFocus();
  expect(screen.getByRole('cell', { name: '插值估计不代表实际观测，单位为米，缺测值未填零。' })).toBeVisible();
});

it('keeps structured code collapsed and opens exact resource links', () => {
  const openResource = vi.fn();
  const markdown = `[查看结果](/api/v12/data/versions/version-1)\n\n\`\`\`json\n{"trace_id":"internal-123"}\n\`\`\``;
  render(<AssistantContent reply={reply({ conclusion: { markdown, sourceIndexes: [] }, sources: [] })} onKnowledge={vi.fn()} onResource={openResource} />);
  fireEvent.click(screen.getByRole('button', { name: '查看结果' }));
  expect(openResource).toHaveBeenCalledExactlyOnceWith('version-1');
  expect(screen.getByText('{"trace_id":"internal-123"}')).not.toBeVisible();
  fireEvent.click(screen.getByText('查看代码或结构化内容'));
  expect(screen.getByText('{"trace_id":"internal-123"}')).toBeVisible();
});

it('retains a source DOM target across parent updates so drawers can restore focus', () => {
  const value = reply();
  const onKnowledge = vi.fn(), onResource = vi.fn();
  const view = render(<AssistantContent reply={value} onKnowledge={onKnowledge} onResource={onResource} />);
  fireEvent.click(screen.getByText('更多信息'));
  const link = screen.getByRole('button', { name: '打开依据：方法说明' });
  link.focus();
  view.rerender(<AssistantContent reply={value} onKnowledge={onKnowledge} onResource={onResource} />);
  expect(screen.getByRole('button', { name: '打开依据：方法说明' })).toBe(link);
  expect(link).toHaveFocus();
});

it('preserves ordinary user text and incomplete attachment-like text verbatim', () => {
  const content = '附加资料：\n- 井号 W-001（Resource ID: 不完整）';
  render(<MessageContent content={content} onResource={vi.fn()} />);
  expect(screen.getByText(content, { normalizer: text => text })).toBeVisible();
});

it('summarizes saved attachment versions while preserving complete inspection and navigation', () => {
  const versionId = '01234567-89ab-cdef-0123-456789abcdef';
  const open = vi.fn();
  render(<MessageContent content={`附加数据：\n- 监测井.csv（数据集: dataset-1；版本: ${versionId}；语义: semantics-1；校验和: ${checksum}）`} onResource={open} />);
  const attachment = screen.getByRole('button', { name: /查看附件 监测井.csv/ });
  expect(attachment).toHaveTextContent('01234567…cdef');
  expect(attachment).not.toHaveTextContent(versionId);
  expect(attachment).toHaveAttribute('title', `数据版本 ${versionId}`);
  fireEvent.click(attachment);
  expect(open).toHaveBeenCalledExactlyOnceWith(versionId);
});

it('explains failures without exposing diagnostic codes until requested', () => {
  render(<ErrorNotice error={new ApiError(503, 'AGENT_PROVIDER_UNAVAILABLE')} />);
  expect(screen.getByText(/智能助手服务暂时不可用/)).toBeVisible();
  expect(screen.getByText(/AGENT_PROVIDER_UNAVAILABLE/)).not.toBeVisible();
  fireEvent.click(screen.getByText('技术详情'));
  expect(screen.getByText(/AGENT_PROVIDER_UNAVAILABLE/)).toBeVisible();
  expect(() => errorMessage(new TypeError('programming error'))).toThrow('programming error');
});

it('shows saved agent reason and metrics only in technical details', () => {
  render(<ErrorNotice code="AGENT_MODEL_OUTPUT_INVALID"
    agentDiagnostics={{ reason: 'REPLY_SCHEMA_INVALID', issues: [
      { path: '/conclusion', type: 'missing' },
    ] }}
    agentMetrics={{ requestCount: 3, observationCallCount: 2, inputTokens: 120,
      outputTokens: 30, totalTokens: 150, durationMs: 420 }} />);
  expect(screen.getByText(/REPLY_SCHEMA_INVALID/)).not.toBeVisible();
  fireEvent.click(screen.getByText('技术详情'));
  expect(screen.getByText(/原因：REPLY_SCHEMA_INVALID/)).toBeVisible();
  expect(screen.getByText(/conclusion · missing/)).toBeVisible();
  expect(screen.getByText(/模型请求：3/)).toBeVisible();
  expect(screen.getByText(/资料读取：2/)).toBeVisible();
  expect(screen.getByText(/词元：150/)).toBeVisible();
});

it('distinguishes unavailable provider usage from a measured zero', () => {
  render(<ErrorNotice code="AGENT_MODEL_OUTPUT_INVALID"
    agentMetrics={{ requestCount: 2, observationCallCount: 1, inputTokens: 0,
      outputTokens: 0, totalTokens: 0, durationMs: 420 }} />);
  fireEvent.click(screen.getByText('技术详情'));

  expect(screen.getByText(/词元：服务端未返回词元用量/)).toBeVisible();
  expect(screen.queryByText(/本地上下文估算/)).not.toBeInTheDocument();
  expect(screen.queryByText(/词元：0/)).not.toBeInTheDocument();
});
