# 05 M0：本地快速反馈与 AI 可执行入口

## 目标
让 Vibecoding AI 对一个组件、一个 Agent case、一个 operator、一个 workflow 能快速验证，不必每次运行完整系统。

## M0-01 先核实开发命令（P0）
先读：
- `source/docs/operations/execution-playbook.md`
- `source/scripts/development/`
- `source/scripts/checks/check_change.py`
- `source/bin/dev`（仓库根）
- `source/AGENTS.md`

当前 owning docs 说明常用：
```bash
../bin/bootstrap --sync
../bin/dev compile --product groundwater
../bin/dev check --scope all
```
以及 `npm test`、`npm run typecheck`、`npm run build`、`python -m pytest -q`。Operator Pack 不由普通 root pytest 收集；`check_native_tests` 有独立 Native 锁域。

任务：确认这些命令在当前 checkout 的真实入口、参数、前置条件和输出目录。不要新建第二套命令。

验收：新增 `docs/development/vibecoding-quick-reference.md`，只记录已实际运行的命令；未运行写 NOT_RUN。

## M0-02 单算子快速运行（P1）
先读：
- `packages/operator-pack/src/moperators/runtime/task.py`
- `packages/operator-pack/src/moperators/smoke.py`
- `packages/operator-pack/catalog/fixtures/index.json`
- `packages/operator-pack/catalog/operators/<operator>/<version>/smoke.json`

若已有 smoke 足够，先写包装脚本复用原执行路径，不复制 runtime。
目标命令形态由当前 repo CLI 决定，可支持 operator id/version、fixture、参数覆盖、输出目录、摘要和 checksum。

验收：选 `timeseries.gap_runs` 或 `statistics.pettitt_change_point` 跑现有 smoke；非法参数得到稳定错误码；输出写入 XDG cache 而不是源码目录。

## M0-03 Workflow golden runner（P1）
先核实是否已有 workflow case runner。若已有则扩展，不要另建 executor。
至少为现有 `monitoring-trend`、`render-map`、`flow-field` 选取可用 fixture，记录 workflow checksum、operator catalog digest、输出身份和关键数值。
验收：重复运行 golden 稳定；golden diff 能指出具体字段或数值变化。

## M0-04 Agent 离线回放（P0）
先复用 `kernel/tests` 中 ScriptedModel/ModelStep 设施。
新增 case 覆盖：
- 普通 DIALOGUE 不执行
- 明确执行提案停在 Confirmation
- 缺字段后要求用户修正
- fan-out 草稿
- MiniMax 非案例执行终态
- 引用必须来自本轮已 read 的 Knowledge unit

验收：重复 3 次结果一致；确认次数与 WorkflowRun 数量正确；不变量逐项断言。

## M0-05 当前功能清单生成（P1）
从 `domain-manifest.json`、`operator catalog index` 和 product-ui 读取真实清单，生成开发参考表。
不要硬编码“六类模型”或手写算子数量。
