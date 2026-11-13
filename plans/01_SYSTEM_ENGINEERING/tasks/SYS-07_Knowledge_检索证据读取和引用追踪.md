# SYS-07：Knowledge 检索证据读取和引用追踪

## 1. 目标
确保检索候选、完整读取、checksum 和最终引用链路可信。

## 2. 非目标
- 不重写整个平台，不创建平行服务、状态表或执行运行时。
- 不修改任务卡范围外的模块；发现必须扩大范围时先提交影响分析。
- 不将 NOT_RUN、UNKNOWN 或 SKIP 写成 PASS。

## 3. 源码范围
- `source/services/knowledge-next/`
- `source/kernel/src/platform_kernel/agent_runtime/evidence.py`
- `Kernel reply validation`
- `Knowledge tests`

## 4. 前置依赖
SYS-01, SYS-02

## 5. Codex 执行指令
核查 retrieve 与 read 的区别、版本状态、checksum、STALE、权限失败和引用校验。只有本轮完整读取且 OK 的证据才可引用。修复错误状态和证据丢失问题。

## 6. 测试命令
```bash
Knowledge tests；Kernel evidence/reply tests；`../bin/dev check --scope knowledge`（核实 scope）。
```
执行前必须核实本地脚本、参数、package scripts 和 toolchain。命令不可用时先读取项目执行手册，不得猜测替代命令。

## 7. 验收标准
未经完整读取的片段不能作为已引用证据；STALE 不替换原正文；引用精确绑定 version/unit/checksum。

## 8. 完成回报
- 本地 HEAD 与初始工作区状态；
- 当前问题是否仍存在及复现证据；
- 修改文件和关键实现；
- 测试命令、退出码、passed/failed/skipped；
- 科学验证结果（适用时）；
- NOT_RUN 项和原因；
- 进度表与缺陷台账更新。
