import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { newRequestId } from "@platform/ui-boundary";
import { useUrlParam } from "@platform/operator-ui";
import { api, decodePublication, PlatformUiError } from "./api";
import type { Publication } from "./api";
import { deadlineExceeded, retryDelay, retryable } from "./polling";
import { readRecovery, saveRecovery } from "./recovery";
import { uploadFile } from "./upload";

const TERMINAL = new Set(["PUBLISHED", "FAILED", "ABORTED"]);

export type OutputDraft = {
  name: string;
  title: string;
  format: string;
  policy: "FILE_ONLY" | "FILE_QUERYABLE" | "FILE_AND_METRICS";
  description: string;
  source: string;
  tags: string;
  nullValues: string;
};

export type FileValidation = {
  blocking: string[];
  warnings: string[];
};

export type FileProgress = {
  phase: "waiting" | "hash" | "upload" | "complete";
  completed: number;
  total: number;
};

function inferredFormat(file: File, formats: string[]): string {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  return formats.includes(extension) ? extension : "binary";
}

export function usePublication(
  scope: string,
  recoveryKey: string,
  formats: string[],
  formatMediaTypes: Record<string, string>,
  uploadBytes: number,
  deadlineSeconds: number,
  onPublished: () => Promise<void>,
  active = true,
) {
  const initial = useMemo(() => readRecovery(localStorage, recoveryKey), [recoveryKey]);
  const [urlPublicationId, setUrlPublicationId] = useUrlParam("publication");
  const initialPublicationId = urlPublicationId || initial?.id || "";
  const [publicationId, setPublicationId] = useState(initialPublicationId);
  const [createdAt, setCreatedAt] = useState(
    initial && initial.id === initialPublicationId ? initial.createdAt : "",
  );
  const [files, setFiles] = useState<File[]>([]);
  const [outputs, setOutputs] = useState<OutputDraft[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [uploadProgress, setUploadProgress] = useState<Record<string, FileProgress>>({});
  const uploadController = useRef<AbortController | null>(null);

  useEffect(() => {
    const recovered = readRecovery(localStorage, recoveryKey);
    const nextPublicationId = urlPublicationId || recovered?.id || "";
    setPublicationId(nextPublicationId);
    setCreatedAt(
      recovered && recovered.id === nextPublicationId ? recovered.createdAt : "",
    );
  }, [recoveryKey, urlPublicationId]);

  const server = useQuery<Publication, PlatformUiError>({
    queryKey: [scope, "data-publication", publicationId],
    enabled: Boolean(publicationId) && active,
    queryFn: async ({ signal }) => decodePublication(await api(
      "data_get_publication",
      "publications/" + encodeURIComponent(publicationId),
      undefined,
      { signal },
    )),
    retry: retryable,
    retryDelay,
    refetchInterval: query => {
      const value = query.state.data;
      if (!value || TERMINAL.has(value.state)) return false;
      const observedCreatedAt = value.createdAt || createdAt;
      return observedCreatedAt && deadlineExceeded(observedCreatedAt, deadlineSeconds) ? false : 1_500;
    },
  });

  useEffect(() => {
    if (server.error && [404, 410].includes(server.error.status)) {
      localStorage.removeItem(recoveryKey);
      setPublicationId("");
      setUrlPublicationId("", "replace");
      setCreatedAt("");
    }
  }, [server.error, recoveryKey, setUrlPublicationId]);

  const chooseFiles = (chosen: File[]) => {
    setFiles(chosen);
    setOutputs(chosen.map((file, index) => {
      const format = inferredFormat(file, formats);
      return {
        name: `output_${index}`,
        title: file.name,
        format,
        policy: ["csv", "parquet", "arrow-ipc-file"].includes(format) ? "FILE_QUERYABLE" : "FILE_ONLY",
        description: "",
        source: "operator_upload",
        tags: "",
        nullValues: "",
      };
    }));
  };

  const updateOutput = (index: number, value: OutputDraft) => {
    setOutputs(current => current.map((item, itemIndex) => itemIndex === index ? value : item));
  };

  const removeOutput = (index: number) => {
    setFiles(current => current.filter((_, itemIndex) => itemIndex !== index));
    setOutputs(current => current.filter((_, itemIndex) => itemIndex !== index));
  };

  const moveOutput = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= files.length) return;
    const move = <Value,>(items: Value[]) => {
      const next = [...items];
      [next[index], next[target]] = [next[target]!, next[index]!];
      return next;
    };
    setFiles(move);
    setOutputs(move);
  };

  const fileValidations = useMemo<FileValidation[]>(() => {
    const names = outputs.map(output => output.name.trim());
    return files.map((file, index) => {
      const output = outputs[index];
      const blocking: string[] = [];
      const warnings: string[] = [];
      if (file.size > uploadBytes) blocking.push(`文件超过单文件上限 ${uploadBytes} 字节`);
      if (!output || !formats.includes(output.format)) blocking.push("格式不在当前服务允许列表中");
      if (!output?.name.trim()) blocking.push("输出名称不能为空");
      if (output && names.filter(name => name === output.name.trim()).length > 1) blocking.push("输出名称必须在批次内唯一");
      if (output?.format === "binary") warnings.push("扩展名未识别，当前按通用二进制文件处理");
      const expectedMediaType = output ? formatMediaTypes[output.format] : undefined;
      if (expectedMediaType && file.type && expectedMediaType !== file.type) warnings.push(`浏览器识别为 ${file.type}，服务格式声明为 ${expectedMediaType}`);
      return { blocking, warnings };
    });
  }, [files, formatMediaTypes, formats, outputs, uploadBytes]);
  const validationBlocked = fileValidations.some(item => item.blocking.length > 0);

  const manifest = {
    outputs: outputs.map(output => ({
      name: output.name,
      title: output.title,
      format: output.format,
      policy: output.policy,
      tags: output.tags.split(",").map(item => item.trim()).filter(Boolean),
      conversion: {
        types: {},
        nullValues: output.nullValues.split(",").map(item => item.trim()).filter(Boolean),
      },
      semantics: { description: output.description, source: output.source },
    })),
    inputVersions: [],
    provenance: { client: "platform-operator-web-v1" },
  };

  const upload = async () => {
    if (files.length === 0 || files.length !== outputs.length || validationBlocked) return;
    setBusy(true);
    const controller = new AbortController();
    uploadController.current = controller;
    let id = "";
    setUploadProgress(Object.fromEntries(files.map((file, index) => [String(index), { phase: "waiting", completed: 0, total: file.size } satisfies FileProgress])));
    try {
      const publication = decodePublication(await api(
        "data_begin_publication",
        "publications",
        { ...manifest, idempotencyKey: newRequestId() },
      ));
      id = publication.publicationId;
      const recovered = saveRecovery(localStorage, recoveryKey, id);
      setPublicationId(id);
      setUrlPublicationId(id, "replace");
      setCreatedAt(recovered.createdAt);
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index]!;
        setNotice(`正在计算 ${file.name} 的 SHA-256`);
        await uploadFile(id, outputs[index]!.name, file, controller.signal, (phase, completed, total) => {
          setUploadProgress(current => ({ ...current, [String(index)]: { phase, completed, total } }));
          setNotice(`${phase === "hash" ? "校验" : "上传"} ${file.name}：${total > 0 ? Math.round((completed / total) * 100) : 0}%`);
        });
        setUploadProgress(current => ({ ...current, [String(index)]: { phase: "complete", completed: file.size, total: file.size } }));
      }
      await api("data_prepare_publication", `publications/${encodeURIComponent(id)}/prepare`, {});
      setNotice("文件已上传，正在准备发布。");
      await server.refetch();
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        if (id) {
          try {
            await api("data_abort_publication", `publications/${encodeURIComponent(id)}/abort`, {});
            setNotice("上传已取消，发布批次已中止。");
          } catch {
            setNotice("上传已取消，但中止结果无法确认；操作 ID 已保留，请手动检查。");
            throw new PlatformUiError(0, "PUBLICATION_ABORT_UNCERTAIN");
          }
        }
        return;
      }
      throw error;
    } finally {
      uploadController.current = null;
      setBusy(false);
    }
  };

  const cancelUpload = () => uploadController.current?.abort();

  const startNewPublication = () => {
    localStorage.removeItem(recoveryKey);
    setPublicationId("");
    setUrlPublicationId("", "replace");
    setCreatedAt("");
    setFiles([]);
    setOutputs([]);
    setNotice("");
    setUploadProgress({});
  };

  const commit = async () => {
    if (!publicationId) return;
    await api("data_commit_publication", `publications/${encodeURIComponent(publicationId)}/commit`, {});
    await server.refetch();
    await onPublished();
  };

  const abort = async () => {
    if (!publicationId) return;
    await api("data_abort_publication", `publications/${encodeURIComponent(publicationId)}/abort`, {});
    await server.refetch();
  };

  const deadlineStopped = Boolean(
    publicationId
    && (server.data?.createdAt || createdAt)
    && !TERMINAL.has(server.data?.state ?? "")
    && deadlineExceeded(server.data?.createdAt || createdAt, deadlineSeconds),
  );

  return {
    publication: server.data ?? null,
    publicationId,
    error: server.error,
    files,
    outputs,
    updateOutput,
    removeOutput,
    moveOutput,
    fileValidations,
    validationBlocked,
    uploadBytes,
    totalBytes: files.reduce((total, file) => total + file.size, 0),
    uploadProgress,
    manifest,
    busy,
    notice,
    chooseFiles,
    upload,
    cancelUpload,
    startNewPublication,
    commit,
    abort,
    deadlineStopped,
    checkNow: server.refetch,
  };
}
