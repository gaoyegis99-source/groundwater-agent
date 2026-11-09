import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { newRequestId } from "@platform/ui-boundary";
import { api, PlatformUiError } from "./api";
import type { Schema, Version } from "./api";
import { retryDelay, retryable } from "./polling";

const TERMINAL = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);

export function usePreview(version: Version, deadlineSeconds: number) {
  const eligibleObjects = version.objects.filter(object => ["xlsx", "geopackage"].includes(object.format ?? ""));
  const [objectId, setObjectId] = useState(eligibleObjects[0]?.objectId ?? "");
  const [selector, setSelector] = useState("");
  const [previewId, setPreviewId] = useState("");
  const [createdAt, setCreatedAt] = useState("");
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlatformUiError | null>(null);
  const selectedObject = eligibleObjects.find(object => object.objectId === objectId) ?? eligibleObjects[0] ?? null;
  const server = useQuery<Schema["PreviewResponse"], PlatformUiError>({
    queryKey: ["data-preview", previewId, offset],
    enabled: Boolean(previewId),
    queryFn: ({ signal }) => api("data_get_preview", `previews/${encodeURIComponent(previewId)}?offset=${offset}&limit=100`, undefined, { signal }),
    retry: retryable,
    retryDelay,
    refetchInterval: query => {
      const value = query.state.data;
      if (!value || TERMINAL.has(value.state)) return false;
      return createdAt && Date.now() - Date.parse(createdAt) > deadlineSeconds * 1_000 ? false : 1_500;
    },
  });
  const create = async () => {
    if (!selectedObject) return;
    setBusy(true);
    setError(null);
    setPreviewId("");
    setOffset(0);
    try {
      const kind: Schema["PreviewRequest"]["kind"] = selectedObject.format === "xlsx" ? "xlsx_table" : "geopackage_layer";
      const request: Schema["PreviewRequest"] = {
        idempotencyKey: newRequestId(),
        kind,
        objectId: selectedObject.objectId,
        versionId: version.versionId,
        semanticId: version.semanticId,
        ...(selector.trim() ? (kind === "xlsx_table" ? { sheetName: selector.trim() } : { layerName: selector.trim() }) : {}),
      };
      const accepted = await api("data_create_preview", "previews", request);
      setCreatedAt(new Date().toISOString());
      setPreviewId(accepted.previewId);
    } catch (caught) {
      setError(caught instanceof PlatformUiError ? caught : new PlatformUiError(0, "PREVIEW_CREATE_FAILED"));
    } finally {
      setBusy(false);
    }
  };
  const cancel = async () => {
    if (!previewId) return;
    await api("data_cancel_preview", `previews/${encodeURIComponent(previewId)}/cancel`, {});
    await server.refetch();
  };
  return {
    eligibleObjects, selectedObject, objectId, setObjectId, selector, setSelector,
    previewId, result: server.data ?? null, error: error ?? server.error,
    busy, offset, setOffset, create, cancel, checkNow: server.refetch,
  };
}
