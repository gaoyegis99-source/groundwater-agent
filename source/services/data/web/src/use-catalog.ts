import { useEffect, useMemo, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useUrlParam } from "@platform/operator-ui";
import { api, decodeVersion, PlatformUiError } from "./api";
import type { Schema, RunAction } from "./api";
import { retryDelay, retryable } from "./polling";

export type SemanticDraft = Schema["Semantics"];
export type DiscoverySort = "relevance" | "title-asc" | "title-desc";

export function useCatalog(_run: RunAction, active = true, governanceEnabled = true) {
  const queryClient = useQueryClient();
  const [search, setUrlSearch] = useUrlParam("q");
  const [tag, setUrlTag] = useUrlParam("tag");
  const [format, setUrlFormat] = useUrlParam("format");
  const [fieldTerm, setUrlFieldTerm] = useUrlParam("field");
  const [unit, setUrlUnit] = useUrlParam("unit");
  const [sortValue, setUrlSort] = useUrlParam("sort", "relevance");
  const [selectedVersionId, setSelectedVersionId] = useUrlParam("version");
  const [searchDraft, setSearchDraft] = useState(search);
  const [fieldTermDraft, setFieldTermDraft] = useState(fieldTerm);
  const [unitDraft, setUnitDraft] = useState(unit);
  const [historicalSemanticId, setHistoricalSemanticId] = useState("");
  useEffect(() => setSearchDraft(search), [search]);
  useEffect(() => setFieldTermDraft(fieldTerm), [fieldTerm]);
  useEffect(() => setUnitDraft(unit), [unit]);

  const sort: DiscoverySort = ["title-asc", "title-desc"].includes(sortValue)
    ? sortValue as DiscoverySort
    : "relevance";
  const discovery = useInfiniteQuery<Schema["DiscoveryResponse"], PlatformUiError>({
    queryKey: ["data-discovery", search, tag, format, fieldTerm, unit],
    enabled: active,
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) => api("data_discovery", "discovery", {
      query: search,
      tags: tag ? [tag] : [],
      formats: format ? [format] : [],
      fieldTerms: fieldTerm ? [fieldTerm] : [],
      units: unit ? [unit] : [],
      limit: 30,
      ...(pageParam ? { after: String(pageParam) } : {}),
    }, { signal }),
    getNextPageParam: page => page.nextCursor ?? undefined,
    retry: retryable,
    retryDelay,
  });
  const inventory = useQuery<Schema["InventoryResponse"], PlatformUiError>({
    queryKey: ["data-inventory"],
    enabled: active,
    queryFn: ({ signal }) => api("data_inventory", "inventory", undefined, { signal }),
    retry: retryable,
    retryDelay,
    staleTime: 60_000,
  });
  const datasets = useMemo(() => {
    const items = discovery.data?.pages.flatMap(page => page.items) ?? [];
    const deduplicated = [...new Map(items.map(item => [item.datasetId, item])).values()];
    if (sort === "title-asc") return deduplicated.sort((left, right) => left.title.localeCompare(right.title, "zh-CN"));
    if (sort === "title-desc") return deduplicated.sort((left, right) => right.title.localeCompare(left.title, "zh-CN"));
    return deduplicated;
  }, [discovery.data, sort]);

  const selectedQuery = useQuery({
    queryKey: ["data-version", selectedVersionId],
    enabled: active && Boolean(selectedVersionId),
    queryFn: async ({ signal }) => decodeVersion(await api(
      "data_get_version",
      `versions/${encodeURIComponent(selectedVersionId)}`,
      undefined,
      { signal },
    )),
    retry: retryable,
    retryDelay,
  });
  const selected = selectedQuery.data ?? null;
  const governance = useQuery<Schema["DatasetGovernanceResponse"], PlatformUiError>({
    queryKey: ["data-dataset-governance", selected?.datasetId ?? ""],
    enabled: active && governanceEnabled && Boolean(selected?.datasetId),
    queryFn: ({ signal }) => api(
      "data_get_dataset_governance",
      `datasets/${encodeURIComponent(selected!.datasetId)}/governance`,
      undefined,
      { signal },
    ),
    retry: retryable,
    retryDelay,
  });
  const versions = useInfiniteQuery<Schema["VersionPage"], PlatformUiError>({
    queryKey: ["data-dataset-versions", selected?.datasetId ?? ""],
    enabled: active && Boolean(selected?.datasetId),
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) => api(
      "data_list_dataset_versions",
      `datasets/${encodeURIComponent(selected!.datasetId)}/versions?limit=30${pageParam ? `&cursor=${encodeURIComponent(String(pageParam))}` : ""}`,
      undefined,
      { signal },
    ),
    getNextPageParam: page => page.nextCursor ?? undefined,
    retry: retryable,
    retryDelay,
  });
  const semanticHistory = useInfiniteQuery<Schema["SemanticRevisionPage"], PlatformUiError>({
    queryKey: ["data-semantic-history", selected?.versionId ?? ""],
    enabled: active && Boolean(selected?.versionId),
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) => api(
      "data_list_semantic_revisions",
      `versions/${encodeURIComponent(selected!.versionId)}/semantics?limit=30${pageParam ? `&cursor=${encodeURIComponent(String(pageParam))}` : ""}`,
      undefined,
      { signal },
    ),
    getNextPageParam: page => page.nextCursor ?? undefined,
    retry: retryable,
    retryDelay,
  });
  const historicalSemantic = useQuery({
    queryKey: ["data-semantic-revision", selected?.versionId ?? "", historicalSemanticId],
    enabled: active && Boolean(selected?.versionId && historicalSemanticId),
    queryFn: ({ signal }) => api(
      "data_get_semantic_revision",
      `versions/${encodeURIComponent(selected!.versionId)}/semantics/${encodeURIComponent(historicalSemanticId)}`,
      undefined,
      { signal },
    ),
    retry: retryable,
    retryDelay,
  });
  const semanticMutation = useMutation({
    mutationFn: async (semantics: SemanticDraft) => {
      if (!selected) return;
      await api(
        "data_create_semantic_revision",
        `versions/${encodeURIComponent(selected.versionId)}/semantics`,
        { expectedSemanticId: selected.semanticId, semantics },
      );
    },
  });

  const load = async (append = false) => {
    if (append) {
      await discovery.fetchNextPage();
      return;
    }
    if (searchDraft !== search || fieldTermDraft !== fieldTerm || unitDraft !== unit) {
      setUrlSearch(searchDraft.trim());
      setUrlFieldTerm(fieldTermDraft.trim(), "replace");
      setUrlUnit(unitDraft.trim(), "replace");
      return;
    }
    await discovery.refetch();
  };
  const inspect = async (id: string) => {
    setHistoricalSemanticId("");
    setSelectedVersionId(id);
  };
  const saveSemantics = async (semantics: SemanticDraft) => {
    await semanticMutation.mutateAsync(semantics);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["data-version", selectedVersionId] }),
      queryClient.invalidateQueries({ queryKey: ["data-semantic-history", selectedVersionId] }),
      queryClient.invalidateQueries({ queryKey: ["data-dataset-governance", selected?.datasetId ?? ""] }),
    ]);
  };
  const inspectSemantic = async (semanticId: string) => setHistoricalSemanticId(semanticId);

  return {
    datasets,
    search: searchDraft,
    setSearch: setSearchDraft,
    tag,
    setTag: setUrlTag,
    format,
    setFormat: setUrlFormat,
    fieldTerm: fieldTermDraft,
    setFieldTerm: setFieldTermDraft,
    unit: unitDraft,
    setUnit: setUnitDraft,
    sort,
    setSort: (value: DiscoverySort) => setUrlSort(value),
    appliedFilterCount: [tag, format, fieldTerm, unit].filter(Boolean).length + (sort === "relevance" ? 0 : 1),
    clearAdvancedFilters: () => {
      setFieldTermDraft("");
      setUnitDraft("");
      setUrlTag("", "replace");
      setUrlFormat("", "replace");
      setUrlFieldTerm("", "replace");
      setUrlUnit("", "replace");
      setUrlSort("relevance", "replace");
    },
    clearFilters: () => {
      setSearchDraft("");
      setFieldTermDraft("");
      setUnitDraft("");
      setUrlSearch("");
      setUrlTag("", "replace");
      setUrlFormat("", "replace");
      setUrlFieldTerm("", "replace");
      setUrlUnit("", "replace");
      setUrlSort("relevance", "replace");
    },
    inventory: inventory.data ?? null,
    totalMatches: discovery.data?.pages[0]?.totalMatches ?? 0,
    matchBasis: discovery.data?.pages[0]?.matchBasis ?? "",
    cursor: discovery.hasNextPage ? "more" : null,
    selected,
    governance: governance.data ?? null,
    history: versions.data?.pages.flatMap(page => page.items) ?? [],
    historyCursor: versions.hasNextPage ? "more" : null,
    semanticHistory: semanticHistory.data?.pages.flatMap(page => page.items) ?? [],
    semanticHistoryCursor: semanticHistory.hasNextPage ? "more" : null,
    historicalSemantic: historicalSemantic.data ?? null,
    loading: discovery.isLoading,
    loadingMore: discovery.isFetchingNextPage,
    refreshing: discovery.isFetching && !discovery.isFetchingNextPage,
    catalogError: discovery.error,
    filterError: inventory.error,
    selectedVersionId,
    detailLoading: selectedQuery.isLoading,
    detailRefreshing: selectedQuery.isFetching || governance.isFetching || versions.isFetching || semanticHistory.isFetching || historicalSemantic.isFetching,
    detailError: selectedQuery.error ?? governance.error ?? versions.error ?? semanticHistory.error ?? historicalSemantic.error,
    saveError: semanticMutation.error,
    retryCatalog: async () => { await discovery.refetch(); },
    retryFilters: async () => { await inventory.refetch(); },
    retryDetail: async () => {
      const retries: Promise<unknown>[] = [];
      if (selectedQuery.error && selectedVersionId) retries.push(selectedQuery.refetch());
      if (governance.error && selected) retries.push(governance.refetch());
      if (versions.error && selected) retries.push(versions.refetch());
      if (semanticHistory.error && selected) retries.push(semanticHistory.refetch());
      if (historicalSemantic.error && selected && historicalSemanticId) retries.push(historicalSemantic.refetch());
      await Promise.all(retries);
    },
    load,
    inspect,
    loadHistory: async () => { await versions.fetchNextPage(); },
    loadSemanticHistory: async () => { await semanticHistory.fetchNextPage(); },
    inspectSemantic,
    saveSemantics,
  };
}
