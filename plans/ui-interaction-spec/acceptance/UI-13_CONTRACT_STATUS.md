# UI-13 合同缺口核验

状态指扩展能力本身。当前公开范围内已执行的路径见[场景矩阵](UI-13_EXECUTION_MATRIX.md)。已有证据的 PASS 不会改变本表中的合同限制。

| ID | 状态 | 当前证据和仍缺能力 |
|---|---|---|
| G01 | BLOCKED_CONTRACT | SessionPage 的创建顺序、搜索、归档与分页可验。缺最后业务活动时间、主 Task 摘要与待处理聚合。 |
| G02 | BLOCKED_CONTRACT | 当前任务规格修订可从 TaskContext 恢复。公开历史页与独立可变有效目标缺失。 |
| G03 | BLOCKED_CONTRACT | 已登记 criteria evidence 按显式身份关联。公开 Turn、Run 缺少完整 Task revision 绑定。 |
| G04 | BLOCKED_CONTRACT | 可展示已有路由意图及澄清选项。缺覆盖旧记录和全部回复类型的持久分类。 |
| G05 | BLOCKED_CONTRACT | 运行 ID、当前输出、公开节点诊断及拓扑可独立查询。历史完整冻结输入和逐节点尝试、输出缺失。 |
| G06 | PASS | 运行归属校验后可读取精确发布 ID 和 Data 状态。隔离实测 PREPARING 到 PUBLISHED，客户端 Results 延迟及读取失败与服务端发布终态独立。 |
| G07 | BLOCKED_CONTRACT | AgentRun 停止回执和 Turn、Run 权威终态可验。逐 Run 停止在途和远端清理缺少公开状态。 |
| G08 | BLOCKED_CONTRACT | 缺通用科学验证标准版本、判定及证据。页面的“科学验证状态未提供”是当前合同下的预期。 |
| G09 | BLOCKED_CONTRACT | 外层 API 合同可校验，部分嵌套结构仍为开放对象。 |
| G10 | BLOCKED_CONTRACT | 恢复命令在服务端校验并生成新确认。公开恢复资格和完整禁用原因缺失。 |
| G11 | BLOCKED_CONTRACT | 精确成果引用可核验。Markdown 中的普通资料链接及未绑定媒体缺逐段对象身份。 |
| G12 | BLOCKED_CONTRACT | 时间和 AgentRun 关联取自服务端。缺完整轮次 Task revision 绑定及控制消息的常规 Turn 身份。 |
| G13 | BLOCKED_CONTRACT | 可按历史 Run ID 独立读取。Session Results 只包含 action 最新尝试，缺完整历史枚举。 |
| G14 | BLOCKED_CONTRACT | 业务步骤完成投影可验。逐步骤失败、阻塞和与节点错误的显式关联缺失。 |
| G15 | BLOCKED_CONTRACT | 已满足条件的现有证据可读。逐条件失败原因和完整历史资源缺失。 |
| G16 | BLOCKED_CONTRACT | 现有替代版本号可重新读取。缺替代对象与目标冻结 action 的显式映射。 |
| G17 | BLOCKED_CONTRACT | 冻结值及确认修订差异可验。逐参数来源未随确认冻结投影。 |
| G18 | PASS | Kernel 在同一 AgentRun 保留独立准备候选。浏览器刷新仍显示流场 BLOCKED 和监测趋势 READY，显式确认仅生成一个趋势 Run，已验证其 SUCCEEDED 与成果。 |
