# 06 对话与 Confirmation Editor 完善

## CHAT-01 数据资源加载错误处理（P0）
源码：`source/web/src/ConfirmationEditor.tsx`。
现状：`data_list_datasets` → 对每个 currentVersion 调用 `data_get_version` → `Promise.all` → setResources；该链缺少 catch。
步骤：
1. 新增 `resourcesLoading`、`resourcesError`、retry state。
2. 请求增加 AbortController，组件卸载/项目切换取消旧请求。
3. 任一版本详情失败时明确选择策略：整个资源选择器报错，或显示成功条目并标注部分失败；不得静默当作“无数据”。
4. 当前版本缺失、无 raw object、资源列表为空分别展示不同状态。
5. 不改变用户确认中冻结的 inputReferences。

验收：列表成功、列表失败、单个版本失败、空列表、项目切换取消均有测试。

## CHAT-02 JSON 参数编辑错误反馈（P0）
源码：`ConfirmationEditor.tsx` 的 SchemaField object/array 分支。
现状：JSON.parse 失败 catch 静默 retain last valid object。
步骤：
1. 为 object/array 编辑维护 raw draft string，不能只保留 last valid object。
2. 解析失败显示行内错误，不更新实际 value。
3. blur/save 时再次校验。
4. 有 JSON 错误时禁用保存。
5. 正确解析后清除错误并更新 diff。

验收：输入非法 JSON 有错误；恢复合法 JSON 后可保存；保存的值类型仍是 object/array。

## CHAT-03 参数边界与 required 提示（P1）
- 数值输入校验 minimum/maximum/exclusive bounds。
- required 缺失显示具体字段。
- enum 不允许空值绕过 required。
- string minLength/pattern 如 schema 有声明则应用。
- 保存前用模型 inputSchema 做一次完整校验，不能只依赖 HTML min/max。
- 显示“默认值”与“用户修改值”，不混为同一来源。

## CHAT-04 原值和编辑值（P1）
`ConfirmationEditor` 当前 `changes` 对比 frozen confirmation.actions.arguments 与 local values。数值 SchemaField 的“原值”文案目前输出 `value`，即当前编辑态，语义容易误导。
步骤：
1. 以 `confirmation.actions[actionIndex].arguments[name]` 作为 frozen before。
2. 当前值使用 `values[actionIndex][name]`。
3. 移除局部字段里误导的原值文本，或明确显示“当前值”。
4. diff 区统一展示“确认时原值 → 本次修订值”。
5. 保存仍走不可变修订 API。

## CHAT-05 大批次编辑体验（P2）
当前参数超过 24 个时默认折叠。完善搜索字段、按 action 定位、修改项筛选和错误项定位；不改变确认动作顺序和冻结输入身份。

## CHAT-06 上传和消息输入（P2）
先审 `UploadDialog.tsx`、`MessageComposer.tsx`、`upload.ts`。目标是明确文件状态、hashing、取消、上传中、重复文件和错误恢复；不改变 Data publication 业务合同。
