# 2026-11-11 工作流与任务专项测试

状态：PASS（工作流与任务专项）。测试当前源码中的工作流编排、任务执行及前后端衔接，复现并修复实际问题；此前冻结基线报告只作历史证据。

## 验收矩阵

| 范围 | 需要证明的行为 | 证据与当前状态 |
| --- | --- | --- |
| 编排与草稿 | 分支汇合可编译，错误端口/参数/循环明确拒绝，草稿不产生执行授权，精确数据身份不替换 | PASS：Compiler 62 项、Web 草稿回归与合同检查 |
| 确认与控制 | 冻结内容才可执行，重复请求不产生额外执行，过期修订/会话响应不会污染新上下文 | PASS：Kernel 867 项、Web 270 项及真实确认链路 |
| 执行与发布 | 排队、运行、发布、失败/取消/超时状态真实；失败传播正确，发布结果才算成功 | PASS：Executor 173 项、Native 160 项及隔离发布链路 |
| 租约与恢复 | 短暂争锁后继续推进，过期 actor 不能取消新 owner 的执行，重启不丢已提交进度 | PASS：真实 SQLite 争锁及 Control/Worker 租约回归；隔离栈重启 |
| 进度与结果 | SSE 事件实际被监听，结果随权威 attempt 更新，单个草稿失败不阻塞其他草稿 | PASS：53 种生产事件核对，Web 回归与桌面恢复用例 |
| 系统链路 | 当前产品装配下任务建立/修订/重启、确认执行/结果恢复和桌面操作贯通 | PASS：当前 6.1 装配，任务协作、6 个模型生命周期及实时浏览器 |

## 工作区与测试边界

HEAD 为 `26f66732ffbb9609a155bf585448a5f4d7b21cd2`，测试对象还包括本轮开始时已有的未提交源码。当前产品选用 6.1.0 与包含两个 remote service 的 UI profile；不切回旧产品来获得通过。并行任务保护彼此及其他已有改动。

复用既有 Kernel、Workflow Compiler、Executor、Data、SSE 和公开 API，没有新增平行执行系统。Provider 固定夹具只用于验证控制链路，不表示真实模型意图识别准确率或科学计算适用性。移动端专项开发不属于本轮。

原始日志、复现测试输出、运行态采样和最终证据保存在 `/home/mtd/.local/state/groundwater-v6/validation/workflow-task-20261111/`。报告保留红测和最终结果，未评估项目见下文；各套件有重叠，不把计数相加。

## 已确认问题与处理

| 问题 | 处理及回归重点 |
| --- | --- |
| SSE 使用了错误的事件名称，部分超时/恢复结果未重读 | 按后端真实事件名订阅；独立核对 53 种生产事件，终态、重试与恢复均刷新相应投影 |
| 停止、修订的迟到响应污染另一个会话 | 只允许原会话与原视图 generation 接收操作结果；确认重复点击、失败重试另有正向验证 |
| 恢复按钮选择历史任务或错误的下游步骤 | 限定当前 turn 的最新 attempt，按冻结 confirmation 的顺序选择恢复源与范围 |
| 草稿读取相互阻塞、停止后仍显示生成中 | 每份草稿独立加载与重试，取消/替代事件使缓存重读，不因一个失败阻塞其他草稿 |
| Kernel 争抢写锁时调度任务退出，已领取的停止租约可能丢失 | 只在尚未开始事务的 SQLITE_BUSY 上延后本轮；保留已领取项，程序错误继续暴露 |
| 过期停止 actor 误取消新 turn，排队停止后仍可能被领取 | finalization 校验失败时不触发本地取消；DIALOGUE/AUTO 的 inventory 和原子 claim 均复核停止状态 |
| 旧 Executor 租约取消已由新 attempt 接管的远端操作 | 只有权威 Job 终态允许取消；重启/租约失效保留远端操作并由新 attempt 续接 |
| 远端 checkpoint 租约失效导致整个 Worker 退出 | 在 attempt 边界识别明确的失效信号，其他 slot 与后续领取继续；正常 shutdown 保持原义 |
| 503 BUSY 响应为数组或非法 JSON 时掩盖原始错误 | 仅对有效对象中的指定 BUSY 码重试，其他响应保留原始 HTTP 失败 |
| 当前双远程服务装配在验收环境缺少绑定 | 从已装配 executor config 创建所有惰性绑定和测试 secret；当前 6.1 装配预检通过 |
| 派生产品合同夹具仍继承旧 UI 路径或版本计数 | 记录 22 个失败，按当前产品与独立 Air Quality 夹具分别修正预期，保留审批、产品隔离和精确身份约束 |

这组改动没有修改科学计算公式，也没有将模拟远端协议测试解释为真实远程模型的科学验证。两个真实并发取消窗口未复现新缺陷，保留现有 TaskSupervisor，而不是据此加入额外调度抽象。

## 最终验证结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| Kernel 完整 owner | PASS，867 项；类型、Ruff、架构边界通过 | `kernel-owner.log`、`kernel-task-findings.json` |
| Executor / Compiler / Native | PASS，分别 173、62、160 项 | `executor-owner-final.log`、`compiler-final.log`、`executor-owner-with-native.log` |
| Web 单测、类型及构建 | PASS，270 项 | `web-owner-final-test.log`、`web-owner-final-typecheck.log`、`web-owner-final-build.log` |
| 跨服务合同 | PASS，183 项；OpenAPI、SDK 检查通过 | `contracts-owner-final.log`、`contracts-owner-final.xml` |
| 当前产品装配与隔离集成 | PASS；桌面生产构建 15 项、真实服务浏览器 16 项；六个模型生命周期及固定 Provider 任务协作 9 项通过 | `integration-owner-attempt-1.log`、`integration-attempt-1/` |
| 双远程绑定测试 | PASS，19 项；当前装配预检通过 | `compose-bindings-green.log`、`assembly-preflight.json` |

上述文件均位于 `/home/mtd/.local/state/groundwater-v6/validation/workflow-task-20261111/`。合同检查最初 22 项失败，修正派生产品夹具后完整重跑通过；历史红测保留。隔离集成使用随机 Compose 项目并完成清理。1592 个集成启动快照文件与当前文件的 SHA-256 一致。Kernel owner 运行中其他工作调整了两个测试等待时间；原版本在全套中通过，现行版本另两项通过，不重复计数。

桌面恢复浏览器用例使用模拟服务快照与 SSE；真实服务浏览器另外验证确认、发布、下载和刷新恢复。固定 Provider 和模拟远程协议不能证明真实模型意图识别表现或远程服务可用性；当前 18 个模型中只有六个经过本次隔离生命周期验收，真实场地科学有效性、真实 Provider、两个真实远程模型和移动端专项均为 NOT_ASSESSED。本专项报告不替代当前源码的全产品发布验收。历史 SQLite 持锁来源仍未确定；本次修复和验证覆盖已定位的队列事务争锁。

Executor 两份 owner 日志中的测试分别通过，但当时共享静态门禁受到并行 Kernel/Web 修改阻断；随后最终 Kernel owner 的 Ruff、Core imports 和 Architecture boundaries 以及 integration owner 的 Ruff/diff 均通过。Native 测试另有 38 条来自 rasterio/affine 的上游 PendingDeprecationWarning，未造成失败。隔离项目的容器、网络、卷和镜像均确认无残留；快照范围内无新增、删除或修改文件。

后续模型验收已补齐本报告保留的十二个模型生命周期，并复验真实 HTTPS 服务与真实数据。参见 [模型端到端验收报告](MODEL_E2E_VALIDATION_2026-11-11.md)。
