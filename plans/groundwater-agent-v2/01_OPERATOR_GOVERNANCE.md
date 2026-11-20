# Groundwater Agent V2 阶段 1 算子治理

## 实施计划

本阶段以本地 `main@bd5d58a` 和 [阶段 0 基线](00_BASELINE_AND_GAPS.md) 为起点。目标是让产品侧记录可验证、可复查的分层算子证据，同时保留执行身份、可见目录、动态组合白名单和现有公开合同。

| 类型 | 改动和验收 |
| --- | --- |
| EXISTING | 盘点实际 Catalog、Manifest、Smoke、锁、Workflow 引用和组合白名单。用阶段 0 和签入的生成清单交叉核对。 |
| EXTEND | 在 `platform/assembler/operator_metadata.py` 的装配入口读取 v2。校验版本化 Schema、相对路径和摘要，沿用 v1 技术摘要兼容投影。回归测试覆盖摘要漂移、未知版本、历史 v1 和独立科学复核。 |
| NEW | Groundwater 固定引用新证据文件，试点 Native 与远程算子的两个版本和一个 QGIS 算子。用实际离线命令生成技术证据，并在报告中给出业务元数据 Schema 提案和独立的锁依赖原型。 |
| DECISION | 科学复核保留可追溯状态，证据对默认曝光及准入的任何新政策由产品负责人另行确认。依赖分析先作为原型，生产使用现有 implementation lock。 |

先写合同和兼容性负例，再完成装配接线和证据试点。验证包括产品装配与配置比对、Catalog 与 Kernel 回归、运行时可用范围内的 Smoke、公开客户端快照和 Git 差异。风险集中在错误继承旧技术记录、错误挂载科学结论和按 ID 忽略版本，分别用投影对比、独立必要证据和精确版本测试覆盖。试点收尾以本文件记录证据矩阵、命令、结果和后续决策。

## 当前 Catalog 健康度

基线提交为 `bd5d58ab63e16b2f5a59ceabcc7faf1c4c71f411`，核查日期为 2026-11-20（Asia/Shanghai），活动领域包为 `groundwater@6.2.0`。统计来自 Catalog loader、索引、装配和领域工作流，不沿用手写数量。`capability-inventory` 的生成参考表保存全部精确 ID 和引用。 [生成清单](../../source/docs/development/current-capability-inventory.md#operator-catalog-摘要) · [产品选择](../../source/products/groundwater/product-manifest.json)

| Runtime | Manifest / 精确版本 | 唯一 ID | 预设使用版本 / 节点数 | 组合成员 |
| --- | ---: | ---: | ---: | ---: |
| Native | 86 | 85 | 40 / 74 | 49 |
| QGIS | 15 | 15 | 3 / 8 | 12 |
| Remote HTTP | 12 | 11 | 11 / 13 | 1 |
| 合计 | **113** | **111** | **54 / 95** | **62** |

113 对 Manifest/Smoke 声明、锁行及索引成员完全一致。版本字面量为 `1.0.0`（111 行）和 `1.1.0`（2 行）。预设与组合共享 3 个版本，其余 51 个仅由预设引用，59 个仅在组合中。执行 Catalog 摘要为 `sha256:47eb493ad355de2062110e459c5d69368fd6de457d5debabc9f2f8c2363f4494`。Phase 0 装配与 Phase 1 的完整 `workflowAuthoring` 配置逐对象一致，包含 113 个已启用精确版本、62 个组合成员和原有技术摘要。

113 条历史技术记录均与当前实现 revision 匹配，按原口径有 62 条浏览器单节点、40 条 Worker 单算子和 11 条远程预设节点。当前没有历史科学复核附件。历史 `reportChecksum` 仍保留原始引用，在本工作区可校验的是 v1 文件及其绑定。此次技术回执另有本地文件和独立摘要。 [历史摘要](../../source/products/groundwater/operator-validation.v1.json) · [本轮回执](../../source/products/groundwater/operator-evidence/phase-1-offline.v1.json)

## 治理模型和证据合同

产品清单通过已有的 `operatorValidation.path/checksum` 固定 `operator-validation.v2.json`。v2 以精确 `operatorId@version` 为键，核对构建时 Catalog 成员。`legacyTechnicalEvidence` 可引用原 v1，保留运行中的技术摘要和旧版解析兼容。产品新配置包含新证据 pin，重新装配时只有 Kernel 的清单摘要和 assembly lock 发生相应变化。 [产品证据文件](../../source/products/groundwater/operator-validation.v2.json) · [v2 Schema](../../source/platform/assembler/operator-validation.v2.schema.json) · [装配加载](../../source/platform/assembler/operator_metadata.py)

- 技术记录固定 runtime、Manifest digest、implementation revision 和 Smoke 声明 digest。每个验证层独立记录状态、日期、范围和文件引用。已执行的记录必须匹配 `operator-evidence-receipt.v1` 内同一个算子身份、验证层、结论、日期和范围。装配用同一份内容校验并解析 JSON。摘要错误、版本未知、越过产品目录及跨算子回执替换拒绝装配。
- 证据记录的结论对应当时的执行。当前健康度通过与活动 Catalog 的精确身份及 Smoke 声明比较计算。更换夹具、Worker 镜像、算法或外部服务版本时，还需按相应验证层复跑。历史技术投影以原实施 revision 判断 `PASSED` 或 `STALE`，并保留 `NOT_RECORDED`。五个试点补充了证据深度，公开技术摘要沿用原口径。
- 科学复核独立记录。`NOT_ASSESSED` 给出原因。`REVIEWED` 需要审查人、范围、日期、独立的 benchmark 和 domain review 文件及自身精确 Manifest/implementation 绑定。路径和内容都需区分，两个文件分别校验。绑定当前实现时只投影为原公开合同的 `REVIEW_EVIDENCE_ATTACHED`。科学附件的有效性与历史技术记录是否存在或过期分别判断。复核人由产品证据提供方登记，签名授权和准入政策仍由产品负责人决定。 [公开投影](../../source/kernel/src/platform_kernel/application/operator_catalog.py)

| 层级 | 证据及本轮试点 |
| --- | --- |
| `SCHEMA_CONTRACT` | 113 对声明的 Schema、端口、参数和实现锁验证通过。五个试点均有与精确身份绑定的报告。 |
| `UNIT` | 本轮运行了 Pack、Compiler 和治理单测。逐算子的独立 unit 报告由该层单独登记。 |
| `SMOKE` | Native、QGIS 的全部 101 个版本执行了实际任务子进程。三条本地试点保存了输入夹具和输出摘要。Remote HTTP 的 12 个声明端口检查归入合同验证，两个 PEST 试点的后端执行为 `NOT_ASSESSED`。 |
| `INTEGRATION` | 本轮离线装配、公共合同和 Kernel 组合边界用例覆盖了跨组件读取。带真实 Worker 服务的逐算子链路需要对应运行回执。 |
| `DOMAIN_CASE` | 需要明确数据、参数、预期指标、适用区域及容差。本轮试点未登记该层。 |
| `REGRESSION` | 以 Phase 0 装配做全部精确算子身份兼容、引用与白名单等价比较。逐算子业务结果基准应在该层另外登记。 |
| 科学与专业复核 | 全部试点为 `NOT_ASSESSED`。科学适用性由独立基准与专业意见共同支撑。 |

### 五个精确试点

下表 `CURRENT` 表示技术记录的四项身份与当前索引匹配。证据日期为 2026-11-20。Native/QGIS 镜像运行当前仓库只读挂载的 Pack，容器禁网并按本机镜像 ID 启动。回执中保存三个 Worker 镜像的 SHA-256、Python 版本、全量精确算子列表，以及本地三个试点的夹具、参数、调用和输出摘要。 [原始回执和镜像身份](../../source/products/groundwater/operator-evidence/phase-1-offline.v1.json)

| 算子 | 当前身份 | 预设引用 | 组合成员 | Smoke | 科学复核 |
| --- | --- | --- | --- | --- | --- |
| `raster.reproject@1.0.0` | CURRENT | `groundwater.transfer-learning-analysis@1.0.0` | 否 | PASS | NOT_ASSESSED |
| `raster.reproject@1.1.0` | CURRENT | 无 | 是 | PASS | NOT_ASSESSED |
| `vector.spatial_join@1.0.0` | CURRENT | 无 | 是 | PASS | NOT_ASSESSED |
| `groundwater.porous.pest_calibrate@1.0.0` | CURRENT | 无 | 是 | NOT_ASSESSED | NOT_ASSESSED |
| `groundwater.porous.pest_calibrate@1.1.0` | CURRENT | `groundwater.porous-calibration@1.0.0` | 否 | NOT_ASSESSED | NOT_ASSESSED |

## 业务元数据 Schema 提案

推荐独立的 Product/Domain sidecar。业务名称、场景、术语和成熟度可以独立演进，装配时用精确版本引用。若把这类数据写入 Manifest，变化会进入现有实现身份、Catalog 摘要和工作流兼容范围。只有需要跨产品一致的执行含义时，才有演进 Manifest 的理由。本阶段提交的是下列草案。字段均为展示或治理说明，附加元数据后的发布与准入仍使用现有合同。

| JSON 字段 | 类型与校验 |
| --- | --- |
| `schemaVersion` | 常量 `operator-business-metadata.v1`。 |
| `operators` | 对象。键为 `operatorId@version`，由产品装配验证。每项拒绝未知字段。 |
| `displayName`、`purpose` | 必填非空字符串。分别表示展示名和可处理的业务目标。 |
| `useCases`、`limitations` | 字符串数组。用明确适用场景和限制描述用户可核验的条件。 |
| `inputRoles`、`outputMeanings` | 端口名到非空字符串的映射。端口必须存在于该精确 Manifest，内容描述业务角色或结果含义。 |
| `maturity` | `CORE`、`SPECIALIZED`、`ADVANCED`、`EXPERIMENTAL`、`UNKNOWN`。层级由产品负责人确定。 |
| `requiredEvidence` | 去重枚举数组，引用技术验证层或 `SCIENTIFIC_REVIEW`。用于说明证据要求。执行限制须单独审批。 |

例如 `vector.spatial_join@1.0.0` 的 `target` 应描述为保留空间位置的目标图层，`join` 应描述为提供匹配属性的图层。结果业务含义及限制可呈现匹配规则、CRS、重复匹配和字段冲突。它们与当前轮的角色确认配合，输入验证继续使用精确 Data 引用与服务端规则。 [算子 Manifest](../../source/packages/operator-pack/catalog/operators/vector.spatial_join/1.0.0/manifest.json) · [显式角色复核](../../source/kernel/src/platform_kernel/agent_runtime/input_roles.py)

## 目录发现和权限

普通执行提案已有预设优先提示词，用户明确选择的 Workflow 限定模型工具。工作流抽屉优先展示预设，原子目录由用户切换。高级组合使用 `list_operators` 的关键词、领域、格式、输出筛选和确定性游标，每页 1–50 条，每轮每个查询至多 3 页。`get_operator` 读取精确 ID/版本，返回端口、参数、解释、运行时及兼容的证据投影。普通登录成员仍可通过已有的 capabilities 和按需目录读取启用算子。将原子目录限定为专家角色属于新的访问策略。 [预设优先](../../source/kernel/src/platform_kernel/agent_runtime/prompts.py) · [目录工具](../../source/kernel/src/platform_kernel/agent_runtime/tooling/catalog_tools.py) · [产品抽屉](../../source/web/src/ProductDrawers.tsx)

验收用例从试点实际装配读取 `raster.reproject@1.0.0`。它的历史技术摘要可读，`confirmation_supported=false`。同一轮引用实际读取结果提交组合草稿，Kernel 仍返回 `COMPOSITION_OPERATOR_UNSUPPORTED`，且执行资格未开启。另一个精确版本 `1.1.0` 保持在组合白名单。证据文件没有向 Kernel 或 Executor 增加准入条件。 [目录与准入回归](../../source/tests/assembler/test_operator_validation.py) · [草稿准入](../../source/kernel/src/platform_kernel/agent_runtime/workflow_draft.py)

## implementation lock 依赖原型

只读脚本复算 113 条 `sourceDigest` 与 `implementationRevision`，从静态分发表、Python 模块 AST、延迟 runner 及 Worker/子进程入口推导可见依赖，再与 Dockerfile 的 `COPY`、基础镜像和锁定依赖输入合并保守复核范围。远程模型额外跟踪服务和 operation binding，无法判定的路径保守纳入全目录。输出三列分别用于比较生产锁、Python 可见依赖和需要复核的上界。当前锁保持原有刷新算法。 [原型](operator_lock_impact.py) · [现有刷新入口](../../source/scripts/development/refresh_operator_lock.py) · [静态分发表](../../source/packages/operator-pack/src/moperators/static_implementations.py)

| 变更样例 | 当前锁影响 | Python 可见范围 | 保守复核上界 |
| --- | ---: | ---: | ---: |
| `operators/native.py` | 101 | 101 | 113 |
| `operators/analysis.py` | 86 | 68 | 113 |
| `operators/raster_reference_grid.py` | 2 | 2 | 113 |
| `operators/qgis.py` | 15 | 101 | 113 |
| `static_implementations.py` | 113 | 101 | 113 |
| `runtime/task.py` | 101 | 101 | 113 |
| Executor `remote_http.py` | 0 | 113 | 113 |
| Executor `operator_child.py` | 0 | 101 | 113 |
| Native 运行依赖锁 | 0 | 0 | 86 |
| QGIS Dockerfile | 0 | 0 | 15 |
| `porous-groundwater` 服务 | 0 | 0 | 11 |
| 无法识别的运行依赖 | 0 | 0 | 113 |

旧 Native 栅格重投影走通用分发表，新版走延迟加载的 reference-grid 实现。QGIS 空间连接跨 Python 和 QGIS processing。远程 PEST 的两个版本使用同一已绑定服务操作，数值程序位于远端。三个 Worker 镜像都打包完整 Pack 和构建时校验入口，因此镜像字节变化需要复核，即使当前实现锁指标为 0。AST 数字可能漏掉插件驱动、动态导入、原生库和外部服务，结果仅用于评估扩散，无法单独证明缩小锁的安全性。 [Native 镜像](../../source/services/executor/Dockerfile.native) · [QGIS 镜像](../../source/services/executor/Dockerfile.qgis) · [Remote HTTP 镜像](../../source/services/executor/Dockerfile.remote-http)

运行原型可用 `python3 -B plans/groundwater-agent-v2/operator_lock_impact.py`。本轮另在内存捕获刷新入口的索引和锁输出，两份内容逐字节等于现有文件。独立运行与对比数据可在本次项目忽略缓存下查看。 [实现锁](../../source/packages/operator-pack/catalog/implementation-lock.json) · [Catalog 索引](../../source/packages/operator-pack/catalog/index.json)

## 本轮验证与交付范围

Python 使用 `./bin/bootstrap --status` 确认的 CPython 3.12.8。宿主组件检查在只启用 loopback 的网络命名空间中执行，允许本机模拟服务。临时 XDG 数据、状态、装配输出和测试目录放在项目内忽略缓存中。测试使用仓库内校验过的 tokenizer 词表；镜像使用本机固定 SHA-256、`--pull=never --network=none` 和只读 Pack 挂载。

| 实际命令或入口 | 已核验的结果 |
| --- | --- |
| `../bin/dev check --scope assembler` | 产品编译，141 项装配测试、Ruff、Git 差异、Core import 与架构检查通过。历史证据可扩展性用例加入后，最后一次定向回归重新读取了最终文件。 |
| `../bin/dev check --scope kernel` | 912 项 Kernel 测试通过。mypy 检查 9 个源文件通过，Ruff、Git 差异、Core import 与架构检查通过。 |
| `../bin/dev check --scope contracts` | 190 项合同测试通过。服务运行态 OpenAPI、Web 客户端与验证器、Python SDK 快照和静态检查通过。 |
| `pytest tests/assembler/test_operator_validation.py tests/assembler/test_groundwater_generated_baseline.py kernel/tests/test_operator_governance.py tests/architecture/test_wheel_contents.py::test_assembler_wheel_audit_matches_packaged_runtime_schemas` | 29 项通过。包括历史 Catalog 增量与 stale binding、证据摘要、路径和结论错绑、独立复核、目录读取与组合拒绝。 |
| `pytest packages/workflow-compiler/tests packages/operator-pack/tests/test_static_pack.py tests/cli/test_capability_inventory.py` | 116 项通过，覆盖编译、筛选分页、静态 Pack 合同和生成清单。 |
| `python -m scripts.checks.check_native_tests` | 171 项通过。使用锁定 Python 和已有 wheelhouse，在项目内隔离副本构建 Pack 及 Executor Wheel，再执行完整 Native 算子和子进程合同套件。38 条第三方库弃用提示随日志保留。 |
| `generate_capability_inventory(..., check=True)`、两次实际装配的规范化产物对比 | PASS。三套产品数据源身份对账，`workflowAuthoring` 与 Executor 配置相同，规范化后仅 Kernel 配置和 assembly lock 因证据 pin 更新。 |
| `uv build --offline --no-index ...` 和 `python -m scripts.release.check_wheel_contents platform-assembler <wheel>` | 真实 Assembler Wheel 构建和内容检查通过。临时环境安装后通过包内 v2 Schema 读取实际产品文件，投影全部 113 条历史记录。 |
| `run_smokes`、`run_operator_smoke`，在 3 个缓存 Worker 镜像中运行 | Native 86、QGIS 15 次实际 Smoke 通过。Remote HTTP 12 条端口合同通过。三个本地试点另行生成精确运行回执。镜像的 GDAL/Qt 及无扩展名夹具读取警告未改变通过结果。 |
| `python -m scripts.checks.check_documentation`、`python -m scripts.checks.check_worktree_hygiene` 和原型脚本 | PASS。原型复算实现锁及各影响场景，五个试点回执在报告的健康度脚本中均为 `CURRENT`。 |

验证日志和可复算中间文件位于 `.pytest_cache/gw-v2-phase1-mkonq5u6/`。`assembly-proof.json` 记录产物字节比较，`catalog-health.json` 记录版本、预设引用与证据状态，`lock-impact.json` 保存原型完整输出，三个 runtime 的 JSON 保存本次 Smoke 明细。可长期查阅的证据摘要固定在产品目录。

先前的装配用例使用了不完整的 SDK 工具上下文，在改用真实 `ToolContext` 后复跑通过。首次合同检查的隔离环境未启用 loopback，启用后 190 项全部通过。早期 Kernel 运行的 `test_submit_rechecks_frozen_local_model_before_new_remote_action[model_version-SUBMIT_UNKNOWN]` 在读取时只剩 21.2 秒，低于用例的 25 秒下限。最终独立运行的 912 项通过。

变更范围包括产品 v2 证据与离线回执、Assembler Schema 与加载器、Kernel 兼容读投影、Assembler Wheel 审计、对应回归和生成清单。当前公开 API 及数据库使用原合同。工作流、算子 Manifest、实现锁、执行权限和白名单继续使用既有版本。阶段 0 报告保留为起始审计材料。

### 阶段退出

| 验收条件 | 结论与证据 |
| --- | --- |
| 旧 Operator 精确身份可解析 | 阶段 0 装配的 113 个 `ID@version/revision` 全部可解析。两个多版本 ID 分别核对预设和组合用途。 |
| 过期实现不能被当作当前验证 | 过期与缺失有独立测试，发布投影只在技术 revision 匹配时给出原技术通过状态。v2 复核绑定自己的 revision 与 Manifest，回执错绑拒收。 |
| 业务元数据保持身份稳定 | Schema 建议落在 sidecar。实际产品证据变更前后执行 Catalog、Executor 配置和已冻工作流身份一致。 |
| Runtime Smoke 可追溯 | Native/QGIS 实际执行回执及 Remote HTTP 声明合同均按镜像和精确版本记录。实际远程执行和独立科学复核为未评估。 |
| 目录读取保留组合白名单 | 真实目录读到历史版本后，同轮组合仍被拒绝。62 个既有组合版本全部保留。 |
| 新合同有版本和回归 | `operator-validation.v2` 和 `operator-evidence-receipt.v1` 均经装配与正反例验证。打包版 Schema 已安装核验。 |

阶段 1 的本地试点和验证满足退出条件。证据的签署角色、下游证据撤回政策，以及真实远程和科学验收仍需要相应业务证据。当前结果留在工作区，HEAD 为 `bd5d58a`。

## 复算试点健康度

从 `source/` 使用已锁定的 Python 在隔离网络命名空间执行以下脚本。它通过正常装配加载器校验 v2 和历史文件，重读 Catalog 并输出两个独立状态。对本地试点，已记录的夹具引用也与当前夹具索引比较。回执的镜像身份用于复现实验环境。

```python
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from scripts.development.capability_inventory import _load_catalog_index
from assembler_domain import _load_operator_catalog
from operator_metadata import load_operator_validation


def read(path):
    return json.loads(path.read_bytes())


root = Path.cwd()
directory = root / "products/groundwater"
product = read(directory / "product-manifest.json")
reference = product["operatorValidation"]
ledger = read(directory / reference["path"])
receipt = read(directory / "operator-evidence/phase-1-offline.v1.json")
catalog, _ = _load_operator_catalog(root)
index, _, _ = _load_catalog_index(root)
rows = {(row["id"], row["version"]): row for row in index["operators"]}
fixture_index = root / "packages/operator-pack/catalog/fixtures/index.json"
fixtures = read(fixture_index)["fixtures"]
projection = load_operator_validation(directory, reference, operators=catalog)
legacy = Counter()
for identity, operator in catalog.items():
    revision = projection.get("@".join(identity), {}).get("implementationRevision")
    if revision is None:
        legacy["NOT_RECORDED"] += 1
    else:
        legacy["PASSED" if revision == operator["implementationRevision"] else "STALE"] += 1
print("legacy", dict(legacy))

for key, entry in ledger["operators"].items():
    identity = tuple(key.split("@", 1))
    record = entry["technicalVerification"]
    runtime = record["runtime"]
    current = record["implementationRevision"] == catalog[identity]["implementationRevision"]
    current &= all(record[field] == rows[identity][field]
                   for field in ("manifestDigest", "runtime", "smokeDigest"))
    sample = receipt["workerSmokes"][runtime]["pilot"].get(key, {})
    for inputs in sample.get("fixtures", {}).values():
        for item in inputs:
            fixture = fixtures.get(item["fixtureId"], {})
            current &= all(fixture.get(field) == item[field] for field in
                           ("sha256", "sizeBytes", "format", "mediaType"))
            if fixture:
                path = (fixture_index.parent / fixture["path"]).resolve()
                if path.is_relative_to(fixture_index.parent) and path.is_file():
                    data = path.read_bytes()
                    current &= (len(data) == item["sizeBytes"]
                                and "sha256:" + sha256(data).hexdigest() == item["sha256"])
                else:
                    current = False
    print(key, "CURRENT" if current else "STALE",
          {name: check["status"] for name, check in record["checks"].items()},
          entry["scientificReview"]["status"])
```
