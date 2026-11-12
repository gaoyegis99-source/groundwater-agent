# SYS-02：架构边界与接口合同审计

## 1. 目标
明确系统模块职责、状态所有权和公共合同一致性。

## 2. 非目标
- 不重写整个平台，不创建平行服务、状态表或执行运行时。
- 不修改任务卡范围外的模块；发现必须扩大范围时先提交影响分析。
- 不将 NOT_RUN、UNKNOWN 或 SKIP 写成 PASS。

## 3. 源码范围
- `source/docs/architecture/architecture-overview.md`
- `source/docs/architecture/architecture-contract.md`
- `source/kernel/`
- `source/services/data/`
- `source/services/knowledge-next/`
- `source/services/executor/`
- `source/packages/workflow-compiler/`

## 4. 前置依赖
SYS-01

## 5. Codex 执行指令
画出现有真实调用链与状态 owner。检查 OpenAPI、DTO、manifest、生成类型和服务端实现是否一致。只登记证据，不做大规模重构。需要改公共合同时另立任务并报告兼容影响。

## 6. 测试命令
```bash
`../bin/dev compile --product groundwater`; `../bin/dev check --scope <核实后的范围>`; `git diff --check`。
```
执行前必须核实本地脚本、参数、package scripts 和 toolchain。命令不可用时先读取项目执行手册，不得猜测替代命令。

## 7. 验收标准
每个状态对象有唯一 owner；公共合同不出现未经解释的漂移；跨服务依赖图有证据；没有创建第二套状态源。

## 8. 完成回报
- 本地 HEAD 与初始工作区状态；
- 当前问题是否仍存在及复现证据；
- 修改文件和关键实现；
- 测试命令、退出码、passed/failed/skipped；
- 科学验证结果（适用时）；
- NOT_RUN 项和原因；
- 进度表与缺陷台账更新。
