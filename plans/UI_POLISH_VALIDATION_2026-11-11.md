# 2026-11-11 前端精修与增量回归报告

状态：PASS。当前前端门禁与冻结基线加三文件 Kernel 修复的完整隔离回归均已完成，覆盖范围分别记录如下。

## 范围与当前要求

基线为 `26f66732ffbb9609a155bf585448a5f4d7b21cd2`。实现沿用现有 Kernel、Data、Knowledge、Workflow 和 Executor；开发控制面与产品运行时代码保持分离。

用户最新决定覆盖此前的绿色管理页要求：暖白底、深灰文字、淡蓝点缀；减少重复解释；数据内容和知识正文优先，详情及编辑可以按需放在抽屉或弹框内。后续不再开展移动端专项开发，以桌面 1440/1366 为主要验收宽度。此前已完成的响应式修复保留，不作为新增开发目标。

## 功能与证据

| 区域 | 本轮行为 | 验证入口 |
| --- | --- | --- |
| 会话列表 | 搜索迟到响应隔离，重命名/归档防重，失败反馈，长标题提示 | `conversation-polish.test.tsx`、`app.test.tsx` |
| 首页模板 | 填入草稿有反馈，可撤销；后续手动编辑不会被撤销覆盖 | `conversation-polish.test.tsx` |
| 对话与成果 | 正文标题层级、可聚焦完整表格、简短版本和完整身份；新会话继续操作防重 | `message-content.test.tsx`、`card-review.test.tsx`、`app.test.tsx` |
| 上传与输入 | 会话作用域隔离、准备取消与错误重试，附件精确身份，发送状态对应实际条件 | `upload.test.tsx`、`conversation-polish.test.tsx` |
| 任务工作台 | 四类视图、键盘页签、显式导航焦点；没有选中会话时显示空状态 | `WorkspacePanel.test.tsx`、`workbench-state.test.tsx`、`workspace-parameter.test.tsx` |
| 数据库 | 切换版本立即撤下旧预览/附加；独立重试；比较先读元数据；图表随容器可见尺寸恢复 | `DatabaseWorkbench.test.tsx`、`workbench-state.test.tsx`、`build-viewers.spec.ts` |
| 知识证据 | 服务端搜索分页、返回保留列表位置和焦点，原版本/单元/checksum 精确匹配 | `knowledge.test.tsx` |
| Data 管理 | 暖白文档风格、内容主区、按需筛选/治理、查询与预览复用既有服务 | 25 单测、9 浏览器场景、类型与构建 PASS |
| Knowledge 管理 | 知识目录与正文阅读抽屉、按需版本/绑定/治理、历史版本保留 | 18 单测、11 浏览器场景、类型与构建 PASS |

主 Web 测试文件位于 `source/web/`，管理测试分别位于 `source/services/data/web/` 与 `source/services/knowledge-next/ui/`。生产浏览器使用合同有效的隔离 fixture，截图用于验证布局与操作，不冒充真实数据实测。

## 当前运行记录

| 检查 | 最近已完成结果 | 当前状态 |
| --- | --- | --- |
| `../bin/dev check --scope web` | 31 files / 238 tests，类型、构建、Ruff、diff 全部 PASS | 首页无会话状态回归已包含 |
| `npm --prefix web run test:e2e:build` | 15 PASS | 最终主 Web 构建与截图通过 |
| Data 管理单测/类型/生产浏览器 | 6 files / 25 tests、类型、构建和 9 browser tests PASS | 最终源码包含空目录重复提示收口 |
| Knowledge 管理单测/类型/生产浏览器 | 18 tests、类型、构建和 11 browser tests PASS | 最终源码包含正文抽屉、嵌套治理、关闭回焦点与任务切换保护 |
| 文档检查 | `DOCUMENTATION_CHECK_OK`，diff 检查退出码 0 | 当前 owning docs 已更新 |
| 独立真实案例 | 4 PASS | 已完成，详见 `REAL_DATA_VALIDATION_2026-11-11.md` |
| 冻结基线完整项目回归 | 14/14 阶段 PASS；2,515 passed / 4 条件 skip | Kernel 853、wheel、最终 Compose 全部通过 |

当前前端日志目录：`/home/mtd/.local/state/groundwater-v6/validation/ui-refine-20261111/`。测试截图及 Playwright JSON 位于 `/home/mtd/.cache/groundwater-v6/build/web/worktrees/678d0a28b0cf3f76/validation/` 下各 UI 的独立目录。

日志入口为 `web-owner-final.log`、`production-browser-final.log`、`data-*-final.log/json` 和 `knowledge-final-console.txt`。Knowledge 日志明确注明从最后实际工具结果转存，并保留原命令、退出码及输出块编号；没有为了补日志重新推算或伪造测试输出。

当前三个前端共 **281 项单元测试、35 个生产构建浏览器场景通过**。`frontend-source-snapshot.json` 保存本轮实际测试的 327 个前端源码、测试及合同文件 SHA-256；最终归档复核没有漂移。主 Web 的早期窄屏用例仍随既有测试套件执行，管理页新增场景已按最新要求改为桌面，未追加移动端功能。

最终桌面截图已复制到日志目录，避免后续构建覆盖：

- [Data 内容视图](/home/mtd/.local/state/groundwater-v6/validation/ui-refine-20261111/data-fixture-content-1440.png)
- [Knowledge 目录](/home/mtd/.local/state/groundwater-v6/validation/ui-refine-20261111/knowledge-directory-1440.png)
- [Knowledge 正文阅读](/home/mtd/.local/state/groundwater-v6/validation/ui-refine-20261111/knowledge-reader-1440.png)
- [Knowledge 生命周期弹框](/home/mtd/.local/state/groundwater-v6/validation/ui-refine-20261111/knowledge-governance-1366.png)
- [主 Web 首页](/home/mtd/.local/state/groundwater-v6/validation/ui-refine-20261111/web-home-1440.png)

Data 内容视图的每页上限为 100 行，沿用现有 Table Query 的计算范围和 deadline，并没有增加服务端仅计算 100 行的能力。GeoPackage 使用现有预览的表格与空间事实，不把这些元数据称作地图渲染。

## 完整回归发现与修复范围

隔离工作树 `/home/mtd/.cache/groundwater-v6/worktrees/real-cases-full-test-66010a1` 的实际 HEAD 为上述 `26f6673`。它仅应用以下三个候选文件，不混入主工作区同时进行的产品 6.1.0/远程服务变更：

- `kernel/src/platform_kernel/interaction/control_persistence.py`：空控制队列先只读检查；有可领取项时仍在写锁内重新检查，保留 FIFO、lease 与 fencing。
- `kernel/tests/test_task_controls.py`：持有其他写锁时的空队列回归以及双 claimant 只领取一次。
- `kernel/tests/test_agent_admission_recovery.py`：逐个建立八个实际就绪且仍阻塞的任务，再检查第九个排队与重启恢复；保留每次两秒上限，不调整生产超时。

历史 Compose 调度停在 `claim_controls -> BEGIN IMMEDIATE -> sqlite3.OperationalError: database is locked`。关闭隔离栈取证之后出现的只读/HTTP 500 属于后续现象。六轮并发探针没有重现历史锁持有者，因此不能把未知持锁来源表述为已证实的异步死锁。

admission 测试在同一 CPU 加五个竞争进程条件下修复前 2 FAIL、修复后 2 PASS；普通相邻测试 23 PASS。该变化只消除了已复现的批量准备计时脆弱性，不能据此推断历史 CPU 或 SDK 初始化的具体原因。

最终完整回归于 **2026-11-11 17:45:09（上海时间）**完成，Run ID 为 `ddec33fe6cc6`，14 个阶段全部 PASS，2,515 项通过、4 个显式条件 skip。最终 Compose 退出码 0，用时 742.068 秒；六模型生命周期 6/6、实时浏览器 16/16 通过。迁移学习和制图工作流均为 SUCCEEDED，运行态共观察到 10 个成功 WorkflowRun、17 个完成 Agent，Kernel readiness 为 200。

其中 2,515 是报告各阶段命令累计的通过记录，未跨阶段去重，不表示 2,515 个独立用例；前端本轮 281/35 的计数也不与它相加。

隔离项目 `platform-groundwater-e2e-45b99aaed62e` 的容器、网络、卷、本地镜像及临时目录均已回收；历史遗留测试数据库容器按已核对的精确 ID 删除，未使用全局 prune。现有部署没有修改，默认 Kernel 的最后只读健康核对通过。

4 个 skip 分别属于缺少独立官方 MF6 release 输入、未激活独立 FloPy 环境及两个未激活独立 Pastas 环境的条件测试。工程报告仍将科学有效性和真实 Agent/Provider 体验标为 `NOT_ASSESSED`；四个真实数据案例的数值结果与适用边界另见真实案例报告。

完整阶段报告和诊断均位于 `/home/mtd/.local/state/groundwater-v6/validation/real-cases-26f6673/`：

- [完整工程回归报告](/home/mtd/.local/state/groundwater-v6/validation/real-cases-26f6673/full-project-report.md)
- [机器可读阶段证据](/home/mtd/.local/state/groundwater-v6/validation/real-cases-26f6673/full-project-report.json)
- [三文件范围、运行态与清理证据](/home/mtd/.local/state/groundwater-v6/validation/real-cases-26f6673/three-file-closure-evidence.json)

报告基线与当前前端定向验收分别列出，不能合并为未运行的“全部当前工作区通过”，也不替同时进行的产品 6.1.0/第三方服务改动取得部署或科学验收结论。
