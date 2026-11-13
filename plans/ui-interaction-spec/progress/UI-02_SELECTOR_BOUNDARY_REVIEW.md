# UI-02 Selector 补充审查

基线 `f0b9f6d6d5e1df970794d2cd16b9cdcec387b7fc`，以工作区已有 UI-02 实现为起点。此次只修改纯投影和相应测试、设计及进度文档。

| 问题 | 判定 | 源码证据与处理 |
|---|---|---|
| 1. Results 四种读取状态被合并 | **成立，已修正。** | 原接口用可选数组，省略值和已读空数组都归为同一个空 Map，无法表达加载中或失败。新输入显式区分 `not-requested/loading/loaded/failed`，省略默认未请求，只有 loaded 可携带数据。调用方在 [allResults](../../../source/web/src/api.ts:395) 完整分页返回后才能提供 loaded；目前 [use-session-stream](../../../source/web/src/use-session-stream.ts:142) 的数组缓存仍由现有页面使用。 |
| 2. 正 outputCount 的 SUCCEEDED Run 把未加载误判为待同步 | **成立，已修正。** | 只有 Results 已加载而且缺少与 runId、agentRunId、toolCallId、goalRevision 完整匹配的有效输出时才显示「成果待同步」。未请求、读取中和读取失败各自显示，保留 SUCCEEDED 和科学验证未知。R 是最新尝试的投影，历史尝试要用 W 查询，重复读取 R 不保证补齐历史引用。[R 查询](../../../source/kernel/src/platform_kernel/agent_runtime/persistence/context.py:239) |
| 3. 完成 Task 要求非空 criteria | **符合合同，保留。** | 首次创建必须提交至少一个 criterion，否则 Kernel 返回 `TASK_CRITERIA_REQUIRED`。修订允许本次更新传空列表，但它保留现有条目；替换会同时加入新条目。空 criteria 的完成快照违反正常写入合同，Selector 继续回退待核实。[创建规则](../../../source/kernel/src/platform_kernel/tasks/persistence.py:190) · [修订规则](../../../source/kernel/src/platform_kernel/tasks/persistence.py:60) |
| 4. 空 steps 通过 every | **符合合同，保留。** | Task 初始化允许 steps=[]，Kernel 只对已声明的未完成步骤阻止完成。非空且缺对应完成检查时仍回退。现有服务端测试用未声明 steps 的 Task 走到 COMPLETE。[任务完成投影](../../../source/kernel/src/platform_kernel/tasks/progress.py:34) · [Kernel 回归](../../../source/kernel/tests/test_tasks.py:42) |
| 5. 预览和下载需验证 Data 可读性 | **成立，已修正。** | R 引用是已发布的身份记录。调用方须从当前 project/用户/会话访问范围按精确版本、对象、语义及 checksum 等核验，再传入 `verifiedOutputs`；失败、身份变化或过期时移除验证事实。Selector 逐项提供 `outputAccess`，仅全量通过时提供整批操作。验证不撤销历史发布记录；真正取内容时 Data 仍重新鉴权，可能返回错误。[精确引用核验](../../../source/web/src/api.ts:245) · [内容端点](../../../source/services/data/src/platform_data_service/api/routers/content.py:92) |
| 6. check-recovery 只是资格检查入口 | **成立，现有实现正确。** | Selector 只提供候选。Kernel 重新校验当前会话、目标修订、最近尝试、待确认冲突及冻结批次。现有 recovery POST 通过校验会创建新的待确认批次，具备持久副作用，纯资格查询 API 仍缺失。批准新 Confirmation 后才进入新尝试。[源运行校验](../../../source/kernel/src/platform_kernel/workflow_execution/persistence/queue.py:175) · [恢复批次创建](../../../source/kernel/src/platform_kernel/interaction/recovery.py:45) |

## 复现与改动

保留原 19 项 Selector 用例及覆盖。其中已发布成果用例补充 Data 核验输入。新增 9 项覆盖成果读取四态、未知读取值、逐成果核验、无业务步骤、异常完成快照、恢复候选边界。改前新增测试为 6 失败、22 通过。未请求场景实际被误显示为待同步，原数组入口也无法接收显式读取状态。改后保留 RUNNING/PUBLISHING、原状态未知回退、停止与取消分离，以及旧确认无审批操作。[复现测试](../../../source/web/src/ui-selectors.test.ts)

本轮文件为 [Selector](../../../source/web/src/ui-selectors.ts)、[单测](../../../source/web/src/ui-selectors.test.ts)、[Selector 设计](../contracts/UI_SELECTOR_DESIGN.md)、[状态映射](../contracts/STATE_MAPPING.md)、本报告及 [PROGRESS](PROGRESS.md)。

## 测试结果与后续条件

- `npm --prefix source/web test -- src/ui-selectors.test.ts` 退出 0，28 项通过。显式纳入测试文件的 TypeScript 检查退出 0。
- 锁定 Python 的 `python -m pytest -q kernel/tests/test_tasks.py kernel/tests/test_confirmation_recovery.py kernel/tests/test_session_results.py` 退出 0，57 项通过。
- Web 门禁、文档和工作区核验结果见 [PROGRESS](PROGRESS.md)。浏览器 E2E 和真实服务内容读取未运行，页面仍未接入 Selector。

UI-03 的 UI-01/UI-02 依赖已满足。完整研究列表仍需 G01 的最近活动、主任务和待处理状态目录投影，或用户明确批准按现有合同缩减范围。两种路径都需用户批准开始 UI-03。后续接入时继续使用项目/用户/会话隔离，Results 只以完整分页成功标记 loaded，预览与下载使用当前 Data 核验事实。这些约束不要求 UI-03 实现成果页。[G01](../contracts/CONTRACT_GAPS.md)
