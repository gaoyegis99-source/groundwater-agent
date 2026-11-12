# SYS-01：本地基线与激活版本核验

## 1. 目标
确认当前本地产品、领域包版本、工具链和测试入口。

## 2. 非目标
- 不重写整个平台，不创建平行服务、状态表或执行运行时。
- 不修改任务卡范围外的模块；发现必须扩大范围时先提交影响分析。
- 不将 NOT_RUN、UNKNOWN 或 SKIP 写成 PASS。

## 3. 源码范围
- `source/products/groundwater/product-manifest.json`
- `当前激活 Domain Package manifest`
- `source/AGENTS.md`
- `source/docs/operations/execution-playbook.md`

## 4. 前置依赖
无；但必须完成本包的本地基线核验。

## 5. Codex 执行指令
只读检查 HEAD、git status、产品 manifest、实际激活领域包、工具链、可用测试 scope。把结果写入 `03_LOCAL_BASELINE.md`。不要修改源码。

## 6. 测试命令
```bash
`pwd`; `git rev-parse --show-toplevel`; `git rev-parse --short HEAD`; `git status --short`; `../bin/gw bootstrap --status`; `../bin/dev check --help`; `../bin/dev compile --help`。
```
执行前必须核实本地脚本、参数、package scripts 和 toolchain。命令不可用时先读取项目执行手册，不得猜测替代命令。

## 7. 验收标准
所有可读取基线字段有文件/命令证据；不确定项标 UNKNOWN；没有源码改动。

## 8. 完成回报
- 本地 HEAD 与初始工作区状态；
- 当前问题是否仍存在及复现证据；
- 修改文件和关键实现；
- 测试命令、退出码、passed/failed/skipped；
- 科学验证结果（适用时）；
- NOT_RUN 项和原因；
- 进度表与缺陷台账更新。
