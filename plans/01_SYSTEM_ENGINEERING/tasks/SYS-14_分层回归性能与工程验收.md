# SYS-14：分层回归性能与工程验收

## 1. 目标
完成系统工程层面的单元、子系统、Workflow、E2E、性能和恢复验收。

## 2. 非目标
- 不重写整个平台，不创建平行服务、状态表或执行运行时。
- 不修改任务卡范围外的模块；发现必须扩大范围时先提交影响分析。
- 不将 NOT_RUN、UNKNOWN 或 SKIP 写成 PASS。

## 3. 源码范围
- `source/docs/operations/execution-playbook.md`
- `各模块 tests`
- `本包 progress/defect ledger`
- `性能与 E2E harness`

## 4. 前置依赖
SYS-03, SYS-04, SYS-05, SYS-06, SYS-07, SYS-08, SYS-09, SYS-10, SYS-11, SYS-12, SYS-13

## 5. Codex 执行指令
按执行手册运行适用全量检查。对大数据查询、GeoJSON 渲染、SSE 长任务、Worker 并发、重启恢复做基准。所有 NOT_RUN 和 SKIP 说明原因。不得以固定 Provider 测试替代真实 Provider 质量评估。

## 6. 测试命令
```bash
在 `source/` 下执行：`../bin/dev check --scope all`; `npm --prefix web test`; `npm --prefix web run typecheck`; `npm --prefix web run build`；其他命令按手册。
```
执行前必须核实本地脚本、参数、package scripts 和 toolchain。命令不可用时先读取项目执行手册，不得猜测替代命令。

## 7. 验收标准
工程回归通过；性能指标有基线和测量环境；恢复/并发一致性有证据；未运行项清楚；形成系统工程阶段报告。

## 8. 完成回报
- 本地 HEAD 与初始工作区状态；
- 当前问题是否仍存在及复现证据；
- 修改文件和关键实现；
- 测试命令、退出码、passed/failed/skipped；
- 科学验证结果（适用时）；
- NOT_RUN 项和原因；
- 进度表与缺陷台账更新。
