# 11 Task、Activity、WorkflowRun 与结果复用

## 当前事实
WebUI 文档要求进度来自持久化 Plan、Confirmation、WorkflowRun 事实投影。主 Web 已有 `TaskCard.tsx`、`ActivityPanel.tsx`、`TaskUpdateCard`、`WorkspacePanel.tsx`、`ResultCard`。
`use-session-stream.ts` 负责 SSE、snapshot、workflow drafts、results。

## PROG-01 修复结果覆盖（P0）
文件：`source/web/src/use-session-stream.ts`。
当前 `allResults` 合并逻辑仅在旧 result 不存在或旧 result.remoteOutputs 为空时写入新结果。旧结果已含 outputs 时，新 attempt 的 outputs 不会覆盖。
实施：
1. 确认 Result 类型是否含 attempt/revision/updatedAt/WorkflowRun id。
2. 以服务端权威结果排序，优先更高 attempt 或明确新版本。
3. 不能简单按数组到达顺序覆盖。
4. 同一 workflowRun 的 output set 要原子替换，不能把两次 attempt 的 outputs 混在一起。
5. 对迟到的旧响应做 generation/request guard。

验收：旧 attempt 有输出，新 attempt 完成后显示新输出；迟到旧响应不能覆盖新 attempt。

## PROG-02 草稿缓存失效（P0）
同文件 `draftCache` 筛选会跳过所有非 GENERATING 已缓存草稿。
实施：
1. 明确 workflow_draft.requested / published 等事件何时使缓存失效。
2. 对 terminal draft 只在明确 dirty 时重取，不要无条件每次轮询。
3. 用 draft status/version/updatedAt 做更新判断。
4. 会话切换和 SSE reconnect 时重建权威快照。

验收：同一 draft 状态从 GENERATING→COMPLETE/FAILED 可见；重新生成的新 draft 不被旧缓存覆盖。

## PROG-03 Activity 文案（P1）
服务端拥有 progress semantics，Web 只投影 Activity。不要在前端根据 tool name 猜用户阶段。
完善：
- 总体阶段优先
- 当前 action / workflow 状态
- 等待确认与等待执行区分
- 失败原因和下一步
- 结果同步中与执行完成区分
- 调试信息只在显式 debug 模式

## PROG-04 失败与恢复（P1）
展示失败 node、错误码、已跳过下游节点、attempt、恢复批次。重试入口必须说明恢复范围。状态从服务端查询，不能本地推断已恢复。

## RESULT-01 多格式结果卡（P1）
按输出 mediaType/format 使用现有 ArtifactViewer：
- CSV/Parquet → TableViewer/ChartView
- GeoJSON/GPKG → GeoViewer/GeoPackagePreviewViewer
- GeoTIFF/COG → RasterViewer
- PNG → image preview
- JSON → structured preview
保持下载、作为下一步输入、在新会话继续、与另一版本比较。

## RESULT-02 结果比较（P2）
比较前检查格式、CRS、grid transform、时间范围、单位和字段语义。不可比时先提示，不静默相减。
