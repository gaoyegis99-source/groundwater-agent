# 缺陷/领域缺口台账

| ID | 模块 | 现象/缺口 | 当前是否复现 | 优先级 | 证据 | 状态 |
|---|---|---|---|---|---|---|
| SYS02-01 | 架构与合同文档 | Data 仍标为 Public API v11，当前模型标为 v4，常驻拓扑漏列 remote_http Worker | 是，已校准 | P3 | `cc83e93`；`assembler_manifests.py`、`assembler_domain.py`、18 份模型声明与 `dev status`；`dev compile`、contracts、assembler 均退出 0 | 已修复 |
| SYS02-02 | 测试环境 | Pytest 清理历史 Toil 工作目录时报告 `PermissionError` / `Directory not empty` | 默认临时目录稳定复现，常规门禁路径已修复 | P3 | `dev check` 与全量 runner 按 Pytest 调用隔离临时目录。常规装配 119 项通过且无旧目录告警，全量七个 Python 切片的临时目录全部清理。注入清理权限错误后报告记录失败并保留已执行的测试计数 | 已修复（常规门禁），共享临时区的历史目录仍在 |
| SYS04-01 | Kernel Confirmation | 参数修订等待 Data 返回期间，批准或拒绝可已持久写入，但修订仍将原确认替换 | 是，已修复 | P0 | `cc83e93`；`test_confirmation_recovery.py` 两个竞态用例先失败；修复后聚焦 23 passed，Kernel scope 870 passed，离线回放 6 × 3 PASS | 已修复 |
| SYS04-02 | Kernel 意图路由调度 | SQLite WAL/SHM 权限异常期间，调度写事务的只读异常可结束调度任务。恢复写权限后 API 又能接受请求，调度仍未恢复 | 现场请求已入队 340.532 秒，重启后领取。真实 SQLite 回归修复前因 `SQLITE_READONLY` 使调度任务退出 | P1 | 数据库内的调度事务遇到可确认的只读回滚时保留持久队列并标记未就绪，恢复可写后自动续领。隔离 HTTP 复测确认 202、503、自动恢复 200。研究实例单服务重启后就绪，合成请求排队 18 ms，路由只领取一次且无 WorkflowRun。证据见 XDG state `audit/kernel-routing-store-22iBP6/`、`audit/research-rollout-MycB4C/kernel-smoke.json` | 已部署并验收 |
| SYS05-01 | Kernel Agent 上下文 | Task 已关联 Data 成果，但上下文按旧 `resource_id` 查找，漏掉本应保留的完成执行 | 是，已修复 | P1 | `cc83e93`；任务与执行持久投影均使用 `DataVersionRefV2.version_id`；新增同数据集不同版本用例先失败，修复后聚焦 173 passed、Kernel scope 871 passed | 已修复 |
| SYS05-02 | Kernel Agent 统计提案 | 用户未要求分组，Provider 调用参数与 READY 准备在 `/groupField` 不一致，提案以 `TASK_PREPARATION_REQUIRED` 失败 | 修复前回归复现，修复后隔离三端通过 | P1 | 研究实例的 READY 记录未含 `groupField`，Provider 原始字段值未留存。新增回归先失败 3 项，聚焦复跑通过 5 项，Kernel scope 879 项通过。隔离 WebUI 生成 `PENDING` 提案，HTTP API 核对批准前无运行，TLI 批准后仅运行一次，HTTP 下载成果确认未分组。证据位于 XDG state `audit/ungrouped-summary-9pWLS0/interaction-log.md`。研究实例 Chromium/TLI/HTTP 核对未传分组的请求生成无分组待确认提案，批准前无运行，见 `audit/research-rollout-results-final-gdCDxB/` | 已部署并验收 |
| SYS06-01 | Web Data 查询客户端 | 提交回执丢失或关闭视图后，客户端无法找回已受理的查询或发出取消 | 修复前网络异常与取消回归复现 | P2 | 主 Web 和 Data 工作台用原请求及幂等键最多补发一次。隔离真实 Gateway/Data 浏览器测试故意丢弃首次 202，两次提交均返回查询 `2898abd6a5a647dbb8b5fe72a9c0fff4`。聚焦回归验证取消及耗尽后的状态提示，证据位于 XDG state `audit/query-receipt-BqxQFf/` | 已修复，连续两次回执不明时明确提示状态未知 |
| SYS06-02 | Data 入库画像 | 字符列中 4,500 位的数字文本触发 Python 整数转换上限，画像生成失败 | Arrow 分批扫描回归先失败 | P1 | 直接以 Decimal 核对 int64 范围。带长数字和前导零的真实 Parquet 发布、精确版本读取、跨批次计数与 160 字符样例回归通过。研究实例合成 Parquet 发布与回读验证通过，见 `audit/research-rollout-MycB4C/data-backend-smoke.json` | 已部署并验收 |
| SYS06-03 | Data 字段画像数值精度 | 超过 JavaScript 安全整数范围的不同整数，在前端 JSON 解码后显示相同 | Arrow、HTTP 和两套前端回归修复前失败 | P2 | Data 读取时为超界 `int64`／`uint64` 附加十进制精确文本，PostgreSQL 保持原整数。真实 Chromium 在两套工作台分别显示 `9007199254740992` 和 `9007199254740993`。旧服务缺少文本时标注精度不可确认。隔离浏览器 16 项通过，证据见 XDG state `audit/field-profile-precision-gpL9VK/`。研究实例主 Web 与独立 Data 已按同一版本显示两个精确整数，见 `audit/research-rollout-verification-7VgBQx/` | 已部署并验收 |
| SYS06-04 | 主 Web 与独立 Data 工作台 | 主工作台遗漏已保存的全量画像和样例；独立 Data 将非空值统称为有效值 | 两个前端的组件回归分别先失败 | P2 | 两个前端复用画像校验。隔离 Chromium 从同一 CSV 版本和语义读取相同样例与 `m` 单位，主工作台以原生 Parquet 核对值域。Data 实际发布测试区分样例范围、全量极值和非有限数。证据见 XDG state `audit/data-field-profile-QySCQ9/`。研究实例两套工作台同一 Parquet 版本显示全量 2 行及精确值域，见 `audit/research-rollout-verification-7VgBQx/` | 已部署并验收 |
| SYS07-01 | Kernel Knowledge 引用 | 同批对同一版本/单元读取正确及错误 checksum 时，错误引用的 STALE 抹去成功证据或精确检索身份 | 是，已修复 | P1 | `cc83e93`；两种顺序的回归先失败；修复后 Kernel 聚焦 162 passed、scope 873 passed，Knowledge scope 98 passed | 已修复 |
| SYS07-02 | Knowledge Web 精确绑定 | 数据集搜索响应未到时按方向键，选择列表关闭或结果被再次过滤，已返回的数据集不可选 | 延迟响应的 Chromium 用例稳定复现 | P1 | 列表保持打开并提示加载，直接使用当前服务端结果；防止重复空选择清掉搜索词。带 `aria-expanded` 断言的浏览器回归连续 3 次通过。联合复跑 Knowledge 浏览器 11 项模拟、4 项真实链路通过 | 已修复 |
| SYS07-03 | Knowledge Web 版本选择 | 点击不可变版本下拉框后，弹出列表立即关闭，无法完成精确绑定 | 隔离真实链路超时，生产构建模拟用例复现 | P1 | Chromium 记录同一次鼠标操作先展开再关闭。版本选择改用原生选择控件及独立搜索框，保留分页、加载状态和语义核验。延迟版本列表定向测试 3 项通过，联合复跑 Knowledge 浏览器模拟 11 项和真实 4 项通过 | 已修复 |
| SYS07-04 | Web 对话引用 | 多条证据标题相同时，回答只显示合并来源清单，段落与原文的对应关系丢失 | 同名证据的两段回答在组件回归中复现 | P2 | 多来源回答使用已持久化的 `sourceIndexes`，每段按版本、单元、checksum 打开原文。隔离 Chromium 实测键盘打开第二条引用并读到同一原文，错误 checksum 的独立 HTTP 回执为 `STALE`。证据见 XDG state `audit/knowledge-citation-skpUau/`。研究实例真实 Provider 的多来源答复在段落中打开精确知识片段，HTTP 回执与界面原文一致，见 `audit/research-rollout-semantics-xqIr23/` | 已部署并验收 |
| SYS08-01 | Workflow Compiler | 重复 input、nodeId、output 的诊断只指向 `/graph` | 是，已修复 | P2 | `cc83e93`；三类实际指针先失败，修复后定位重复声明字段，编译器与 Kernel 草稿 120 passed、产品装配退出 0 | 已修复 |
| SYS08-02 | Workflow golden | 三个快照只在 `/operatorCatalogDigest` 与当前目录不符，现行 `5a9d8c40…`，快照 `2ca1c058…` | 是，已修复 | P1 | `cc83e93`；三个 case 先退出 1 且差异均仅为目录摘要。只校准该字段后复跑三个 case 均退出 0、`differences=[]`。原始日志与路径在 SYS-14 XDG state 目录 | 已修复 |
| SYS08-03 | Agent 多步骤 Workflow 草稿 | 用户明确要求同时输出中间排序表和最终统计，连通 DAG 仍被 Agent 的终点限制拒绝 | 已在固定 Provider 复现 | P2 | 正向回归先失败。移除草稿的终点限制，保留顺序依赖与编译器端口验证；提示仍只要求输出用户明确指定的结果。草稿聚焦 56 项通过，新用例保留两个具名输出且未创建运行 | 已修复 |
| SYS09-01 | Executor 发布恢复 | 已过截止时间的 FINALIZING 任务在重启后仍开始上传 Data 成果 | 是，已修复 | P1 | `cc83e93`；修复前上传 2 项，修复后上传 0 项、撤销发布并成为 TIMED_OUT；聚焦 74 passed、Executor 174 passed、Native 160 passed | 已修复 |
| SYS09-02 | Executor / Kernel / Web 任务面板 | Executor 存储节点状态，但科研工作台只能看到运行级状态和错误 | HTTP、Worker 重试与生产浏览器回归修复前失败 | P1 | 新增受运行归属保护的只读节点诊断。真实超时与重新批准后的两次运行分别显示 `CANCELLED`（0 次尝试）与 `SUCCEEDED`（1 次尝试）。隔离交互 3 项、合同 187 项通过。原始 HTTP 回执及截图见 XDG state `audit/workflow-node-diagnostics-BQAcRF/interaction/`。研究实例的节点 HTTP 回执及 Web 展开均显示 `describe` 已完成、尝试 1 次，见 `audit/research-rollout-MycB4C/node-smoke.json`、`audit/research-rollout-verification-7VgBQx/` | 已部署并验收 |
| SYS10-01 | Confirmation Editor | 数据选择固定首批 50 项；数值参数「原值」错误显示草稿值 | 是，已修复 | P1 | `cc83e93`；51 项与原值回归先失败；修复后 Web 290 passed、类型和构建通过 | 已修复 |
| SYS10-02 | Kernel Confirmation | 仅修改其他参数时，Data 当前语义修订变化会静默替换该输入的原冻结引用 | 是，已修复 | P1 | `cc83e93`；带真实存储引用和变更 `semanticId` 的回归先失败，修复后确认聚焦 24 passed；沿用 `CONFIRMATION_DATA_VERSION_INVALID`，无需公共合同变更 | 已修复 |
| SYS10-03 | Confirmation 新选择 | 选择器读取与保存之间如果新选版本的语义指针变化，修订冻结了与选择时不同的完整引用 | Kernel HTTP 特征测试与 Web 翻页回归复现 | P2 | Web 保存用户选择时取得的精确引用。隔离三端实测用 HTTP 改变真实 Data 语义，WebUI 保存返回 409，原批次待确认且无运行；重新选择返回 201，TLI 看到新待确认批次。合同影响见进度，证据见 XDG state `audit/query-receipt-BqxQFf/three-interface-verified/`。研究实例实际改变合成数据的语义，保存旧选择返回 409，重新选择后返回 201 且仍无运行，见 `audit/research-rollout-semantics-xqIr23/` | 已部署并验收 |
| SYS10-04 | WebUI / Kernel 确认修订 | 保存时路由把 `toolCallId` 传入按 `tool_call_id` 校验的 Kernel，合法请求返回 `409 CONFIRMATION_ACTION_SET_INVALID` | 修复前复现，修复后三端验证通过 | P1 | `test_v12_confirmation_revision_requires_a_new_approval` 先复现 `409`，修复后聚焦 37 项、Kernel 全量 875 项通过。隔离 TLI、WebUI、HTTP API 共 6 项通过。保存返回 `201`，修订 2 为 `PENDING`，旧 ID 批准返回 `409 CONFIRMATION_NOT_CURRENT`。TLI 明确批准新 ID 后仅执行一次，使用修订参数 `flatten`。交互证据位于 XDG state `audit/confirmation-revision-QvlGVR/interaction-log.md` | 已修复 |
| SYS10-05 | WebUI 跨标签页登录 | 浏览器已有登录 Cookie，新标签页可读会话，写请求缺少当前 CSRF 令牌而收到 403 | 单测 2 项修复前复现 | P1 | 令牌按同源浏览器共享，保留旧标签页的兼容读取；退出后旧令牌不可恢复。生产构建的 Chromium 双标签页新建会话返回 201。研究实例使用真实页面登录后，在第二标签页新建会话返回 200，见 `audit/research-rollout-verification-7VgBQx/` | 已部署并验收 |
| SYS10-06 | Kernel Confirmation | 用户保存涉及自有私有数据的确认修订时，Kernel 以固定服务账号读取，Data 拒绝并使保存返回 409 | Kernel HTTP 聚焦回归修复前返回 409 | P1 | 现将通过鉴权的用户主体、显示名与角色传入 Data，沿用 Data 资源访问规则。研究实例的临时研究员在 WebUI 保存自有 PRIVATE 数据的修订返回 201，批准前无运行，TLI 批准并重放后只有一次执行。见 `audit/research-rollout-MycB4C/research-rollout.events.jsonl` | 已部署并验收 |
| SYS11-01 | Workflow Draft DAG | 具名输出在图中只有节点计数，缺项列表只显示节点名 | 是，已修复 | P2 | `cc83e93`；Web 两个 UI 断言先失败；修复后 Web 290 passed，Kernel 草稿 55 passed | 已修复 |
| SYS12-01 | GeoJSON | 150,000 个数值要素触发 `Math.min/max` 参数展开栈溢出 | 是，已修复 | P1 | `cc83e93`；`SpatialViewers.test.tsx` 先出 RangeError；单次遍历后保留正确极值和同一地图实例 | 已修复 |
| SYS12-02 | ChartView | X 轴固定为分类、查询无排序、分页剩余未提示 | 是，已修复 | P1 | `cc83e93`；`ArtifactViewer.test.tsx` 先失败，修复后覆盖数值/时间/分类轴、散点提示、排序、单位与分页截断 | 已修复 |
| SYS12-03 | Raster Probe | 小数 NoData 按 Float64 元数据直接比较，实际 Float32 样本被显示为物理值 | 是，已修复 | P1 | `cc83e93`；使用真实 GeoTIFF 的 `-9999.9` 先误返回 `value`，修复后标为 NoData；探针回归含投影/仿射、Scale/Offset | 已修复 |
| SYS13-01 | 成果预览、比较 | 打开成果只传版本 ID，会选当前语义和默认对象；比较省略血缘且只能选择首批 100 个版本 | 是，已修复 | P1 | `cc83e93`；组件先失败；现复用精确语义历史端点校验版本与对象，比较新增血缘和游标；生产 Chromium 实测历史语义读取与 GeoTIFF 探针通过 | 已修复 |
| SYS13-02 | 成果下载 | 成功任务快照与非终态 results 记录同时出现时，匹配数量即可暴露下载；未命名文件缺少 `download` 属性 | 是，已修复 | P1 | `cc83e93`；`activity.test.tsx` 先失败，现同时验证同一运行、终态和完整成果组；Web 297 passed | 已修复 |
| SYS13-03 | 成果版本比较 | 版本列表和比较详情之间语义更新时，右侧静默改用详情返回的新语义 | 延迟语义变化的组件回归修复前失败 | P2 | 对比使用所选列表项的版本和语义 ID，必要时读取对应历史语义。生产 Chromium 核对成果发布时语义与选中版本列表语义，Data 历史语义定向回归通过。证据见 XDG state `audit/workflow-node-diagnostics-BQAcRF/comparison/`。研究实例浏览器读取比较列表后变更右侧版本当前语义，界面仍用选择时的语义，见 `audit/research-rollout-results-live-KdXAnl/` | 已部署并验收 |
| SYS13-04 | 成果刷新与附件预览 | 刷新时仅恢复版本 ID，成果列表未到会读到当前语义；复用附件预览也丢掉发布引用 | 恢复选择和新会话附件组件回归分别先失败 | P1 | 保留会话及身份作用域内的精确成果引用，附件和工作台按该引用核对与预览。生产 Chromium 延迟成果列表时仍显示发布语义。证据见 XDG state `audit/result-format-verification-kMGJtr/`。研究实例变更合成成果当前语义后，刷新预览与新会话附件仍使用发布时的版本、对象和语义，新会话无消息和运行，见 `audit/research-rollout-results-live-KdXAnl/` | 已部署并验收 |
| SYS13-05 | 成果下载文件名 | 命名成果原来缺扩展名，真实 Data 响应的对象 ID 文件名又覆盖了链接建议名 | 研究实例 Chromium 下载得到对象 ID 文件名，Data HTTP 回归先失败 | P2 | Web 按成果格式建议扩展名。Data 从不可变工作流发布回执读取原对象的输出名，并继续使用流式响应。证据见 `audit/research-rollout-results-final-gdCDxB/` 与 `audit/research-rollout-MycB4C/download-red.log`。Data 回归 100 项通过。研究实例 Chromium 原生下载得到 `summary.json`，字节数和 SHA-256 与发布回执一致，见 `audit/research-rollout-results-live-KdXAnl/` | 已部署并验收 |
| SYS14-01 | Executor I/O 基准 | 脚本使用旧版参数和引用、旧 Control 数据库注入点，无法测量当前装配 | 是，已修复 | P2 | `cc83e93`；原命令 `MODEL_ARGUMENTS_INVALID`；校准后 64 MiB 和 1 GiB 均通过，保留两个并行租约和恢复后的校验和 | 已修复 |
| SYS14-02 | 计划目录检查 | 本轮台账直接引用了退役路径，架构守门判定 `RETIRED_PLAN_REFERENCE` | 是，已修复 | P2 | 历史出处改用 Git blob ID；`plan-hygiene-before.log` 复现，`plan-hygiene-after.log` 10 passed | 已修复 |
| SYS14-03 | 能力清单 CLI 回归 | 测试读取已退役 UI 配置，旧配置 10 个模型，当前产品选中的配置为 18 个 | 是，已修复 | P2 | 从当前 Product Manifest 取 UI 路径，完整保留模型、示例及输出文本的比较；工程速查引用生成清单中的现行身份与计数 | 已修复 |
| SYS14-04 | 回复质量评估预检 | 工具的固定领域身份为 6.0.0 与 10 个模型，当前装配无法加载 | 是，已修复 | P2 | 将精确固定身份更新为 6.1.0 与当前 18 个模型；本地 `load_release_identity` 成功返回，完整模型身份摘要 `sha256:60f27176ce7e018b926e54397c9fa878c06811ac3382552dc945bc24d211be64`；真实 Provider NOT_RUN | 已修复 |
| SYS14-05 | Data / Knowledge 前端构建 | 两套工作台首屏加载完整 Data 校验器，弱网拉长工作台出现时间 | Chromium 冷缓存实测 | P3 | 拆出首屏操作校验器。Data 首屏加载的 Data 校验器从 573,808 字节降为 60,328 字节。两轮弱网样本均值 Data 6.65 → 4.39 秒，Knowledge 9.24 → 5.50 秒。生成校验一致性及本地生产浏览器 9 + 11 项通过 | 首屏已修复，完整校验器仍按需加载 |

| SYS14-06 | 交互式开发批次 | 运行时应用或范围检查失败后，下一批只检查自己的变更，旧失败仍在却显示新批次通过 | 修复前复现 | P1 | `test_later_batch_reconciles_failed_work_without_reapplying_valid_runtime` 修复前 2 项失败。下一批保留待应用的变更和待验证范围，已应用的运行时不因验证失败重复更新 | 已修复 |
| SYS14-07 | 交互式开发启动和停止 | 初始化期间的源码变更被新快照吞掉，停止时已入队的后续批次被丢弃 | 两条路径均在修复前复现 | P1 | 启动期间编辑、停止前入队两个回归各先失败。控制器改为初始化前拍摄快照，停止时处理已经接收的批次 | 已修复 |
| SYS14-08 | 监控回归入口 | 监控测试位于操作脚本目录，只能手动运行，常规工作区收集和全量切片都不会发现 | 由 `pytest.ini` 和切片清单确认 | P2 | 测试移入 `tests/cli/test_live_monitor.py`。平台切片按 `tests/cli` 收集，单独收集确认 6 项 | 已修复 |
| SYS14-09 | 生产浏览器验收入口 | 全新构建目录中，Playwright 在生成页面前等待 preview，就绪探测超时 | 空缓存首跑退出 1 | P2 | 将构建放到 Playwright 的服务器启动命令，保证产物生成后才启动 preview。同一入口用另一个空目录复跑，对话浏览器测试 2 项通过 | 已修复 |
| SYS14-10 | 实例快照与研究归档 | 宿主 SQLite 读取会创建 WAL 辅助文件；不同 UID 可导致 Kernel 写入失败，停机快照也会把探测生成的文件归档 | 临时 WAL 库、快照和活动归档回归均复现 | P1 | 停机读取移入数据库与已提交 WAL 的临时副本；活动归档校验 Compose 身份和数据挂载后由 Kernel 容器读取。归档聚焦 6 项通过，隔离 rootless 容器实测辅助文件与库 UID 均为 110000 | 已修复 |
| SYS14-11 | 完整验收续跑 | 同一产品下修改源码后，续跑会跳过旧源码已经通过的阶段 | 已跟踪、未跟踪和缺少指纹的报告均先失败 | P1 | 续跑与阶段检查点核对 HEAD 及文件内容指纹。真实 CLI 拒绝旧源码报告并保留文件，同源报告复用全部 14 个阶段 | 已修复 |
| SYS14-12 | 完整验收证据 | 不同批次或失败重跑可能从同一缓存路径拾取上次证据 | 共享路径回归先失败 | P1 | 每个阶段尝试使用独立路径。续跑保留原证据，强制重跑使用新路径。首次输出缺失时标记 MISSING。全量报告和同源续跑报告均核验 9 项证据 SHA-256 | 已修复 |
| SYS14-13 | 装配金样 | 确认修订合同变动后，装配锁金样仍使用旧 Kernel HTTP 合同摘要 | 生成文件比对先失败 | P1 | 七项生成文件只改变 `assembly-lock.json`，其中唯一字段差异为 `/modules/kernel/httpContracts/kernel-http-v12`。校准金样后装配范围 119 项通过 | 已修复 |
| SYS14-14 | 运行实例状态 | 本地装配更新后，状态命令把运行实例误判为未初始化 | 现有实例返回 `DEGRADED`，运行装配有 111 份文档的成功回执 | P1 | 运行状态按容器配置挂载核对服务清单和 Knowledge 回执。混合挂载回归显示 `UNKNOWN`，合入后真实实例查询 PASS | 已修复 |
| SYS14-15 | 装配清理 | 清理将容器挂载的旧装配列为可删除，影响后续启动 | 真实预演确认，两项回归先失败 | P1 | 保留容器绑定的装配，挂载不可核验时保留旧装配。合入后真实预演保留运行装配，仍识别闲置装配 | 已修复 |
| SYS14-16 | 隔离 Compose 浏览器门禁 | 模型生命周期的 48 行观测表与字段画像的 2 行 CSV 同名，完整门禁选择了错误版本 | 完整门禁 1 项失败、2 项被同组串行规则跳过 | P2 | 让两份数据使用不同标题，浏览器按精确标题选择 CSV，再以版本 ID 核对预览 | 已修复，隔离浏览器 16 项通过 |
| SYS14-17 | 隔离 Compose 歧义场景 | 已批准的统计作业返回 `JOB_DEADLINE_EXCEEDED` | 原歧义用例本轮完成并发布，未复现超时。两个改名追加用例在批准前失败，未覆盖目标链路 | P2 | 本轮运行 `862c4b08-642d-4635-b4e7-8f061d019ad5`：节点完成至 Data 发布 3,520.66 ms，Data 发布至 Executor 终态 26.34 ms，Executor 终态至 Kernel 回写 759 ms。外置取证脚本解析多行 JSON 失败，隔离入口退出 1，同一隔离环境清理前独立补读三服务记录。历史失败缺少可关联的发布和回写时间。证据见 XDG state `audit/sys14-17-xk4sMY/verification.json` | 开放，历史超时根因 UNKNOWN |

只记录当前本地 checkout 可核实的问题。领域问题必须有数据、方法或科学依据。UNKNOWN 不得关闭。

SYS-01（`cc83e93`）完成工具链、产品声明、功能清单与运行实例核验，本卡未登记产品缺陷。

## SYS-03 历史审计复核

历史依据见[清理前的源码审计、差距矩阵与算子分析](https://github.com/gaoyegis99-source/groundwater-agent/tree/19175f592697999b895d6d3ebdb25c6d06d39455/plans)。以下结论基于 `cc83e93` 的源码和本轮实际检查。源码可见的限制与运行缺陷分别注明触发条件。

| 历史编号 | 当前证据与触发条件 | 当前状态 | 后续任务 |
|---|---|---|---|
| 版本/目录计数 | 现行产品选择 6.1.0；`capability-inventory --verify` 校验 18 个模型、18 个工作流、111 个算子。历史 6.0.0 计数已过期 | ALREADY_FIXED_VERIFIED（激活版本） | SYS-01 |
| UI-01/UI-02 | `ConfirmationEditor.tsx` 已捕获列表/详情失败和非法 JSON，聚焦套件通过；冻结值显示的全交互核验待完成。目录仍只读取 `limit=50` 的第一页，并为当前版本逐个发请求 | ALREADY_FIXED_VERIFIED（错误反馈）；CONFIRMED（第 51 项无法经选择器获得）；UNKNOWN（原值全部显示场景） | SYS-10 |
| UI-03/UI-04 | `use-session-stream.ts` 使用请求序列与当前视图检查，以最新 results 替换旧数组；draft epoch 支持发布和重连后重新读取终态。`workflow-draft.test.tsx` 与 `progress.test.tsx` 共 50 passed | ALREADY_FIXED_VERIFIED | SYS-04/SYS-11/SYS-13 |
| UI-05/UI-06 | GeoJSON 着色变更无须重建地图；栅格探针读取原始波段并应用 Scale/Offset，单独报告 NoData。SpatialViewers 与 raster-probe 现有测试通过。GeoJSON 极值仍以展开数组调用 `Math.min/max`，大数组需运行复现 | ALREADY_FIXED_VERIFIED（历史生命周期/探针）；UNKNOWN（大数组阈值） | SYS-12 |
| UI-07 | `ChartView.tsx` 固定 category X 轴并仅查询 1000 行。时间/数值轴、查询排序、截断和单位需联合核验 | CONFIRMED（固定轴和读取上限）；UNKNOWN（其余表现） | SYS-12 |
| AG-01/AG-02 | `preparation_tool.py` 逐字段检查；`run_resume.py` 使用 `finalize_completed_execution`。`missing-field-correction` 与 `minimax-noncase-terminal` 各通过三次离线回放 | ALREADY_FIXED_VERIFIED | SYS-04/SYS-05 |
| AG-03 | `fan-out-draft` 三次通过。固定 Provider 复现终点限制后，显式顺序草稿支持同时输出中间排序表与最终统计。回归核对两个具名输出且无运行。见 SYS08-03 | ALREADY_FIXED_VERIFIED（fan-out）；已修复（中间输出） | SYS-05/SYS-08 |
| Workflow Compiler | `graph.py` 仍要求 cardinality `one`，输入绑定限定 workflow input 或 artifact node output，常量输入与 many 不在当前已支持范围 | CONFIRMED（现行合同限制） | SYS-08 |
| WF-01 | `WorkflowDraftCard.tsx` 已调用 `WorkflowDraftGraph.tsx`，现有图支持展开节点端口、分支/汇合连线。SYS-11 确认遗漏的是具名输出边及缺项列表端口，并补齐这两处 | ALREADY_FIXED_VERIFIED（历史缺图）；已修复（SYS11-01） | SYS-11 |
| KNOW-01 | 对话段落可打开对应原文。隔离浏览器核对了精确身份、回执、键盘焦点和重载，组件测试覆盖来源定位及必需上下文 | 已验证本轮引用交互，见 SYS07-04 | SYS-07 |
| DATA-02 | 当前以 Data 持久化画像验证全量计数、抽样、值域、单位和大整数精确显示 | 已修复，见 SYS06-02／SYS06-03／SYS06-04 | SYS-06 |
| WF-02 | 生产浏览器验证节点失败、重读与切换；隔离真实 Worker 超时后，原运行节点取消、恢复运行节点完成，按各自运行 ID 展示 | 已验证节点、错误和恢复交互，见 SYS09-02 | SYS-09 |
| DATA-01 | 隔离三端核对主会话参数选择的版本、对象与语义。版本语义在保存前变更时，重新选择后生成待批准批次 | 已验证参数选择及精确引用衔接，见 SYS10-03／SYS10-06 | SYS-06/SYS-10 |
| RES-01 | 生产构建 Chromium 用 12 种已声明成果格式夹具串联预览、下载、附加与比较，核对格式集合、版本、对象、语义、SHA-256 与血缘 | Web 逐格式交互已验证；真实算子逐格式发布另验 | SYS-13 |
| GW-01–04 / OP-01–03 / MOD-01–02 | 当前激活清单及 contracts scope 已覆盖趋势扩展、流场、覆盖、制图、Kriging、规则分类与 DRASTIC 的工程声明。Pastas/MF6 仍属于独立领域开发；本轮未验证算法口径、结果适用性和外部数据 | UNKNOWN（领域科学验收） | 第二阶段 |

本轮 `dev agent-replay --case all` 退出 0（6 场景各 3 次），确认编辑器、空间查看器和栅格探针 Vitest 共 14 passed，`scripts.checks.check_documentation` 退出 0。历史 `bootstrap` 不可用、依赖未物化及 Ruff F401 由 SYS-01 与 SYS-02 的实际通过结果复核为 `ALREADY_FIXED_VERIFIED`。
