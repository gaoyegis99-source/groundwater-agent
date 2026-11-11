# 08 Workflow Draft：编排、补齐、DAG 与导出

## 当前事实
`source/web/src/WorkflowDraftCard.tsx` 展示草稿、节点序列、missing inputs、outputs、data bindings、parameter sources、knowledge refs、limitations，并允许 COMPLETE 草稿 JSON export。
文档 `source/docs/capabilities/webui.md` 明确当前草稿只生成/导出，不安装、不确认执行、不创建 Job。
Workflow Compiler 位于 `source/packages/workflow-compiler/`，校验 Workflow v6。

## WF-01 修复顺序意图误判（P0）
源码：`source/kernel/src/platform_kernel/agent_runtime/tooling/reply_submission.py`。
当前 `_sequential_draft_issue` 用消息正则 `先.+(?:再|然后)|\b(first|then|after)\b`，并要求 edge_count >= node_count-1、sink count=1、outputs 都来自该 sink。
问题：普通“data after 2020”可能触发；合法 fan-out 多 sink 被拒。
步骤：
1. 先新增失败用例：“先做数据 QA，再分别做趋势和缺测分析”应允许一源多汇。
2. 新增“Use observations after 2020 ...”不得触发顺序链规则。
3. 保留明确线性顺序负例：“first sort, then calculate statistics”，图中无连接应被拒。
4. 根据用户明确表达的依赖关系和 DAG 图本身校验；不得仅靠关键词判断。
5. 若用户明确要求 A→B，则校验 B 的依赖路径包含 A。
6. 允许多个 sink；最终 outputs 可以包含用户请求的多个最终分析产物。
7. 保留 compiler 的 cycle/unreachable/port checks。

验收：合法 fan-out/fan-in 不误拒；明确线性依赖断开仍拒绝；普通 after 时间短语不触发。

## WF-02 草稿图形化呈现（P1）
源码：`WorkflowDraftCard.tsx`、`types.ts`、`workflow-draft.test.tsx`。
1. 新增只读 SVG DAG，不先引入大型图编辑库。
2. 节点显示 operator title、node id（技术详情）、输入/输出数量。
3. 连线按 nodeOutput binding 绘制。
4. workflowInput 使用独立外部输入节点。
5. 缺失输入在图边缘显示 missing badge。
6. 节点点击展开端口、参数来源和 limitations。
7. 多 sink 正常呈现。

## WF-03 草稿缺项补齐（P1）
1. 缺 DATA 时从当前 Data catalog 选择精确 DatasetVersion。
2. 缺参数时根据 operator/model schema 生成字段编辑器。
3. 规则/配置输入必须作为独立 workflow input，不得拿 observations 数据绑定到 rules。
4. 补齐后重新调用 compiler preflight。
5. 展示修改前后差异和仍未解决事项。
6. 导出更新后的草稿。
7. 不添加执行按钮，不绕过现有用户确认流程。

## WF-04 编译错误可解释化（P1）
把 compiler error code + JSON pointer 映射为具体节点/端口/参数。保留机器 code；UI 给出“哪一步、哪个端口、期待什么”。例如：
- WORKFLOW_INPUT_BINDING_INVALID
- WORKFLOW_PORT_INCOMPATIBLE
- WORKFLOW_PARAMETER_BINDING_INVALID
- WORKFLOW_CYCLE
- WORKFLOW_NODE_UNREACHABLE
- WORKFLOW_CARDINALITY_UNSUPPORTED

## WF-05 Draft 数据身份一致性（P0）
每个 data binding 必须精确包含 project/dataset/version/semantic/object/checksum，且与当前 Turn 实际 inspect/read 的数据证据一致。草稿重新生成不能把旧 Turn 的 Data version id 复制过来。

## WF-06 Draft 导出（P2）
完善导出 JSON 的可读性、schema version、operator catalog digest、data binding identity、limitations。导出文件仍声明 `execution_allowed=false`。
