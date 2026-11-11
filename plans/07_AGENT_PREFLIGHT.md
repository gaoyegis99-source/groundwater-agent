# 07 Agent 路由与 Preparation / Preflight

## AG-01 字段参数存在性检查（P0）
源码：
- `source/kernel/src/platform_kernel/agent_runtime/preparation_tool.py`
- `source/kernel/src/platform_kernel/settings.py`（ExecutableModelDefinition）
- `source/domains/groundwater/6.0.0/models/*.model.json`
- `source/kernel/tests/`

当前 preparation 对每个 `data_input_parameter` 调用 `inspect_data_version`，核对 version identity/current policy，并只检查 descriptor.fields / semantics.fields 是否非空。它还没有将每个字段参数与 descriptor 的真实 field id/name 逐项核验。

实施：
1. 盘点七个模型的字段参数：groupField/timeField/valueField/xField/yField 等。
2. 定义清晰的模型元数据声明，将 field parameter 映射到 data input parameter 和允许字段类型。
3. 从已核验的精确 DatasetVersion descriptor 读取字段集合。
4. 区分 field id 与显示名称；只允许使用系统合同规定的标识。
5. 不存在字段返回 `FIELD_NOT_FOUND`，给出相似候选但不自动替换。
6. 类型不匹配返回 `FIELD_TYPE_MISMATCH`。
7. descriptor 缺失返回 UNKNOWN，不得伪装 PASS；按业务规则阻止或要求用户判断。
8. 在 Confirmation 之前生成 blocking issue。
9. 保留现有 `_ready_snapshot` 的 inputs、knowledge_refs、parameter_sources 和 checks。

验收：
- 不存在列被识别。
- 同名但不同 Data version 不可混用。
- 字段类型不兼容被阻止。
- descriptor 缺失不是 PASS。
- 七个模型的已有黄金参数误拦为 0。

## AG-02 单位/CRS/时间语义检查（P1）
先从 Data semantics、descriptor、model schema 确认现有信息可用性。
分阶段增加：
- 单位字段是否已声明；
- 米/毫米/英尺等是否可比；
- 水位高程与埋深的 basis 是否一致；
- 空间算子是否要求 projected metric CRS；
- 时间字段是否可解析、时区是否明确、范围是否覆盖。
缺少语义时标 UNKNOWN，不推测单位或 CRS。

## AG-03 MiniMax 普通 run 终结路径（P0）
源码：`source/kernel/src/platform_kernel/agent_runtime/run_resume.py`。
当前 MiniMax `WAITING_EXECUTION` 且 action 全终态分支调用 `finalize_kernel_case(deps, run)`；该 finalizer 与 case 终结语义耦合。
步骤：
1. 完整阅读 `finalize_kernel_case`、`finalize_kernel_case_rejection`、`run_finalization.py` 和相关 tests。
2. 抽取通用 `finalize_completed_execution`，只依赖持久化 actions/WorkflowRun/output facts。
3. 案例免责声明只在 `kernel_case_state(...) is not None` 的 case 路径生成。
4. 多 action 输入引用按身份去重统计。
5. 多 plan/action 时正确关联 plan revision，不只默认第一个 action。
6. FAILED action 不得被 COMPLETE action 覆盖。
7. 保留 MiniMax presentation-only 的设计：Provider ACK 失败不把已成功执行改判失败。

验收：
- MiniMax 普通成功 run 无“开发调试案例”声明。
- synthetic case 保留免责声明。
- 多 action 输入计数准确。
- action 有失败时最终状态失败。
- DeepSeek 正常路径不变。

## AG-04 Intent Router 中文金标（P1）
读取 `intent_routing/` 与 `test_intent_routing*.py`。
建立中文样例，覆盖普通咨询、比较、执行、拒绝执行、状态查询、workflow draft、附件读取、缺字段澄清。负例要覆盖“先解释不要执行”“比较上次和这次结果”等。
按 responseKind/semanticIntent 分层报告，不只看总准确率。

## AG-05 错误修复和工具反馈（P2）
将稳定错误码映射为可执行修复建议；不在 prompt 中重复所有 schema。对于可机器判定错误，工具返回 path/code/expected/observed。
