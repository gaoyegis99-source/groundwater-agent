import { ApiError, errorCodeMessage, errorMessage } from './api';
import { agentTokenUsageLabel } from './chat-state';
import type { AgentDiagnostics, AgentMetrics, WorkflowValidationDiagnostics } from './types';

type Props = {
  title?: string; diagnostics?: WorkflowValidationDiagnostics | null;
  agentDiagnostics?: AgentDiagnostics | null; agentMetrics?: AgentMetrics | null;
} & ({ error: ApiError | string; code?: never } | { code: string; error?: never });

export function ErrorNotice({ error, code, title, diagnostics, agentDiagnostics, agentMetrics }: Props) {
  const message = diagnostics ? errorCodeMessage(diagnostics.code) : code !== undefined ? errorCodeMessage(code) : typeof error === 'string' ? error : errorMessage(error);
  const details = [
    code ?? (error instanceof ApiError ? `${error.code}\nHTTP ${error.status === 0 ? '未收到响应' : error.status}` : null),
    error instanceof ApiError && error.requestId ? `请求 ID：${error.requestId}` : null,
    error instanceof ApiError && error.retryAfter ? `建议等待：${error.retryAfter} 秒` : null,
    agentDiagnostics ? [
      `原因：${agentDiagnostics.reason}`,
      ...(agentDiagnostics.issues ?? []).map((issue) => `${issue.path || '/'} · ${issue.type}`),
    ].join('\n') : null,
    agentMetrics ? `模型请求：${agentMetrics.requestCount}\n资料读取：${agentMetrics.observationCallCount}\n词元：${agentTokenUsageLabel(agentMetrics)}${agentMetrics.totalTokens > 0 ? `（输入 ${agentMetrics.inputTokens} / 输出 ${agentMetrics.outputTokens}）` : ''}\n耗时：${agentMetrics.durationMs} 毫秒` : null,
  ].filter((value): value is string => value !== null);
  const node = diagnostics && /^\/graph\/nodes\/(\d+)(?:\/|$)/.exec(diagnostics.path);
  return <div className="error-notice" role="alert">
    {title && <strong>{title}</strong>}
    <p>{message}</p>
    {diagnostics && <p>{node ? `请核对第 ${Number(node[1]) + 1} 步，` : ''}调整编排要求后，可重新提交草稿。</p>}
    {!!details.length && <details className="technical-details"><summary>技术详情</summary><pre>{details.join('\n')}</pre>
      {diagnostics && <p><code>{diagnostics.path || '（草稿根对象）'}</code> · {diagnostics.code}</p>}
      {error instanceof ApiError && error.issues?.map((issue, index) => <p key={index}><code>{issue.pointer}</code>：{issue.message} {issue.code && <small>（{issue.code}）</small>}</p>)}
      {error instanceof ApiError && error.requestId && <button type="button" onClick={() => void navigator.clipboard.writeText(error.requestId!)}>复制请求 ID</button>}
    </details>}
  </div>;
}
