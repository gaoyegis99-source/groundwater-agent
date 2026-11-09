import { useEffect, useMemo, useState } from "react";
import { useQuery as useServerQuery } from "@tanstack/react-query";
import { newRequestId } from "@platform/ui-boundary";
import { useUrlParam } from "@platform/operator-ui";
import { api, PlatformUiError } from "./api";
import type { QueryResult, Schema, Version } from "./api";
import { deadlineExceeded, retryDelay, retryable } from "./polling";
import { readRecovery, saveRecovery } from "./recovery";

const TERMINAL = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);
const MAX_HISTORY = 20;

export type FilterDraft = { id: string; field: string; op: Schema["Filter"]["op"]; value: string };
export type AggregateDraft = { id: string; function: Schema["Aggregate"]["function"]; field: string; alias: string };
export type SortDraft = { id: string; field: string; direction: Schema["Sort"]["direction"] };
export type QueryHistoryEntry = {
  queryId: string;
  createdAt: string;
  title: string;
  versionId: string;
  summary: string;
  request: Schema["DataQueryRequest"] | null;
};

let rowSequence = 0;
const rowId = (prefix: string) => `${prefix}-${++rowSequence}`;

function historyKey(recoveryKey: string): string {
  return `${recoveryKey}:history`;
}

function readHistory(recoveryKey: string): QueryHistoryEntry[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(historyKey(recoveryKey)) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.flatMap(item => {
      if (!item || typeof item !== "object") return [];
      const record = item as Record<string, unknown>;
      if (
        typeof record.queryId !== "string"
        || typeof record.createdAt !== "string"
        || typeof record.title !== "string"
        || typeof record.versionId !== "string"
        || typeof record.summary !== "string"
      ) return [];
      const request = record.request && typeof record.request === "object" && !Array.isArray(record.request)
        ? record.request as Schema["DataQueryRequest"]
        : null;
      return [{
        queryId: record.queryId,
        createdAt: record.createdAt,
        title: record.title,
        versionId: record.versionId,
        summary: record.summary,
        request,
      }];
    }).slice(0, MAX_HISTORY);
  } catch {
    return [];
  }
}

function fieldValue(type: string | undefined, value: string): string | number | boolean {
  if (type && /(int|float|double|decimal|number)/i.test(type)) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : value;
  }
  if (type && /bool/i.test(type)) return value === "true";
  return value;
}

export function useQuery(
  selected: Version | null,
  versions: Schema["VersionSummary"][],
  scope: string,
  recoveryKey: string,
  deadlineSeconds: number,
  active = true,
) {
  const initial = useMemo(() => readRecovery(localStorage, recoveryKey), [recoveryKey]);
  const initialHistory = useMemo(() => readHistory(recoveryKey), [recoveryKey]);
  const [urlQueryId, setUrlQueryId] = useUrlParam("query");
  const initialQueryId = urlQueryId || initial?.id || "";
  const [queryId, setQueryId] = useState(initialQueryId);
  const [createdAt, setCreatedAt] = useState(
    initial && initial.id === initialQueryId ? initial.createdAt : "",
  );
  const [offset, setOffset] = useState(0);
  const [pageSize, setPageSize] = useState(100);
  const [selectedFields, setSelectedFields] = useState<string[]>([]);
  const [filters, setFilters] = useState<FilterDraft[]>([]);
  const [aggregates, setAggregates] = useState<AggregateDraft[]>([]);
  const [groupBy, setGroupBy] = useState<string[]>([]);
  const [sorts, setSorts] = useState<SortDraft[]>([]);
  const [metricName, setMetricName] = useState("");
  const [metricVersions, setMetricVersions] = useState<string[]>([]);
  const [requestSnapshot, setRequestSnapshot] = useState<Schema["DataQueryRequest"] | null>(
    initialHistory.find(item => item.queryId === initialQueryId)?.request ?? null,
  );
  const [historyEntries, setHistoryEntries] = useState<QueryHistoryEntry[]>(initialHistory);

  useEffect(() => {
    const first = selected?.descriptor.fields[0]?.fieldId ?? "";
    setSelectedFields([]);
    setFilters(first ? [{ id: rowId("filter"), field: first, op: "eq", value: "" }] : []);
    setAggregates([]);
    setGroupBy([]);
    setSorts([]);
  }, [selected?.versionId]);
  useEffect(() => {
    const recovered = readRecovery(localStorage, recoveryKey);
    const nextQueryId = urlQueryId || recovered?.id || "";
    const nextHistory = readHistory(recoveryKey);
    setQueryId(nextQueryId);
    setCreatedAt(
      recovered && recovered.id === nextQueryId ? recovered.createdAt : "",
    );
    setOffset(0);
    setHistoryEntries(nextHistory);
    setRequestSnapshot(nextHistory.find(item => item.queryId === nextQueryId)?.request ?? null);
  }, [recoveryKey, urlQueryId]);

  const server = useServerQuery<QueryResult, PlatformUiError>({
    queryKey: [scope, "data-query", queryId, offset, pageSize],
    enabled: Boolean(queryId) && active,
    queryFn: ({ signal }) => api(
      "data_get_query",
      `queries/${encodeURIComponent(queryId)}?offset=${offset}&limit=${pageSize}`,
      undefined,
      { signal },
    ),
    retry: retryable,
    retryDelay,
    refetchInterval: query => {
      const result = query.state.data;
      if (!result || TERMINAL.has(result.state)) return false;
      return createdAt && deadlineExceeded(createdAt, deadlineSeconds) ? false : 1_500;
    },
  });

  useEffect(() => {
    if (server.error && [404, 410].includes(server.error.status)) {
      localStorage.removeItem(recoveryKey);
      setQueryId("");
      setUrlQueryId("", "replace");
      setCreatedAt("");
      setRequestSnapshot(null);
    }
  }, [server.error, recoveryKey, setUrlQueryId]);

  const retain = (id: string, snapshot?: Schema["DataQueryRequest"]) => {
    const record = saveRecovery(localStorage, recoveryKey, id);
    setQueryId(id);
    setUrlQueryId(id, "replace");
    setCreatedAt(record.createdAt);
    setOffset(0);
    if (snapshot && selected) {
      const summary = `${snapshot.filters?.length ?? 0} 个筛选 · ${snapshot.aggregates?.length ?? 0} 个汇总 · ${snapshot.sort?.length ?? 0} 个排序`;
      const entry: QueryHistoryEntry = {
        queryId: id,
        createdAt: record.createdAt,
        title: selected.title,
        versionId: selected.versionId,
        summary,
        request: snapshot,
      };
      setHistoryEntries(current => {
        const next = [entry, ...current.filter(item => item.queryId !== id)].slice(0, MAX_HISTORY);
        localStorage.setItem(historyKey(recoveryKey), JSON.stringify(next));
        return next;
      });
      setRequestSnapshot(snapshot);
    } else {
      setRequestSnapshot(null);
    }
  };

  const submitQuery = async () => {
    if (!selected) return;
    const fieldsById = new Map(selected.descriptor.fields.map(field => [field.fieldId, field]));
    const snapshot: Schema["DataQueryRequest"] = {
      idempotencyKey: newRequestId(),
      kind: "table",
      versionIds: [selected.versionId],
      semanticIds: { [selected.versionId]: selected.semanticId },
      fields: selectedFields,
      filters: filters.flatMap(item => {
        if (!item.field || (item.op !== "is_null" && item.value === "")) return [];
        return [{
          field: item.field,
          op: item.op,
          ...(item.op === "is_null" ? {} : { value: fieldValue(fieldsById.get(item.field)?.type, item.value) }),
        }];
      }),
      groupBy,
      aggregates: aggregates.map(item => ({
        function: item.function,
        ...(item.function === "count" && !item.field ? {} : { field: item.field }),
        alias: item.alias.trim() || `${item.function}_${item.field || "rows"}`,
      })),
      sort: sorts.map(item => ({ field: item.field, direction: item.direction })),
    };
    const accepted = await api("data_create_query", "queries", snapshot);
    retain(accepted.queryId, snapshot);
  };

  const compareMetrics = async () => {
    const snapshot: Schema["DataQueryRequest"] = {
      idempotencyKey: newRequestId(),
      versionIds: metricVersions,
      metricName,
      kind: "metrics",
    };
    const accepted = await api("data_create_query", "queries", snapshot);
    retain(accepted.queryId, snapshot);
  };

  const cancel = async () => {
    if (!queryId) return;
    await api("data_cancel_query", `queries/${encodeURIComponent(queryId)}/cancel`, {});
    await server.refetch();
  };
  const openHistory = async (id: string) => {
    const entry = historyEntries.find(item => item.queryId === id);
    const record = saveRecovery(localStorage, recoveryKey, id, entry?.createdAt);
    setQueryId(id);
    setUrlQueryId(id);
    setCreatedAt(record.createdAt);
    setOffset(0);
    setRequestSnapshot(entry?.request ?? null);
  };
  const clearHistory = () => {
    localStorage.removeItem(historyKey(recoveryKey));
    setHistoryEntries([]);
    setRequestSnapshot(null);
  };

  const deadlineStopped = Boolean(
    queryId
    && createdAt
    && !TERMINAL.has(server.data?.state ?? "")
    && deadlineExceeded(createdAt, deadlineSeconds),
  );
  const activeHistoryEntry = historyEntries.find(item => item.queryId === queryId) ?? null;

  return {
    selected,
    versions,
    queryId,
    result: server.data ?? null,
    error: server.error,
    offset,
    setOffset,
    pageSize,
    setPageSize,
    selectedFields,
    setSelectedFields,
    filters,
    setFilters,
    aggregates,
    setAggregates,
    groupBy,
    setGroupBy,
    sorts,
    setSorts,
    metricName,
    setMetricName,
    metricVersions,
    setMetricVersions,
    requestSnapshot,
    activeHistoryEntry,
    historyEntries,
    openHistory,
    clearHistory,
    submitQuery,
    compareMetrics,
    cancel,
    deadlineStopped,
    checkNow: server.refetch,
  };
}
