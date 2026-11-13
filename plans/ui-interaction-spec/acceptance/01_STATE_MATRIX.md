# 状态矩阵（需按本地后端枚举校准）

| 场景 | Session | Task | Turn | WorkflowRun | UI |
|---|---|---|---|---|---|
| 空会话 | active | none | none | none | 输入研究问题 |
| 普通咨询 | recent | none | complete | none | 继续问答 |
| 澄清 | active | waiting input | waiting | none | 回答问题 |
| 数据准备 | active | preparing | running/complete | none | 查看数据质量 |
| Draft | active | planning | complete | none | 修改方案 |
| 待确认 | waiting user | waiting confirmation | complete | none | 编辑/确认/拒绝 |
| 执行中 | active | executing | terminal/running as actual | running | 进度/停止请求 |
| 执行失败 | attention | blocked/open | failed | failed | 失败详情/恢复 |
| 停止请求中 | active/unknown | executing | running | cancel pending | 核对状态 |
| Run 成功发布中 | active | publishing | complete | succeeded | 同步成果 |
| 发布完成 | review | waiting review/open | complete | succeeded | 成果/评审 |
| Task 完成 | completed | complete + criteria satisfied | terminal | complete | 归档/新研究 |

此表是场景说明，不是新后端枚举。
