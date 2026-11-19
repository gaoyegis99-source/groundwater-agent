# 本轮测试与验证证据

源码基线：`e6d8512797a128887cf2bab41a92629ddb06b668`。执行日期：2026-11-14。只运行隔离检查，没有启动产品服务或连接活动数据库；测试自身使用临时目录及模拟/临时SQLite。没有请求真实模型或Embedding Provider，也没有运行QGIS/远程求解器/真实训练。

## 数量口径

- `reproduce_inventory.py`对当前跟踪Python AST统计：2,131个`test_*`函数、254个含测试定义文件。参数化、异步和独立包使该数不能与pytest case数直接相加比较。另有68个JS/TS `.test/.spec`文件，本轮未执行其前端测试。
- 根`source`的pytest配置实际收集2,287项，见[collect.log](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/collect.log)及[命令元数据](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/collect.json)。它不涵盖全部operator-pack、lightweight及third-party独立套件；收集成功不等于执行成功。
- 本轮最终通过540个不同的选定case：495个主合同/准备/装配等检查（493首轮通过+2单独重跑通过）及45个算子检查。不是“2287全部通过”，也不应把两次执行同一case重复计数。

## 完整执行记录

| 记录 | 范围 | 结果 | 含义 |
|---|---|---|---|
| collect | `source`根`--collect-only -q` | exit0；2287 collected，pytest报告2.54秒 | 验证当前可收集范围 |
| contracts-attempt1 | 目标套件误包含不存在的`services/data/tests/test_json_schema_validation.py` | exit4；no tests ran | 本轮检查脚本选错路径，不是产品测试失败；已保留日志 |
| contracts | `tests/contracts`、`tests/assembler`、Executor remote_http/schema、Kernel internal_modules/settings、Knowledge retrieval_budget | exit1；493 passed、2 failed，323.95秒 | 两项失败来自本轮隔离环境设置，详见下文 |
| retry | 上述两项失败case | exit0；2 passed，3.90秒 | 修正检查环境后通过；没有修改生产源码 |
| operators | 8个专业算子测试文件 | exit0；45 passed，2.23秒 | 验证所选数值/边界/语义行为；部分测试含mock |
| preparation boundary probe | 真实`prepare_interruption`函数+真实flow-field输入schema，合成工具轨迹 | READY、1个Action、只有DATA_IDENTITY | 复现风险现状，不是修复后回归通过，不是现场执行 |

日志与精确命令：[首次路径错误](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/contracts-attempt1.log)、[主检查](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/contracts.log)、[重跑](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/retry.log)、[算子](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/operators.log)。各同名JSON（主检查为contracts.json、算子为operators-check.json、重跑为retry-check.json）包含cwd、UTC起止时间、完整argv及exit code。日志的pytest耗时与外层进程总耗时口径不同。

### 两项失败及归因

1. `tests/contracts/test_agent_evaluator.py::test_compose_compiles_temporary_sources_and_cleans_up`：本轮先将`PLATFORM_GENERATED_ROOT`指到`/tmp/groundwater-assessment-*/assembly`，触发`PATH_OUTSIDE_OWNED_ROOT`。重跑改用允许的XDG build根下独立`repository-assessment-*`目录；没有复用或覆盖活动装配目录。
2. `tests/contracts/test_groundwater_resource_wiring.py::test_sdk_configuration_error_terminalizes_run_session_and_trace`：本轮设置`OPENAI_AGENTS_DISABLE_TRACING=1`，使测试要求的错误trace不可见。重跑去掉该设置后通过；该测试模拟SDK配置错误，不需要真实Provider。

这是环境调整后对原失败case的定向重跑，**没有重新执行整495项套件以证明同一进程中全绿**。原失败证据完整保留，不能将其抹去或描述成首次全部通过。

## 环境与可复现入口

主检查使用现成项目Python3.12：`/home/mtd/.cache/groundwater-v6/toolchains/v6-d2879b157dedc56a/bin/python`。算子检查使用现成科学环境Python3.13：`/home/mtd/miniconda3/bin/python3`，显式PYTHONPATH和独立pytest配置；因此算子通过**不构成锁定Native环境的发布验收**。没有安装/升级依赖。

[run_checks.py](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/run_checks.py)支持`collect`、`contracts`、`retry`、`operators`。最终保留的重现脚本已采用修正后的构建路径和tracing设置；后续运行写带时间戳的`recheck-*`日志，保留本轮原始证据。它设置`PYTHONDONTWRITEBYTECODE=1`、禁用自动加载pytest插件与cacheprovider、分配独立basetemp，清除TEST数据库/URL环境变量。路径是本机环境记录，不保证别的机器直接可用。

在仓库根可以复查清单或重跑选定检查（本轮已完成，无需为报告再次执行）：

```bash
python3 plans/repository-assessment/evidence/reproduce_inventory.py
python3 plans/repository-assessment/evidence/build_detail_tables.py
python3 plans/repository-assessment/evidence/build_source_index.py
python3 plans/repository-assessment/evidence/build_capability_evidence.py
python3 plans/repository-assessment/evidence/run_checks.py contracts
python3 plans/repository-assessment/evidence/run_checks.py operators
```

前两个清单脚本顺序执行，后者补回算子分类，避免以原始清单覆盖详细矩阵。清单脚本只写报告目录。纯函数探针见[probe_preparation_boundary.py](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/probe_preparation_boundary.py)，输出为[preparation-boundary-probe.json](/home/mtd/liweigis/workflow/groundwater-v6/plans/repository-assessment/evidence/preparation-boundary-probe.json)；使用上述项目Python及`PYTHONDONTWRITEBYTECODE=1`可重现，脚本自身不写数据库/知识/源码。

## 算子测试具体范围

`test_flow_field_interpretation.py`、`test_kriging.py`、`test_monitoring_trend_expansion.py`、`test_drastic.py`、`test_table_classify.py`、`test_transfer_learning_audit_semantics.py`、`test_summary_scientific_semantics.py`、`test_render_map_scientific_expression.py`，均位于`source/packages/operator-pack/tests/`。

它们覆盖水头/梯度结果解释、Kriging数值/诊断、趋势扩展、规则/NoData边界、迁移学习训练切分与单步回测含义、统计与制图科学表达。包括合成输入和mock，不能推定真实井网、外部基准、跨场地泛化、全部QGIS行为或神经网络训练已经验证。

## 明确未运行

| 验证层 | 状态 | 不作的结论 |
|---|---|---|
| 全量根pytest、独立lightweight、前端/浏览器、全部operator smoke | NOT_RUN | 不宣称全仓回归通过 |
| Data/Knowledge PostgreSQL/pgvector集成与真实导入 | NOT_RUN | 活动库schema、数据质量、索引性能UNKNOWN |
| 真实SDK/Provider/Embedding、全流程用户交互 | NOT_RUN | 工具实际选择、token成本、召回污染、用户体验UNKNOWN |
| QGIS、远程HTTP真实服务、Porous vendor/NWT/MT3D/PEST | NOT_RUN | 运行可用性与外部依赖身份UNKNOWN |
| MODFLOW6/Pastas真实外部二进制/环境、GRU训练 | NOT_RUN | spike/案例可运行性、预测精度UNKNOWN |
| 现场独立资料、数值收敛/质量守恒完整验收 | NOT_RUN | 科研结果准确性、监管适用性UNKNOWN |
| 活动数据库、用户数据、真实归档恢复、生产安全渗透 | NOT_RUN | 使用情况、外部消费者、生产安全保证UNKNOWN |

源码中存在其他验证脚本和历史验收材料时，只记录“实现/材料存在”，不把其历史结果登记为本轮通过。报告完整性与源码未改检查另见REPORT_VALIDATION.json。
