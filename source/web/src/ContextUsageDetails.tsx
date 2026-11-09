export interface ContextUsage {
  tokenizer: 'o200k_base'; totalTokens: number; availableInputTokens: number; excessTokens: number;
  breakdown: { tools: number; instructions: number; dialogue: number; executionReferences: number; observations: number; serialization: number };
}

const labels: Record<keyof ContextUsage['breakdown'], string> = {
  tools: '工具与输出结构', instructions: '指令', dialogue: '对话与记忆',
  executionReferences: '执行引用与工具调用', observations: '观察内容', serialization: '序列化与分词差额',
};

export function ContextUsageDetails({ usage }: { usage: ContextUsage }) {
  const largest = (Object.keys(labels) as (keyof ContextUsage['breakdown'])[])
    .filter(key => key !== 'serialization').reduce((a, b) => usage.breakdown[a] >= usage.breakdown[b] ? a : b);
  return <details className="technical-details context-usage">
    <summary>上下文占用：{usage.totalTokens} / {usage.availableInputTokens} 词元{usage.excessTokens > 0 && ' · 已超限'}</summary>
    {usage.excessTokens > 0 && <p role="status">输入超出预算 {usage.excessTokens} 词元，占用最多的是{labels[largest]}。本次模型调用尚未发出。</p>}
    <table><thead><tr><th>内容</th><th>估算词元</th></tr></thead>
      <tbody>{(Object.entries(labels) as [keyof ContextUsage['breakdown'], string][]).map(([key, label]) =>
        <tr key={key}><td>{label}</td><td>{usage.breakdown[key]}</td></tr>)}</tbody></table>
    <p className="muted">使用 {usage.tokenizer} 本地估算，可用输入预算已扣除输出预留。对话含压缩记忆；执行引用含工具调用及执行回执；序列化差额含 JSON 包装、转义及分词边界，可为负数。此处不是服务端计费量。</p>
  </details>;
}
