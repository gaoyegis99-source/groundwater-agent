import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { PlatformUiError, type Version } from "./api";
import { SemanticEditor } from "./SemanticEditor";

it("blocks duplicate semantic writes and preserves the draft after a rejected save", async () => {
  const user = userEvent.setup();
  const version: Version = {
    versionId: "v1", semanticId: "s1", currentVersion: "v1", datasetId: "d1", publicationId: "p1",
    project: "project-1", title: "监测数据", createdAt: "2026-10-17T10:00:00Z", inputVersions: [], objects: [], tags: [],
    descriptor: { fields: [{ fieldId: "f0", name: "井号", type: "string", nullCount: 0 }], quality: {}, rowCount: 1 },
    semantics: { description: "井号", source: "监测台账", fields: { f0: { meaning: "井号", unit: null } } },
  };
  let rejectSave: (error: Error) => void = () => {};
  const onSave = vi.fn(() => new Promise<void>((_resolve, reject) => { rejectSave = reject; }));
  render(<SemanticEditor version={version} allowed onSave={onSave} />);
  await user.clear(screen.getByRole("textbox", { name: "描述" }));
  await user.type(screen.getByRole("textbox", { name: "描述" }), "监测井水位");
  await user.click(screen.getByRole("button", { name: "预览修订" }));
  await user.click(await screen.findByRole("button", { name: "创建语义修订" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
  expect(screen.getByRole("button", { name: "正在保存…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "返回编辑" })).toBeDisabled();
  await act(async () => rejectSave(new PlatformUiError(409, "SEMANTIC_VERSION_CONFLICT")));
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.getByRole("textbox", { name: "描述" })).toHaveValue("监测井水位");
  expect(screen.getByRole("button", { name: "创建语义修订" })).toBeEnabled();
});
