# SYS-09：Executor 节点状态恢复与成果发布

## 1. 目标
验证 Worker 调度、节点状态、重试恢复、Artifact 和 Data publication。

## 2. 非目标
- 不重写整个平台，不创建平行服务、状态表或执行运行时。
- 不修改任务卡范围外的模块；发现必须扩大范围时先提交影响分析。
- 不将 NOT_RUN、UNKNOWN 或 SKIP 写成 PASS。

## 3. 源码范围
- `source/services/executor/`
- `source/kernel/src/platform_kernel/workflow_execution/`
- `Executor client/contracts`
- `Executor tests`

## 4. 前置依赖
SYS-08

## 5. Codex 执行指令
跟踪从确认到 enqueue、node attempt、worker lease、result finalization、publication 和 Kernel projection 的调用链。检查 crash/retry/idempotency 和部分失败。

## 6. 测试命令
```bash
Executor tests；Kernel workflow execution tests；`../bin/dev check --scope executor`（核实）。
```
执行前必须核实本地脚本、参数、package scripts 和 toolchain。命令不可用时先读取项目执行手册，不得猜测替代命令。

## 7. 验收标准
重启后任务状态可恢复；重试不重复发布；失败节点可定位；成功成果具有 checksum/lineage；用户确认前不执行。

## 8. 完成回报
- 本地 HEAD 与初始工作区状态；
- 当前问题是否仍存在及复现证据；
- 修改文件和关键实现；
- 测试命令、退出码、passed/failed/skipped；
- 科学验证结果（适用时）；
- NOT_RUN 项和原因；
- 进度表与缺陷台账更新。
