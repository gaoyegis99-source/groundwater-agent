import { useDeferredValue, useMemo, useRef, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import type { Key } from "react";
import {
  Button,
  ComboBox,
  Input,
  Label,
  ListBox,
  ListBoxItem,
  Popover,
} from "react-aria-components";
import { dataApi, decodeDataVersion, errorText, PlatformUiError } from "./api";
import type { DataCapabilities, DataSchema, KnowledgeSchema } from "./api";
import { retryDelay, retryable } from "./polling";

type Binding = KnowledgeSchema["DataBinding"];

export function BindingPicker({
  capabilities,
  compatible,
  value,
  onChange,
}: {
  capabilities: DataCapabilities | null;
  compatible: boolean;
  value: Binding | null;
  onChange: (value: Binding | null) => void;
}) {
  const [semantics, setSemantics] = useState<DataSchema["SemanticRevisionSummary"][]>([]);
  const [fields, setFields] = useState<Array<{ fieldId: string; name: string }>>([]);
  const [datasetId, setDatasetId] = useState(value?.datasetId ?? "");
  const [versionId, setVersionId] = useState(value?.versionId ?? "");
  const [semanticId, setSemanticId] = useState(value?.semanticId ?? "");
  const [fieldId, setFieldId] = useState(value?.fieldId ?? "");
  const [datasetSearch, setDatasetSearch] = useState("");
  const [versionSearch, setVersionSearch] = useState("");
  const [error, setError] = useState<PlatformUiError | null>(null);
  const [versionLoading, setVersionLoading] = useState(false);
  const datasetSelection = useRef(0);
  const versionSelection = useRef(0);
  const selectedDatasetKey = useRef(value?.datasetId ?? "");
  const selectedVersionKey = useRef(value?.versionId ?? "");
  const deferredDatasetSearch = useDeferredValue(datasetSearch);
  const datasetPages = useInfiniteQuery<DataSchema["DatasetPage"], PlatformUiError>({
    queryKey: ["binding-datasets", capabilities?.productId ?? "", capabilities?.project ?? "", deferredDatasetSearch],
    enabled: Boolean(capabilities && compatible),
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) => dataApi(
      "data_list_datasets",
      `datasets?query=${encodeURIComponent(deferredDatasetSearch.trim())}&limit=30${pageParam ? `&cursor=${encodeURIComponent(String(pageParam))}` : ""}`,
      signal,
    ),
    getNextPageParam: page => page.nextCursor ?? undefined,
    retry: retryable,
    retryDelay,
  });
  const datasets = useMemo(() => {
    const items = datasetPages.data?.pages.flatMap(page => page.items) ?? [];
    return [...new Map(items.map(item => [item.datasetId, item])).values()];
  }, [datasetPages.data]);
  const versionPages = useInfiniteQuery<DataSchema["VersionPage"], PlatformUiError>({
    queryKey: ["binding-versions", datasetId],
    enabled: Boolean(datasetId),
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) => dataApi(
      "data_list_dataset_versions",
      `datasets/${encodeURIComponent(datasetId)}/versions?limit=30${pageParam ? `&cursor=${encodeURIComponent(String(pageParam))}` : ""}`,
      signal,
    ),
    getNextPageParam: page => page.nextCursor ?? undefined,
    retry: retryable,
    retryDelay,
  });
  const versions = useMemo(() => {
    const items = versionPages.data?.pages.flatMap(page => page.items) ?? [];
    const query = versionSearch.trim().toLocaleLowerCase();
    return query ? items.filter(item => item.versionId.toLocaleLowerCase().includes(query)) : items;
  }, [versionPages.data, versionSearch]);

  const chooseDataset = async (key: Key | null) => {
    const next = key === null ? "" : String(key);
    if (next && next === selectedDatasetKey.current) return;
    selectedDatasetKey.current = next;
    selectedVersionKey.current = "";
    datasetSelection.current += 1;
    versionSelection.current += 1;
    setDatasetId(next);
    setDatasetSearch(datasets.find(item => item.datasetId === next)?.title ?? next);
    setVersionId("");
    setVersionSearch("");
    setSemanticId("");
    setFieldId("");
    setSemantics([]);
    setFields([]);
    setError(null);
    onChange(null);
    if (!next) return;
  };

  const chooseVersion = async (key: Key | null) => {
    const next = key === null ? "" : String(key);
    if (next && next === selectedVersionKey.current) return;
    selectedVersionKey.current = next;
    const selection = ++versionSelection.current;
    setVersionId(next);
    setVersionSearch(next);
    setSemanticId("");
    setFieldId("");
    setSemantics([]);
    setFields([]);
    setError(null);
    onChange(null);
    if (!next) return;
    setVersionLoading(true);
    try {
      const [detail, history] = await Promise.all([
        dataApi("data_get_version", `versions/${encodeURIComponent(next)}`),
        dataApi("data_list_semantic_revisions", `versions/${encodeURIComponent(next)}/semantics?limit=100`),
      ]);
      if (selection !== versionSelection.current) return;
      const validated = decodeDataVersion(detail);
      const descriptor = validated.descriptor as { fields?: Array<{ fieldId?: unknown; name?: unknown }> };
      setFields((descriptor.fields ?? []).flatMap(field => (
        typeof field.fieldId === "string" && typeof field.name === "string"
          ? [{ fieldId: field.fieldId, name: field.name }]
          : []
      )));
      setSemantics(history.items);
      setSemanticId(validated.semanticId);
      onChange({
        serviceBinding: "data-primary",
        projectId: capabilities!.project,
        datasetId,
        versionId: next,
        semanticId: validated.semanticId,
        fieldId: null,
      });
    } catch (caught) {
      if (selection === versionSelection.current) {
        selectedVersionKey.current = "";
        setError(caught as PlatformUiError);
      }
    } finally {
      if (selection === versionSelection.current) setVersionLoading(false);
    }
  };

  const emit = (nextSemantic: string, nextField: string) => {
    if (!capabilities || !datasetId || !versionId || !nextSemantic) {
      onChange(null);
      return;
    }
    onChange({
      serviceBinding: "data-primary",
      projectId: capabilities.project,
      datasetId,
      versionId,
      semanticId: nextSemantic,
      fieldId: nextField || null,
    });
  };

  if (!capabilities) return <p className="muted">Data 目录不可用，不能选择精确绑定。</p>;
  if (!compatible) return <p role="alert" className="inline-error">Data 与 Knowledge 的产品、项目或主体不一致；精确绑定已禁用。</p>;
  return (
    <fieldset className="binding-picker">
      <legend>限定到具体数据版本（可选）</legend>
      <p className="binding-catalog-state" role="status">
        {datasetPages.isLoading || datasetPages.isFetching && deferredDatasetSearch !== datasetSearch ? "正在搜索 Data 目录…" : datasets.length > 0 ? `已加载 ${datasets.length} 个数据集${datasetPages.hasNextPage ? "，还有更多" : ""}` : "Data 目录中没有可绑定的数据集"}
      </p>
      <ComboBox inputValue={datasetSearch} onInputChange={setDatasetSearch} selectedKey={datasetId || null} onSelectionChange={chooseDataset}>
        <Label>数据集</Label>
        <div className="combo-input"><Input /><Button aria-label="打开数据集列表">▼</Button></div>
        <Popover><ListBox items={datasets}>{item => <ListBoxItem id={item.datasetId}>{item.title}<small>{item.datasetId}</small></ListBoxItem>}</ListBox></Popover>
      </ComboBox>
      <ComboBox inputValue={versionSearch} onInputChange={setVersionSearch} selectedKey={versionId || null} onSelectionChange={chooseVersion} isDisabled={!datasetId}>
        <Label>不可变版本</Label>
        <div className="combo-input"><Input /><Button aria-label="打开版本列表">▼</Button></div>
        <Popover><ListBox items={versions}>{item => <ListBoxItem id={item.versionId}>{item.versionId}<small>{new Date(item.createdAt).toLocaleString()}</small></ListBoxItem>}</ListBox></Popover>
      </ComboBox>
      <p className="binding-version-state" role="status">
        {versionPages.isLoading
          ? "正在读取不可变版本…"
          : versionLoading
            ? "正在核对版本与语义修订…"
            : datasetId
              ? `已加载 ${versions.length} 个不可变版本`
              : "选择数据集后可继续选择不可变版本"}
      </p>
      {datasetPages.hasNextPage && <button type="button" className="secondary" disabled={datasetPages.isFetchingNextPage} onClick={() => void datasetPages.fetchNextPage()}>{datasetPages.isFetchingNextPage ? "正在加载…" : "加载更多数据集"}</button>}
      {versionPages.hasNextPage && <button type="button" className="secondary" disabled={versionPages.isFetchingNextPage} onClick={() => void versionPages.fetchNextPage()}>{versionPages.isFetchingNextPage ? "正在加载…" : "加载更多版本"}</button>}
      <label>语义修订
        <select value={semanticId} disabled={!versionId} onChange={event => { setSemanticId(event.target.value); setFieldId(""); emit(event.target.value, ""); }}>
          <option value="">选择语义修订</option>
          {semantics.map(item => <option key={item.semanticId} value={item.semanticId}>{new Date(item.createdAt).toLocaleString()} · {item.semanticId}</option>)}
        </select>
      </label>
      <label>字段（可选）
        <select value={fieldId} disabled={!semanticId} onChange={event => { setFieldId(event.target.value); emit(semanticId, event.target.value); }}>
          <option value="">整个版本</option>
          {fields.map(item => <option key={item.fieldId} value={item.fieldId}>{item.name} · {item.fieldId}</option>)}
        </select>
      </label>
      {value && <button type="button" className="secondary" onClick={() => { selectedDatasetKey.current = ""; selectedVersionKey.current = ""; setDatasetId(""); setDatasetSearch(""); setVersionId(""); setVersionSearch(""); setSemanticId(""); setFieldId(""); onChange(null); }}>清除绑定</button>}
      {(error || datasetPages.error || versionPages.error) && <p role="alert" className="inline-error">{errorText(error ?? datasetPages.error ?? versionPages.error)}</p>}
    </fieldset>
  );
}
