# E2 Agent 运行机制

任务：SYS-04、SYS-05。

重点：
- 路由和澄清边界。
- 上下文构建、历史恢复和版本隔离。
- 工具发现、调用参数和返回值合同。
- Confirmation 修订、拒绝、恢复。
- 长任务、SSE、snapshot reconciliation 和失败恢复。

完成标准：Agent 不越过执行授权边界；恢复后状态与持久化事实一致；错误能稳定呈现。
