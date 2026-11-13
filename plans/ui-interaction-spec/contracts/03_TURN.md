# Turn UI Contract
用户心智：一次用户输入触发的一轮 Agent 协作。Turn 可没有 Task 或 WorkflowRun。
展示：用户输入/附件、公开 Activity 摘要、正式回复、澄清卡、Draft/Confirmation/Result/Error（仅真实存在时）。
不展示隐藏推理、原始工具轨迹、模型请求次数。临时流式正文必须标记尚未核验；正式回复通过校验后替换。队列/提交/回执待核对状态明确区分。
