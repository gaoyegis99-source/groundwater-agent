# 实体关系与归属
Project → Session → Turns[]；Session → PrimaryTask?；Task → GoalRevisions / Criteria / BusinessSteps / Blockers / WorkflowRuns[]；WorkflowRun → Attempts / NodeFacts / PublishedResources[]。
这是 UI 关系，不要求创建同名数据库表。
消息归属 Turn；Task 归属 Session；Draft 归属产生它的 Turn/goal revision；Confirmation 归属冻结批次；WorkflowRun 归属实际执行上下文；Resource 通过精确版本/对象/checksum/lineage 关联。
禁止仅按 modelId 将结果归入最新 Turn；禁止仅按文件名去重；禁止新 Session 复制旧确认。
