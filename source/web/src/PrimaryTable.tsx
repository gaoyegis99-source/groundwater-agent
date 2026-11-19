import { useEffect, useState } from 'react';
import { tableColumnLayout } from '@platform/ui-boundary';
import { ApiError, readDataVersion, runDataQuery } from './api';
import { outputName } from './card-presentation';
import type { DataQueryPage, ResourceReference } from './types';
import { fieldUnit, fieldWithUnit, scientificCell } from './viewers/scientific-display';

export function PrimaryTable({ reference }: { reference: ResourceReference }) {
  const [preview, setPreview] = useState<(Pick<DataQueryPage, 'rows'> & {
    columns: { name: string; layout: ReturnType<typeof tableColumnLayout> }[];
  }) | null>();
  const [retry, setRetry] = useState(0);
  const signature = JSON.stringify(reference);
  useEffect(() => {
    const ref: ResourceReference = JSON.parse(signature);
    const controller = new AbortController();
    const { signal } = controller;
    setPreview(undefined);
    async function read() {
      const detail = await readDataVersion(ref.versionId, signal, ref);
      const fields = detail.descriptor.fields?.slice(0, 5);
      if (!fields?.length) throw new ApiError(422, 'TABLE_FIELDS_UNAVAILABLE');
      const page = await runDataQuery({ versionId: ref.versionId, semanticId: ref.semanticId, fields: fields.map(f => f.fieldId) }, 0, 5, signal);
      if (!page.result) throw new ApiError(422, 'TABLE_PREVIEW_UNAVAILABLE');
      const labels = new Map(fields.map(f => [f.fieldId, fieldWithUnit(f.name, fieldUnit(detail, f.fieldId))]));
      if (!signal.aborted) setPreview({
        columns: page.result.fields.map(field => ({ name: labels.get(field.fieldId) ?? field.fieldId, layout: tableColumnLayout(field.type) })),
        rows: page.result.rows,
      });
    }
    void read().catch(error => {
      if (!signal.aborted) {
        if (!(error instanceof ApiError)) throw error;
        setPreview(null);
      }
    });
    return () => controller.abort();
  }, [signature, retry]);
  if (preview === undefined) return <p role="status">正在读取主表…</p>;
  if (!preview) return <p>主表预览暂时无法读取。<button type="button" onClick={() => setRetry(n => n + 1)}>重试读取主表</button></p>;
  return <div className="table-scroll" role="region" tabIndex={0} aria-label="主要成果表格，可横向滚动"><table>
    <caption>{outputName(reference.outputName ?? '')} · 前 {preview.rows.length} 行</caption>
    <thead><tr>{preview.columns.map((column, index) => <th key={index} scope="col" data-column={column.layout}>{column.name}</th>)}</tr></thead>
    <tbody>{preview.rows.map((row, index) => <tr key={index}>{row.map((value, col) => <td key={col} data-column={preview.columns[col]?.layout}>{scientificCell(value)}</td>)}</tr>)}</tbody>
  </table></div>;
}
