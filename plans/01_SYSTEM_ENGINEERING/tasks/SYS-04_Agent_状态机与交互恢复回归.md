# SYS-04：Agent 状态机与交互恢复回归

## 1. 目标
验证 Agent/Session/Confirmation/Task 的状态机、恢复和异常边界。

## 2. 非目标
- 不重写整个平台，不创建平行服务、状态表或执行运行时。
- 不修改任务卡范围外的模块；发现必须扩大范围时先提交影响分析。
- 不将 NOT_RUN、UNKNOWN 或 SKIP 写成 PASS。

## 3. 源码范围
- `source/kernel/src/platform_kernel/agent_runtime/`
- `source/kernel/src/platform_kernel/interaction/`
- `source/kernel/tests/`

## 4. 前置依赖
SYS-01, SYS-02

## 5. Codex 执行指令
围绕普通咨询、执行提案、确认修订、拒绝、恢复、重复请求、并发和 Provider 失败建立状态矩阵。先补失败测试，再做最小修复。不得改变确认授权边界。

## 6. 测试命令
```bash
`../bin/dev check --scope kernel`（先核实 scope）；`../bin/dev agent-replay --help` 后执行适用 case。
```
执行前必须核实本地脚本、参数、package scripts 和 toolchain。命令不可用时先读取项目执行手册，不得猜测替代命令。

## 7. 验收标准
普通对话不创建 Job；执行必须经过确认；恢复后状态与持久记录一致；重复提交不重复执行；失败不伪装成功。

## 8. 完成回报
- 本地 HEAD 与初始工作区状态；
- 当前问题是否仍存在及复现证据；
- 修改文件和关键实现；
- 测试命令、退出码、passed/failed/skipped；
- 科学验证结果（适用时）；
- NOT_RUN 项和原因；
- 进度表与缺陷台账更新。
