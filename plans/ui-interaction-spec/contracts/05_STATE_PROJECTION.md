# 状态投影合同
前端仅将 Kernel/Executor/Data 状态映射为 label/tone/actions，不创建业务状态机。

Codex 必须按真实枚举补全：
| 对象 | 原始字段/枚举 | UI label | tone | 可用操作 | owner |
| Session | 待审计 | 待映射 | neutral | open/rename/archive | Kernel |
| Task lifecycle/state | 待审计 | 待映射 | neutral | clarify/review | Kernel |
| Turn | 待审计 | 待映射 | neutral | view/stop | Kernel |
| Confirmation | 待审计 | 待映射 | warning/success/danger | edit/confirm/reject | Kernel |
| WorkflowRun | 待审计 | 待映射 | neutral | view/stop/recover | Executor/Kernel |
| Publication | 待审计 | 待映射 | neutral | preview/download | Data |

SSE 负责及时通知，snapshot 负责最终核对和断线恢复。unknown enum 不默认成功；状态不确定显示待核对；收到终态事件后刷新权威快照；session switch 隔离旧事件。
