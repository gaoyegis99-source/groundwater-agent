# WorkflowRun UI Contract
用户心智：一次经过授权的真实专业计算运行。关联字段按 API 核验：runId、task/session/goalRevision、confirmation、workflow/model version、input references、parameter snapshot、status/timestamps、attempts/node facts、outputs/publication、recovery lineage。
Draft != Confirmation != WorkflowRun != Publication。Run success != publication complete != scientific validation passed。Stop requested != cancelled。节点状态来自 Executor 持久事实。恢复需新批次并遵守新确认规则。
