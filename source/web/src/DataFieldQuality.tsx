import { formatFieldProfileValue } from '@platform/ui-boundary';
import type { ResourceDetail } from './types';

export function DataFieldQuality({ descriptor, semantics }: Pick<ResourceDetail, 'descriptor' | 'semantics'>) {
  const { fields, profile, rowCount } = descriptor;
  if (!fields?.length) return null;
  return <section>
    <h4>字段与质量</h4>
    {profile && <>
      <p>全量统计 · {rowCount} 行</p>
      <p className="muted">样例为每字段最先出现的 {profile.sampleLimitPerField} 个非空值，不代表值分布。</p>
    </>}
    <div className="table-scroll" tabIndex={0} role="region" aria-label="字段与质量">
      <table>
        <thead><tr><th scope="col">字段</th><th scope="col">类型</th><th scope="col">单位</th><th scope="col">空值</th>{profile && <th scope="col">字段画像</th>}</tr></thead>
        <tbody>{fields.map(field => {
          const semantic = semantics.fields?.[field.fieldId];
          const facts = field.profile;
          return <tr key={field.fieldId}>
            <th scope="row"><strong>{field.name}</strong>{semantic?.meaning && <small>{semantic.meaning}</small>}<details><summary>字段标识</summary><code>{field.fieldId}</code></details></th>
            <td>{field.type}</td><td>{semantic?.unit ?? '未声明'}</td><td>{field.nullCount ?? '未统计'}</td>
            {profile && <td>{facts && <>
              <div>非空值 {facts.nonNullCount}</div>
              {facts.nonFiniteCount ? <div>非有限数 {facts.nonFiniteCount}</div> : null}
              {facts.valueRange && <div>值域 <span>{formatFieldProfileValue(facts.valueRange.minimum, field.type, facts.exactIntegers?.minimum)} – {formatFieldProfileValue(facts.valueRange.maximum, field.type, facts.exactIntegers?.maximum)}</span></div>}
              {facts.numericString && <div>可解析为数字 {facts.numericString.parsedCount} / {facts.numericString.checkedCount}
                {facts.numericString.identifierLike && '，含前导零'}</div>}
              {facts.temporal && <div>日期时间解析 {facts.temporal.parsedCount} / {facts.temporal.checkedCount}
                {facts.temporal.kind === 'timestamp' && <>
                  <div>物理时区 {facts.temporal.physicalTimezone || '未声明'}</div>
                  <div>业务时区 {semantics.timezone || '未声明'}</div>
                </>}</div>}
              {facts.sampleValues.length > 0 && <details><summary>查看样例</summary><ul>
                {facts.sampleValues.map((sample, index) => <li key={index}><code>{formatFieldProfileValue(sample, field.type, facts.exactIntegers?.sampleValues?.[index])}</code></li>)}
              </ul></details>}
            </>}</td>}
          </tr>;
        })}</tbody>
      </table>
    </div>
  </section>;
}
