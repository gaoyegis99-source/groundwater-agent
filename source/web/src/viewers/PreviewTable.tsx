import { tableColumnLayout } from '@platform/ui-boundary';
import type { DataPreviewResult } from '../types';
import { EmptyState } from '../ui/Feedback';
import { scientificCell } from './scientific-display';

export function PreviewTable({ result, offset, label, empty }: {
  result: DataPreviewResult; offset: number; label: string; empty: string;
}) {
  if (!result.rows.length) return <EmptyState description={empty} />;
  const columns = result.fields.map(field => ({ ...field, layout: tableColumnLayout(field.type) }));
  return <div className="table-scroll" role="region" aria-label={label} tabIndex={0}>
    <table>
      <caption>本页 {result.rows.length} 行。缺测表示空值，0 表示实际零值。</caption>
      <thead><tr>{columns.map(field => <th key={field.fieldId} scope="col" data-column={field.layout}>{field.name}<small>{field.type}</small></th>)}</tr></thead>
      <tbody>{result.rows.map((row, index) => <tr key={offset + index}>
        {columns.map((field, column) => <td key={field.fieldId} data-column={field.layout} data-missing={row[column] == null || undefined}>{scientificCell(row[column])}</td>)}
      </tr>)}</tbody>
    </table>
  </div>;
}
