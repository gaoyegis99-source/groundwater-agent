# SYS-05：上下文、路由和工具调用边界验证

## 1. 目标
确保意图路由、上下文编译和工具输入输出在跨轮次时保持准确。

## 2. 非目标
- 不重写整个平台，不创建平行服务、状态表或执行运行时。
- 不修改任务卡范围外的模块；发现必须扩大范围时先提交影响分析。
- 不将 NOT_RUN、UNKNOWN 或 SKIP 写成 PASS。

## 3. 源码范围
- `source/kernel/src/platform_kernel/intent_routing/`
- `source/kernel/src/platform_kernel/agent_runtime/context*`
- `source/kernel/src/platform_kernel/agent_runtime/tooling/`
- `相关 tests/replay cases`

## 4. 前置依赖
SYS-04

## 5. Codex 执行指令
检查上下文是否混入过期数据版本、旧 confirmation、旧 evidence；检查工具参数归一化、schema 验证和错误回传。增加普通对话/咨询/草稿/执行意图边界测试。

## 6. 测试命令
```bash
Kernel 相关 pytest；`../bin/dev agent-replay --help` 和对应 case；范围 check。
```
执行前必须核实本地脚本、参数、package scripts 和 toolchain。命令不可用时先读取项目执行手册，不得猜测替代命令。

## 7. 验收标准
上下文来源和生命周期明确；工具输入符合合同；旧证据不冒充当前轮；意图边界无意外执行。

## 8. 完成回报
- 本地 HEAD 与初始工作区状态；
- 当前问题是否仍存在及复现证据；
- 修改文件和关键实现；
- 测试命令、退出码、passed/failed/skipped；
- 科学验证结果（适用时）；
- NOT_RUN 项和原因；
- 进度表与缺陷台账更新。
