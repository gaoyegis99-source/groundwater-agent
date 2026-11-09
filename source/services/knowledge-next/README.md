# Knowledge Service 3

上传已整理的 JSON 知识包，服务自动校验、验证可选 Data 绑定、向量化并整包生效。服务通过 Knowledge HTTP v3 和 tools v4 接入 Groundwater v12。

## 运行

在仓库根目录运行，先设置与当前产品相同的真实 Embedding 服务地址：

```bash
export PLATFORM_EMBEDDING_BASE_URL=http://YOUR_EMBEDDING_HOST:PORT
../bin/compose -f services/knowledge-next/compose.yaml up -d --build --wait
```

API 位于 <http://127.0.0.1:8120/knowledge/v3/>，OpenAPI 为 `/openapi.json`。服务镜像不再
托管管理页面；装配后的统一 Gateway 在 `/knowledge/` 提供页面。
`KNOWLEDGE_PORT` 可改端口，`KNOWLEDGE_PROJECT` 默认 `local-data`。Compose 默认名为
`platform-knowledge-next-v3`，使用专属数据库角色和卷；数据库不映射宿主机端口。本地开发
由统一 Gateway 转发 Kernel 已验证的本地身份，且服务只能由内部容器网络访问。导入和生命周期变更在路由上要求 `admin`，并把主体、角色、trace/request ID
持久化到任务或修订记录。`KNOWLEDGE_READ_ONLY=1` 同时停止 Worker 领取任务并关闭 API
管理写入；检索仍会保存审计回执。

API 与 Worker 使用同一镜像并要求 Schema v7。迁移只允许沿本服务
v4→v5→v6→v7 顺序升级；v7 将资源权限收敛为 owner 与 PRIVATE/PROJECT。未知版本以
`SCHEMA_VERSION_UNSUPPORTED` 拒绝。Embedding 不可用时混合检索明确失败，目录、精确读取与
显式词法检索仍可用。

Data 仅在知识包带绑定时需要。可设置 `KNOWLEDGE_DATA_URL` 为容器可访问的 Data API 根地址；与本仓库独立 Data Compose 联调时使用：

```bash
../bin/compose -f services/knowledge-next/compose.yaml -f services/knowledge-next/compose.data.yaml up -d --wait
```

此覆盖文件只把新 Knowledge 服务接入现有 Data 网络，通过 HTTP 读取 Data 固定版本和历史语义；不修改 Data 存储、部署文件或原始资料。Data 必须已运行，且项目相同。普通知识不依赖 Data；不下载、解析或重复保存原始文件。

## 知识包与自动生效

完整输入约束由 `KnowledgePackageV1` 的 OpenAPI Schema 定义；最小示例见 [example.json](examples/example.json)。页面直接上传这个格式。每包至多 128 篇文档、16 MiB；每篇至多 64 个单元、16 个来源、32 个 Data 绑定；每单元至多 4,000 字符。非法输入返回字段位置，不部分入库。

装配启动时的领域目录 bootstrap 先调用 preflight，对语义内容完全一致的条目直接返回
`ALREADY_APPLIED`，只为新增或变化条目提交原子导入。更新条目的当前版本只作为本次提交的
CAS 前置条件；`expectedVersion` 不写入不可变知识正文。bootstrap 是独立的一次性初始化状态，
失败不会阻断 Kernel/Gateway 的登录和诊断接口。

- `schemaVersion=knowledge-package.v1`、`projectId`、`idempotencyKey` 和 `documents` 构成一个原子导入组。
- 文档包含 `knowledgeId`、`contentVersion`、标题、来源与已分块 `units`；可附别名、类型、完整模型身份及 Data 绑定。来源类型与正文是上传者声明，不代表专家认证。
- 更新必须传入当前 `expectedVersion`；首次创建不传。服务不重新分块或改写正文。必要表头、单位、脚注通过 `requiredUnits` 声明；跨单元关系必须位于同篇文档内。
- 状态为 `QUEUED → VALIDATING → INDEXING → SUCCEEDED/FAILED`；Data 或 Embedding 临时故障进入 `RETRY_WAIT`，到期后重新 `VALIDATING`。上传即要求生效，无人工审核或第二次发布动作。永久失败后修改知识包、使用新的幂等键重新导入；临时故障预算耗尽后可由管理员显式发起人工重试，不修改原知识包及幂等身份。同键同内容重放返回原导入，同键不同内容冲突。
- 全部外部处理在事务外完成，短事务再次检查预期版本、任务租约和尝试代次后原子切换。失败不会替换旧知识。Worker 崩溃后重启可重新领取租约过期的任务，复用已完成的 Embedding；程序错误使任务失败并退出 Worker。
- 正文版本不可变。生命周期独立修订，撤回不删除历史正文，复核逾期可见但有提示，有效期过期不参与检索。重新启用旧版本不会把它设为当前版本；要替换当前版本需新导入。

来源有效期和复核日期在导入时聚合为文档生命周期；后续人工生命周期变更需预期修订号和原因，并保留历史。管理版本详情允许查看撤回正文；取证接口不将其作为有效证据返回。

现有 `domain-knowledge.v2` 用显式转换器转换，保留知识标识、来源、正文和块顺序：

```bash
platform-knowledge convert PATH_TO_DOMAIN_KNOWLEDGE.json --project local-data --key seed-001 > /tmp/knowledge-package.json
platform-knowledge-client --url http://127.0.0.1:8120 --writes POST imports --json-file /tmp/knowledge-package.json
platform-knowledge-client GET imports/IMPORT_ID
```

转换器保留包摘要；`--prefix` 可显式为知识 ID 添加领域包前缀。它不自动推测旧产品的运行时 ID，也不迁移历史聊天引用。客户端可独立安装 `services/knowledge-next/client`，仅依赖 HTTP；默认关闭管理写入。

## 检索、绑定与证据

| 接口 | 行为 |
| --- | --- |
| `GET capabilities`、`GET tools` | 部署能力、预算、固定只读工具契约 |
| `POST imports`、`GET imports/{id}` | 整包导入及持久状态 |
| `POST imports/preflight` | 不写入的服务端导入差异、expectedVersion 与待执行检查 |
| `POST imports/{id}/retry` | 管理员对临时故障耗尽的 FAILED 导入显式重试；请求体 `{ "requestId": "稳定请求键" }`，返回同一导入 ID 与新 `retryGeneration` |
| `GET documents`、`GET documents/{id}/versions` | 带 facets、筛选、排序和游标的目录与不可变版本历史 |
| `GET/PUT documents/{id}/access` | 读取 owner 与 PRIVATE/PROJECT 可见性，或由 owner/admin 更新可见性 |
| `GET versions/{id}`、`GET/POST versions/{id}/lifecycle` | 管理详情、生命周期历史与带 CAS 的变更 |
| `POST lifecycle/batch/preflight`、`POST lifecycle/batch` | 管理员逐项差异预检；按当前版本和修订号 CAS 原子更新至多 100 份知识 |
| `GET activity` | 管理员导入与生命周期操作时间线 |
| `POST retrievals` | `query`、`mode=hybrid/lexical`、可选 `scope`、类型/精确绑定、top-K、最低分、metadata filter 与预算 |
| `POST evidence/read`、`POST evidence/validate` | 精确版本/单元/checksum 批量读取或仅核验状态 |
| `POST lookup` | 按知识编号、标题、别名、模型 ID 或精确绑定查询，无 Embedding |
| `GET retrievals/{receipt_id}` | 取证身份、状态与范围回执，不复制正文或原问题 |

适用范围直接从正文版本的 `bindings` 派生：空列表为 `GENERAL`，否则 `DATA_BOUND`。通用仅表示未限定具体数据版本，不代表适用于所有任务。

| scope | binding | 行为 |
| --- | --- | --- |
| 未指定 | 无 | 仅通用知识（1.1 的默认行为变更） |
| 未指定 | 有 | 通用 + 精确匹配专属知识 |
| general | 不接受 | 仅通用知识 |
| bound | 必须 | 仅精确匹配专属知识 |
| combined | 必须 | 通用 + 精确匹配专属知识 |

项目不一致直接拒绝。Document 的 owner 可修改自己拥有的资源，admin 可管理全部项目资源；
PROJECT 对项目成员可读，PRIVATE 仅 owner/admin 可读。非项目成员由 Kernel 返回 404，项目成员
权限不足返回 403；资源读取仍隐藏为不存在以避免枚举。`lookup` 带绑定时严格匹配；仅传明确 key
时仍受同一可见性过滤。HTTP
客户端示例见 [retrieve.py](examples/retrieve.py)：任务先确定精确 Data 版本和语义，再传绑定，
不在 Knowledge 检索时寻找最新版本。

组合检索分别从两类知识取得至多 40 个词法和 40 个向量候选，共用一次查询 Embedding。在同一数据库快照内按各模态原始分数合并排序，然后 RRF 60 融合，Top-5、每篇至多 2 个主单元；不预留类别名额。单范围检索仍为双路各 40 候选。词法使用 NFKC/casefold、字符 trigram 与别名；排序采用 PostgreSQL `ts_rank` 等权和长度归一化（参数 2），避免重叠 n-gram 的词距加权与标题过度加权。1–2 字词也有词法项，但精确术语优先使用 `lookup`。

默认 `KNOWLEDGE_VECTOR_SEARCH=exact`；可显式选择 `hnsw`，使用有界迭代扫描与 `ef_search=100`。候选不足不以不匹配数据补足。不同规模的负载和过滤召回必须分别验证；默认不启用重排序、LLM 查询改写或服务内 Agent。[pgvector 官方说明](https://github.com/pgvector/pgvector)

正文与搜索投影同库；搜索只包含当前活动版本。Embedding 完成后开启 REPEATABLE READ 短事务，在同一快照中取得候选、生命周期与证据回执。模型固定为现有 `qwen3-embedding-4b-q4`、1024 维、标题/摘要/正文模板；缓存摘要包含配置身份与实际输入。数据库固定项目，因此缓存不会跨项目复用。首期仅有一个固定索引代次，改变模型或词法契约需要显式升级和重建，不支持在线混用不同模型。

`EvidenceBundleV1` 返回完整单元、服务生成的证据 ID、来源、定位、绑定和观察时间。默认预算 8 单元/16,000 字符，上限 16 单元/32,000 字符；所需上下文递归整体纳入，放不下则 `NEEDS_EXPANSION`。逐项区分 `OK/STALE/WITHDRAWN/EXPIRED/NOT_FOUND`；`validate` 的 OK 仅说明身份状态，因不返回正文，不能作为本轮新证据。`NO_MATCH` 不等于知识不存在。

每项证据返回派生 `scope` 与 `bindingMatch`：`GENERAL` 表示未绑定具体数据，`EXACT` 表示请求身份精确一致，`NOT_CHECKED` 表示专属证据没有任务上下文，`MISMATCH` 表示不一致；不存在的版本无法推断范围，返回 null。它们不替代 `status` 生命周期/checksum 核验。历史 `read/validate` 不接收任务绑定，专属证据保持 `NOT_CHECKED`，不声称适用于当前任务。新回执记录实际检索范围、请求绑定及各类候选/返回主单元与含上下文单元数；旧回执内容原样保留，不回填适用性结论。

Data 绑定的 wire 身份固定为 `data-primary/projectId/datasetId/versionId/semanticId/fieldId`。字段绑定验证精确语义修订中的字段 ID，不能靠同名字段替代；新版本或新语义不自动继承旧绑定。`fieldId=null` 仅匹配整数据绑定，不是字段通配。导入验证仅证明引用存在，不证明知识解释正确或最新适用性。证据保留导入时核验时间 `bindingsVerifiedAt`，在线检索不访问 Data，也不声称已重新核验 Data 当前状态。数值查询和语义编辑仍归 Data。

`/internal/tools/v5/{retrieve_evidence,read_evidence,lookup_knowledge}` 提供严格 `{data: EvidenceBundleV1, outcome: ToolOutcome}` 输出，输入包装为 `callId/projectId/arguments`。`knowledge-tools-v5` [静态契约](src/platform_knowledge_next/contracts/observation-tools.v5.json) 同时声明进度能力、结果码和安全摘要模板，并由契约测试校验。公共 API 仍为 `/knowledge/v3`，知识包与证据身份版本不变。Domain Manifest 通过 `{service, contract, tool}` 选择这些工具，Assembler 校验后注册到 Kernel。

## Data 故障处理

连接失败、超时和 HTTP 408/429/500/502/503/504 进入持久延迟重试；每包最多五次 Data 校验尝试，间隔 5、15、45、120 秒。第一次临时故障设置十分钟绝对截止时间。合法 `Retry-After` 秒数或 HTTP 日期可延后领取，到达截止时间直接失败，不再发起 Data 请求。

`data_attempts` 与租约 `attempt` 分开：开始 Data 校验前持久加一，进程中断也消耗这次预算。`next_retry_at`、`retry_deadline`、`last_error` 随任务保存；等待时释放 Worker。最后一次临时故障保留在 `last_error`，当前/终止原因见 `error`，成功清空 `error`。同幂等键重放返回同一任务与当前计数，不重置预算；已有 FAILED 不自动复活。仅 `DATA/EMBEDDING_RETRY_EXHAUSTED` 或 `DATA/EMBEDDING_RETRY_DEADLINE_EXCEEDED` 可通过 operator 的 `POST imports/{id}/retry` 进入新的一次有界周期。请求键使重复提交幂等；导入 ID、原上传幂等键、历史尝试计数及失败审计回执不变，`retryGeneration` 递增，Worker 下次领取时产生新的 `attempt`。永久错误不允许原位重试。租约过期和旧代次都不能提交成功、失败或重试。每次重试重新核对预期知识版本、Data 固定版本与历史语义；同一尝试内按资源缓存，跨尝试清空。

版本/字段不存在、绑定或版本冲突直接失败。缺少地址、无效 HTTP 地址、鉴权/项目错误分别报告配置或访问原因；无效 JSON 和缺失响应字段报告 `DATA_RESPONSE_INVALID`，其他 HTTP 协议错误报告 `DATA_PROTOCOL_ERROR`，均不自动重试。Embedding 和程序错误沿用原失败策略。管理页面显示等待、次数、下次时间及错误；通用入库与历史证据读取不需要 Data。

## 备份与恢复

所有权威状态在 PostgreSQL，不依赖本地解析文件。保留所有已生效版本和紧凑回执；首期没有自动历史清理。备份脚本暂停本组件正在运行的 API/Worker，完成 `pg_dump -Fc` 和 SHA-256 后恢复原运行服务。

```bash
bash services/knowledge-next/scripts/backup.sh /absolute/path/knowledge.dump
bash services/knowledge-next/scripts/restore.sh /absolute/path/knowledge.dump knowledge-restored
KNOWLEDGE_PORT=8125 ../bin/compose -p knowledge-restored -f services/knowledge-next/compose.yaml up -d --wait
```

恢复拒绝已有目标卷，保留原部署。备份时若使用了自定义 Compose 项目，通过 `KNOWLEDGE_COMPOSE_PROJECT` 指定；恢复后使用原项目、模型、Data 连接配置。回执只有身份和范围，恢复证据依赖同时保留的不可变正文。开发测试及恢复演练不等于整个平台发布门禁通过。

## 存储基线

每个实例使用独立的显式 Knowledge PostgreSQL bind 目录。新库在单事务内应用
`001.sql` 至 `004.sql` 建立 Schema v7；现有匹配项目和模型配置的 v4/v5/v6 库可由迁移命令
按顺序升级，保留导入、尝试、正文、检索与生命周期身份。API/Worker 不自动执行迁移，也不
挂载或删除旧实例目录；实例升级先创建并验证快照，未知版本没有隐式升级路径。

## 开发与验证

```bash
uv pip install -e packages/embedding-client -e services/knowledge-next -e services/knowledge-next/client
npm --prefix services/knowledge-next/ui ci
npm --prefix services/knowledge-next/ui test
npm --prefix services/knowledge-next/ui run typecheck
npm --prefix services/knowledge-next/ui run build
npm --prefix services/knowledge-next/ui run test:ui
TEST_KNOWLEDGE_DATABASE_URL=postgresql://USER:PASSWORD@HOST/knowledge_test pytest -q -o addopts='' services/knowledge-next/tests
python services/knowledge-next/scripts/evaluate.py --url http://127.0.0.1:8120 --output /tmp/quality.json
npm --prefix services/knowledge-next/ui run test:e2e
```

测试数据库名必须以 `_test` 结尾，会清空其业务表；质量评测使用专用的新 Knowledge 实例及真实 Embedding，不接触旧实例。浏览器测试需要先在 UI 目录执行 `npx playwright install chromium`，会创建并撤回测试知识。
浏览器精确绑定场景还要求通过 `DATA_E2E_URL` 指向同项目的专用 Data 测试实例；缺少该变量时测试拒绝启动，不回退到固定端口或已有 Data 部署。装配拓扑中的 Data 目录请求经统一 Gateway 的同源只读代理完成，页面核对两个 capabilities 的产品、项目和主体后才启用精确绑定选择。

完整的 Data/Knowledge 隔离联调、当前镜像备份恢复和管理 UI 门禁从仓库根目录运行：

```bash
python scripts/checks/check_data_knowledge_e2e.py \
  --output /absolute/temporary/data-knowledge-e2e.json
```

性能脚本 `scripts/benchmark.py` 只接受 `_bench` 结尾的数据库，需要从已导入 291 单元的评测数据库恢复。它复用真实正文和向量构造 5,000/50,000 单元，通过持续版本更新模拟入库并发；查询向量先从真实服务获取再重放，输出明确排除 Provider 的服务调用耗时。该合成规模试验不证明新增语料召回率。真实 Provider 的端到端耗时由 297 题评测另外记录。

领域知识内容仍由领域包持有，服务不包含地下水专用检索规则。验收结果写入本次运行的
XDG state，不作为服务源码的一部分。
