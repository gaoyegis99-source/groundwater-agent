# UI-11 成果详情与评审

基线 `f0b9f6d`。会话成果、版本和对象读取沿用公开合同。新增成果复核区，在 Data 精确身份通过后展示现有描述、适用限制和字段质量；运行、已读取成果版本与科学验证分别显示。运行来源仅在精确引用唯一匹配时显示。按格式选择的 viewer、血缘、下载、比较和新会话动作沿用现有入口。

通用科学验证受 G08 限制。Data 的字段统计与质量规则各有自身口径。Data Quality Run 属于数据集、版本与语义的检查，公开合同无科研 WorkflowRun 验证关系。成果面板的科学验证保留未提供。成果附加到新会话只创建空 Session 和带精确引用的本地草稿。

修改 `WorkspacePanel.tsx`、`workbench-polish.css`、`e2e/build-result-formats.spec.ts` 和进度。新增浏览器断言先复现科学验证显示缺失；修复后验证 12 种格式的预览、下载校验和、比较和复用，并检查新会话无旧运行或确认。生成 1440×900 和 390×844 截图。浏览器使用合同兼容响应。Kernel 成果、Data 质量和 Executor 发布合计 9 项通过。

- `../bin/dev check --scope web` 退出 0，371 项、类型、生产构建和 Ruff 通过。生产 Chromium 全量 30 项通过。真实跨服务浏览器测试待 UI-13。

## 授权复核

本地 HEAD 为 `f0b9f6d6d5e1df970794d2cd16b9cdcec387b7fc`。开始时有 52 个已修改的跟踪文件和 11 项未跟踪记录。本轮增量修改现有工作区。

- Kernel `agent_runtime/persistence/context.py::list_session_results` 按会话和目标修订分页，输出来自 action 最新尝试的 `workflow_runs.outputs_json`。 `projection.py::action_status` 将成功运行映射为结果记录的 COMPLETE。Task `progress.py::_result_progress` 独立校验已有交付证据所指向的成功运行。成果复核区继续按完整引用匹配唯一可见运行。
- Data `catalog/service.py::describe` 校验版本读取权限并返回对象描述、语义和输入血缘。 `readDataVersion` 核对数据集、版本、对象、校验和、格式、MIME、大小和精确语义修订。
- 成果列表根据本次读取状态展示加载、失败、重试或已加载的成果。刷新时精确引用仍可用于读取预览。详情页在取得发布证据并完成 Data 身份核验后提供下载、比较和复用。查看器内的下载按钮由详情页统一接管。通用科学验证继续显示未提供，用户评审由现有 Task criteria 记录。
- 本轮修改 `source/web/src/{App,WorkspacePanel,WorkspacePanel.test,ArtifactViewer}.tsx`、`source/web/src/viewers/{BasicViewers,GeoPackagePreviewViewer,XlsxPreviewViewer}.tsx`、`source/web/e2e/{build-result-formats,v12-interaction-isolated}.spec.ts`、本报告、`PROGRESS.md` 和 `DECISIONS.md`。

### 验证

- 两项新增回归先分别失败，复现读取失败时使用旧成果和精确预览提前暴露下载入口。
- `npm --prefix source/web test -- src/WorkspacePanel.test.tsx src/ArtifactViewer.test.tsx src/artifact-viewer-metadata.test.tsx src/viewers/XlsxPreviewViewer.test.tsx` 退出 0，4 文件、30 项通过。
- 从 `source/` 执行 `../bin/dev check --scope web` 退出 0，38 文件、396 项通过。TypeScript、生产构建、Ruff 和差异空白检查通过。
- 从 `source/` 用锁定 CPython 执行 `python -m pytest -q --basetemp "$(mktemp -d)" kernel/tests/test_session_results.py services/data/tests/test_quality_control.py services/executor/tests/test_control.py -k 'session_results or quality or publication or published_remote'`，退出 0，13 项通过、48 项由表达式排除，覆盖成果归属、Data 质量及取消与发布竞争。
- `npm --prefix source/web run test:e2e:build` 退出 0，生产 Chromium 33 项通过。成果格式用例使用合同兼容的响应和实际文件字节，校验 12 种格式只有一个成果下载入口，并验证校验和、版本比较、精确附件和新会话隔离。
- 从 `source/` 执行 `../bin/dev check --scope contracts`。首次在 SDK 检查阶段退出 143。复跑退出 0，公共合同 190 项及运行时 OpenAPI、TypeScript、Python SDK 检查通过。
- 从 `source/` 用锁定 CPython 执行 `python -m scripts.checks.check_compose_e2e --product groundwater --interaction-only --webui-report <证据目录>`，退出 0。Playwright 6 项通过，0 失败、跳过或 flaky。真实 Gateway、Kernel、Executor、Data 使用合成观测与固定本地 Provider。浏览器从真实成果详情创建空会话，核对服务端任务、确认、运行和消息均为空，并验证待发送附件沿用确切的版本、语义、对象和校验和。证据在 `ui-11-isolated/platform-groundwater-e2e-b179919cad15/gate.json`，附桌面 1440×900、窄屏 390×844 及新会话截图。
- G05/G06 的旧尝试完整输出和实时发布子状态、G08 的通用科学验证、G15 的逐条件完整证据仍需后端合同。外部模型 Provider 与完整生命周期门禁为 NOT_RUN。
