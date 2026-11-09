# Data Service v3

本组件管理独立的数据空间：上传 → 描述/检索 → 查询 → 原子发布 → 下载/工具调用。
它已装配进 Groundwater v12，并通过统一 Gateway、Kernel 与 Executor 的声明式 HTTP 边界提供数据能力；不读取旧 Resource 的数据库或目录。

## 启动和停止

在仓库根目录执行（Linux，Docker Compose；查询要求 Landlock 和 libseccomp）：

```bash
../bin/compose -f services/data/compose.yaml up -d --build --wait
```

API 位于 <http://127.0.0.1:8110/data/v3/>，接口文档为 <http://127.0.0.1:8110/docs>。
`DATA_PORT=8111` 可更换本机端口。Compose 项目固定为 `platform-data-v3`。
该服务镜像只提供 API，不托管管理页面；装配后的统一 Gateway
在 `/data/` 提供。独立 Compose 只发布回环地址上的 API，PostgreSQL 不发布宿主机端口。
Compose 中的数据库密码仅用于这个隔离本机开发环境。

```bash
../bin/compose -f services/data/compose.yaml stop api worker
# 停止整个独立组件，保留所有卷：
../bin/compose -f services/data/compose.yaml down
```

同一 schema 的镜像更新先执行 `stop api worker`，再运行上述构建启动命令。
`migrate` 初始化当前 Schema v8，并且只接受本服务 v4→v5→v6→v7→v8 的顺序事务化升级；
未知、跳跃或更高版本以 `SCHEMA_VERSION_UNSUPPORTED` 拒绝。实例升级由 `gw migrate plan/apply`
在已验证快照和影子存储中完成，不允许服务启动时隐式跨版本升级。

仅恢复已有容器、镜像与 schema 未变化时，使用以下命令（也适用于已停止的 Worker）：

```bash
../bin/compose -f services/data/compose.yaml restart api worker
# 仅恢复 Worker，API 继续运行：
../bin/compose -f services/data/compose.yaml restart worker
```

不要用 `start worker` 恢复单个 Worker：Compose 会启动其一次性迁移依赖，
而仍运行的 API 持有目录锁，迁移会以 `DATA_ROOT_IN_USE` 拒绝，阻止 Worker 启动。
这时使用上述 `restart worker`，无需修改或删除数据。

API、Worker、迁移、CLI 来自同一镜像。API 启动检查 schema，不自动迁移；`migrate` 是单独的一次性任务。
一个根目录运行一个 API 进程和一个 Worker，查询/准备并发为 1，上传并发为 2。Worker 使用持久队列、租约及尝试代次；中断操作从冻结输入重新准备或查询，不自动重放发布提交。
普通编程错误使操作失败、Worker 退出；修复原因后重新启动，不把错误转为空结果。

## 数据和职责

PostgreSQL schema v8 保存目录、版本、语义、owner 与 PRIVATE/PROJECT 可见性、质量套件/运行/告警、实验与模型注册、
事务 outbox、血缘、指标、发布组、操作事件、查询和预览回执。
本地 `objects/` 保存不可变文件；DuckDB 仅查询已登记的 Parquet。文件先持久化，PostgreSQL 事务决定整组可见性。
本地文件系统与 PostgreSQL 不构成分布式事务；未引用文件通过停写审计和 GC 回收。

- Dataset 是稳定身份，DatasetVersion 是不可覆盖快照；wire 更新必须携带 `datasetId` 和 `expectedVersion`。
- DataObject 区分原始文件与规范化 Parquet，分别有 SHA-256。派生表示记录转换配置与 Arrow 版本。
- SemanticRevision 保存调用方声明的业务含义；修订使用预期语义版本检查，历史查询继续引用旧版本，`GET /versions/{vid}/semantics/{sid}` 可核对历史解释。
- 字段 ID 按位置为 `f0`、`f1` 等，原列名独立保存，支持重名列。CSV 默认文本，`001` 不变为 `1`。
- CSV 转换类型为 `string/int64/float64/date/timestamp/bool`，日期使用 ISO 格式；带时区 CSV 时间戳须先在来源端规范化，避免静默丢弃时区。Parquet 保留物理类型。
- 质量检查涵盖完整解析、转换和空值计数；重复、业务真实性、空间适用性不在这些检查的证明范围内。
- 来源运行 ID、模型名称及语义内容是调用方声明。目录中文检索使用大小写不敏感的字面子串，不宣称语义召回。
- `inventory` 与 `discovery` 是当前目录的只读请求时投影，不保存第二份索引。结构化发现支持 query、tag、format、field 和 unit 条件，返回确定性分数、匹配理由及缺失元数据；这些结果用于候选选择，不是冻结执行身份或适用性证明。

仅保留 `ObjectStore`、`FormatReader`、`QueryBackend` 三类静态扩展。新增实现须随代码与契约测试发布；不运行时安装插件。
模块之间使用已校验的类型；API/CLI 不直接执行数据库查询计划，外部客户端不导入服务内部模块。

## HTTP 与工具调用

API 前缀 `/data/v3`，完整输入输出类型见 `/openapi.json`，能力与限制见 `/data/v3/capabilities`。
`X-Project-ID` 若提供必须与部署固定项目相同；`X-Client-Type` 为 `web/sdk/agent/service/cli`，仅用于归因。
研究运行由统一 Gateway 转发 Kernel 已验证的本地身份；Data 只在内部容器网络提供服务。
`DATA_READ_ONLY=1` 在服务端禁用发布与语义写入，仍允许查询。工具适配器默认不暴露写工具；这不代替服务端访问控制。

`researcher` 只可提交受限 `USER_UPLOAD`：一个名为 `file` 的新 Dataset 输出，不得指定
`datasetId/expectedVersion`、lineage、转换、指标、标签或管理语义。完整 `MANAGED`
Publication 只允许 `admin` 或受信任的内部服务。Publication、Query 和
Preview 都由创建主体拥有，幂等键按项目和主体隔离；跨主体读取、上传、准备、提交、中止或
取消返回 404。提交后的 Dataset/Model 仅使用 owner 与 PRIVATE/PROJECT 可见性；PRIVATE
仅 owner/admin 可读，PROJECT 对项目成员可读，不再维护资源级 grant 与 ACL revision。

质量套件版本不可变，Dataset 绑定使用 revision CAS。`POST /quality/runs` 只接收现有
`table.validate@1.0.0` 的完整规则结果，并冻结 Data Version、SemanticRevision、套件、算子版本
和实现摘要；规则严重级别来自套件，不能由调用方改写。模型实验运行冻结输入版本、实现摘要、
环境与别名解析结果；Registry 只引用 Data 中不可变的 PyTorch 权重，并使用 immutable
experiment ID、tags、benchmarkStatus 与非授权 alias；旧 stage 仅保留为历史标签。

输入 JSON 最大 1 MiB。已知边界错误使用 `application/problem+json`，稳定机器分类位于顶层
`code`，字段问题位于 `errors` 并使用 JSON Pointer；不暴露数据路径或数据库连接。未预料的异常按
服务错误失败。

### 目录概览与结构化发现

`GET /inventory` 返回当前 Dataset 数、已有标签/描述覆盖量以及 format/tag 分布。
`POST /discovery` 接受 `query/tags/formats/fieldTerms/units/after/limit`，对当前版本、语义和对象元数据做确定性过滤与排序，并返回紧凑候选身份、命中的字段、`matchReasons`、`unknowns`、`totalMatches` 和游标；完整 descriptor/semantics 不复制到发现响应。Agent 工具的开放 `arguments` 键按工具合同保留 snake_case；选定候选后仍须调用 `inspect_data_version` 冻结完整版本身份。

发现仅匹配已登记的字面元数据，不做向量语义召回，不推断缺失字段含义，也不缓存跨项目副本。更正语义后下一次请求直接读取新的 current catalog 投影。

### 管理发布一组结果

以下完整发布流程属于管理员/受信任内部服务接口；普通浏览器上传使用上述受限形状，
不能用它更新已有 Dataset。

1. `POST /publications`：指定幂等键、命名输出清单、输入版本、来源声明。
2. `PUT /publications/{publicationId}/items/{name}`：上传原始字节，`X-Content-SHA256` 使用 `sha256:<64 位小写十六进制>`。同名相同字节可重放；不同字节冲突。
3. `POST /publications/{id}/prepare`：冻结清单，返回 `operationId`；轮询 `/operations/{id}` 或发布组。
4. 准备成功进入 READY，`POST /publications/{id}/commit` 统一公开；提交响应丢失可重放或查询发布组。
5. 发布前可 `POST /publications/{id}/abort`；已发布组不可中止。失败内容使用新的发布组修正。

```json
{
  "idempotencyKey": "example-run-001",
  "outputs": [
    {"name":"prediction","title":"预测明细","format":"csv","policy":"FILE_QUERYABLE",
     "conversion":{"types":{"f1":"float64"},"null_values":[]},
     "semantics":{"description":"逐井预测值","fields":{"f1":{"unit":"m"}}}},
    {"name":"metrics","title":"评价指标","format":"json","policy":"FILE_AND_METRICS"},
    {"name":"image","title":"结果图","format":"png","policy":"FILE_ONLY"}
  ],
  "inputVersions": [],
  "provenance":{"runId":"external-run-001"}
}
```

指标文件契约：

```json
{"metrics":[{"name":"RMSE","definition":"sqrt(mean(square(predicted-observed)))","unit":"m","evaluationScope":"same-wells-and-time-window","value":1.2}]}
```

指标按名称唯一；缺失口径、非有限值或同名重复都会失败。指标比较要求定义、单位、评价范围精确一致，不自动换算单位。
发布 FILE_ONLY 文件不推断其业务语义；但声明为 JSON 的文件在 READY 前仍会进行
UTF-8、语法、重复键和有限数值校验。该校验受 `DATA_JSON_VALIDATION_BYTES` 限制，
超过预算会失败关闭而不是以未检查内容进入 READY。JSON 只有显式
FILE_AND_METRICS 才投影入指标表；任意不透明字节必须声明为 `binary`。

### 查询

`POST /queries` 接受单版本表格查询或多个指标版本比较，返回持久查询 ID。

```json
{"idempotencyKey":"query-001","versionIds":["VERSION_ID"],"groupBy":["f0"],"aggregates":[{"function":"avg","field":"f1","alias":"average"}]}
```

`GET /queries/{id}?offset=0&limit=100` 返回状态、回执及固定结果分页。
回执固定数据版本、文件校验和、语义版本、查询计划与引擎版本。
`coverage` 说明实际计算范围，`truncated` 说明持久化返回行集是否截断，`nextOffset` 仅说明该行集分页。
查询默认最多保存 10000 行、4 MiB 结果；超出字节预算明确失败。行数截断不改变聚合计算范围。
下载 `/queries/{id}/content` 返回完整已保存结果 JSON，24 小时后返回 410；历史回执保留。
长期结果通过下载后显式上传发布，并在 `inputVersions` 中记录原输入版本。

表格支持字段选择、过滤、排序、分组和 count/min/max/sum/avg；不接受 SQL、文件路径、关联或代码执行。
默认按原始行序分页；指定排序以原始行序消除并列；分组查询以分组字段消除并列。
不支持的嵌套/二进制查询结果明确失败，不能自动字符串化后冒充原类型。

查询子进程在 DuckDB 初始化后启用 Landlock：已登记对象目录只读、尝试目录可写、Python 运行时只读；seccomp 禁止网络套接字、执行新程序与 ptrace。DuckDB 另限制精确文件白名单、扩展、线程、内存和临时磁盘。
主进程执行截止时间和取消，终止底层子进程；初始化阶段不执行用户查询。缺少隔离机制时操作失败，不降级绕过。

当前宿主机 Docker syscall 解析器会拦截 Landlock，因此 Worker 使用容器内 libseccomp 在打开队列前安装固定的默认拒绝策略；启动容器的宿主机 seccomp 层设为 unconfined，同时移除全部 Linux capabilities 并禁止权限提升。所有子进程继承安装后的过滤器，查询再加更严格规则。API/数据库仍使用 Docker 默认策略。详见 [隔离配置与上游来源](deploy/README.md)。

### 只读预览与范围下载

`POST /previews` 为 XLSX 工作表或 GeoPackage 图层创建幂等只读预览；`GET /previews/{id}` 返回状态和分页结果，`POST /previews/{id}/cancel` 取消排队或运行中的操作。预览冻结版本、语义、对象和 checksum，在与查询相同的隔离子进程中解析。

XLSX 不执行公式、宏或外部链接；GeoPackage 以只读不可变 SQLite 打开，只允许从标准元数据选择图层，不接受 SQL。默认输入上限 256 MiB、展开上限 512 MiB、结果 4 MiB、1,000 行或要素、100 列和 200,000 个几何坐标。CSV/Parquet 正式查询白名单不变。

对象内容端点接受一个 `Range: bytes=...` 范围并返回 `206`，用于 COG/GeoTIFF 按需读取。多段或无效范围返回 `416`；完整响应和范围响应都包含 checksum ETag。

### 外部客户端与 CLI

```bash
uv pip install -e services/data/client
platform-data-client --url http://127.0.0.1:8110 operations
platform-data-client --url http://127.0.0.1:8110 get-version VERSION_ID
platform-data-client --writes upload PUBLICATION_ID prediction /absolute/path/prediction.csv
```

Python `DataClient` 使用仓库中提交的 v3 生成 DTO 与 operation descriptors；运行时不读取 OpenAPI，也不按字符串分派工具。
适配器保持回执与结果覆盖信息，不带 LLM 依赖。二进制上传通过显式生产者方法 `upload()` 完成，工具参数不能打开任意宿主机路径。
可运行的合成生产者示例（表格＋指标＋图片，重复运行验证同一发布回执）：

```bash
PYTHONPATH=services/data/client/src services/data/.venv/bin/python services/data/examples/publish_results.py
```

示例通过真实 HTTP 发布并使用类型化查询方法读取结果；内容明确标注为合成数据，不代表模型实际运行。

模拟外部 agent 的 inventory、discovery 和版本语义读取：

```bash
PYTHONPATH=services/data/client/src services/data/.venv/bin/python \
  services/data/examples/agent_read_demo.py --search 模型预测明细
```

该示例不导入服务内部模块，也不内置 LLM；所有调用由冻结合同生成的 DTO 和 operation descriptor 驱动。
“理解”部分只整理调用方声明及物理结构；自然语言意图理解仍由外部 agent 核心负责。

统一 Gateway 的 `/data/` 页面按项目角色提供分面目录、版本治理、质量趋势、可读血缘、XLSX/GeoPackage
预览、完整查询构建与本地历史、指标比较、结构化语义修订、下载和多文件发布向导。管理员的
`GET /activity` 汇总查询、预览、发布、质量、实验、模型阶段和别名修订，
`GET /datasets/{id}/governance` 投影所有者、质量控制面趋势、血缘和完整治理活动；两者都要求
`admin`。页面只保存按 `product/project/subject` 分区且带版本的 operation/publication
ID 与创建时间；响应正文仍由 Data 拥有，刷新后重新读取权威状态。统一 Gateway 同源代理
白名单 Data v3 路由，tools、internal 和 OpenAPI 路径不可达。

## 配置

`DATA_DATABASE_URL`、`DATA_ROOT` 必填；`DATA_PROJECT` 默认 `local-data`。以下值必须为正整数：

| 环境变量 | 默认 |
| --- | ---: |
| DATA_QUERY_SECONDS | 60 |
| DATA_PREPARE_SECONDS | 3600 |
| DATA_QUERY_MEMORY_MB | 512 |
| DATA_TEMP_MB | 2048 |
| DATA_RESULT_BYTES | 4194304 |
| DATA_RESULT_ROWS | 10000 |
| DATA_UPLOAD_BYTES | 1073741824 |
| DATA_UPLOAD_SLOTS | 4 |
| DATA_JSON_VALIDATION_BYTES | 16777216（严格 JSON 校验内存预算） |
| DATA_LEASE_SECONDS | 30 |
| DATA_RESULT_HOURS | 24 |

可选 `OPENLINEAGE_URL`、`DATA_QUALITY_WEBHOOK_URL` 和 `MLFLOW_ADAPTER_URL` 必须是不含凭据的
HTTP(S) 地址。Data Worker 从同一事务 outbox 领取事件，使用事件幂等键、30 秒租约和指数退避；
目标不可用只形成积压，不回滚发布、质量或模型状态。`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 可将
HTTP trace 异步输出到 Collector；未配置时保留 OpenMetrics 和关联日志。

发布文件永久保留；结果过期后读取关闭，物理文件在离线 GC 时回收。这里没有在线热备份、自动删除已发布数据或自动修复损坏文件。

## 快照、恢复与审计

实例级一致性由 `gw snapshot create/verify/restore` 控制面拥有。快照在实例停写后保存数据库、
对象 checksum、实例配置与组件 schema 版本；恢复使用 `plan/apply` 确认，不要求异盘、加密或
月度演练。关键成果另用 `gw archive create/verify` 固化源码、参数、输入输出 checksum 和 lineage。

迁移通过影子存储校验 schema、记录数、对象摘要、owner 映射和 lineage，全部通过后才切换实例
指针；失败保留原存储和可恢复快照。操作步骤见[部署运行维护](../../docs/operations/deployment/operations.md)。

组件级 `platform-data restore` 只用于 Data 组件的隔离恢复验证，不代替实例快照。它在目标数据
根目录内暂存内容，使用 PostgreSQL 单事务恢复数据库，审计引用后再发布对象目录；普通 API/Worker
在暂存标记存在时拒绝启动，避免把未完成恢复当成正常空目录。

## 开发验证

```bash
../bin/bootstrap --sync
../bin/gw deps verify --scope python
python -m pytest -q services/data/tests
ruff check services/data
npm ci --ignore-scripts --offline
npm --prefix services/data/web test
npm --prefix services/data/web run typecheck
npm --prefix services/data/web run build
npm --prefix services/data/web run test:ui
python -m scripts.checks.check_core_imports
scripts/checks/check-boundaries.sh
git diff --check
```

测试默认启动独立临时 PostgreSQL 容器并结束后删除；也可指定专用测试 `DATA_TEST_DATABASE_URL`，该账号须能创建/删除测试数据库，禁止提供生产数据库。
构建镜像后以 `DATA_TEST_BACKUP_IMAGE=1` 重跑服务测试，覆盖真实备份/恢复工具。
浏览器测试使用 `DATA_E2E_URL` 指定专用服务，不连接已有 Groundwater 数据；命令为 `npm --prefix services/data/web run test:e2e`。

仅验证 UI 重构可运行 `npm --prefix services/data/web run test:ui`：自动构建并启动本地 preview，
用模拟 HTTP 覆盖目录/语义、查询分页与恢复、上传校验和与发布流程。该检查不需要数据库，
也不替代上述专用服务的真实端到端验收。
可用 `DATA_E2E_SCREENSHOT` 指定独立截图路径，避免覆盖前次验收证据。
这些是组件开发验收，不证明主产品集成或真实 agent 的自然语言选择准确率。
