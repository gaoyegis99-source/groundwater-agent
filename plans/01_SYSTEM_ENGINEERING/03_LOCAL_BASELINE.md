# 系统工程包本地基线

核验日期：2026-11-12（Asia/Shanghai）。本文件记录活动计划建立时的本地事实；后续执行仍须从当前清单和测试重新核实。

| 项目 | 结果 |
|---|---|
| 仓库根 | `/home/mtd/liweigis/workflow/groundwater-v6` |
| 基线提交 | `19175f592697999b895d6d3ebdb25c6d06d39455` (`origin/main`) |
| 活动分支 | `codex/workspace-hygiene` |
| 源码根目录 | `source/` |
| Python 工具链 | PASS；CPython 3.12.8，位于 XDG cache |
| 依赖范围 | `python`、`npm`、`system` 均为 PASS |
| 维护诊断 | 清理实施前 `gw doctor` 为 PASS；清理器会将可重建残留报告为 `WORKSPACE_RESIDUE_PRESENT` |
| 产品 manifest | `product-manifest.v8`；产品 API v12 |
| 激活 Domain Package | `groundwater@6.1.0` |
| 激活 checksum | `sha256:8662efa7382bbe4f9fe7c35fd873bf7109a4d75ee3213fd4bb0fdc92b4c4b10d` |
| 测试 scope | `kernel`, `data`, `knowledge`, `executor`, `assembler`, `contracts`, `web`, `integration`, `all` |
| Compose/运行配置 | 研究实例通过 XDG lifecycle 管理；默认 Gateway `28186` |

事实入口：

- `source/products/groundwater/product-manifest.json`
- 产品清单选择的 Domain Package manifest
- `source/AGENTS.md`
- `source/docs/architecture/architecture-contract.md`
- `source/docs/operations/execution-playbook.md`

历史审计只作为 Git 历史中的线索，不得直接当作当前事实。

## SYS-01 执行基线

2026-11-12（Asia/Shanghai），`codex/workspace-hygiene`，HEAD `cc83e9339c165eb424271d4ef46db93cc32ca8cc`。仓库根与上表一致，唯一检出的仓库内 `AGENTS.md` 位于 `source/`。初始工作区有 12 个已跟踪修改和 3 个未跟踪文件，涉及交互式增量开发、`check_change.py`、CLI 测试及交互模式文档。

| 核验 | 证据 |
|---|---|
| 产品与激活领域 | `product-manifest.v8`、Public API v12、`groundwater@6.1.0`、`domain-manifest.v3`；产品选择的包 checksum 与上表一致 |
| 算子目录 | `packages/operator-pack/catalog/index.json`，`operator-catalog-index.v1`，111 条算子（84 Native、15 QGIS、12 remote_http）；目录 digest `sha256:5a9d8c409454a44af27544a8a3d010953b8acd15543000dbfb5c7dddd473fcb8` |
| 装配输入 | `../bin/dev capability-inventory --verify` 退出 0；验证 18 个 workflow、18 个模型、54 个实际被 workflow 引用的算子，生成清单与签入文件逐字节一致 |
| 工具链与依赖 | `../bin/gw bootstrap --status` 退出 0，锁定 CPython 3.12.8；`../bin/gw deps status` 退出 0，python/npm/system 均 PASS；Node v22.23.1、npm 10.9.8 |
| 测试入口 | `../bin/dev check --help` 与 `../bin/dev compile --help` 均退出 0；scope 与上表一致；`source/web/package.json` 声明 Vitest、tsc、Vite 与 Playwright 入口 |
| 本地运行实例 | `../bin/dev status --product groundwater` 退出 0，`status=PASS`、`issues=[]`，报告 dirty 源码快照 `cc83e93`；Kernel、Data、Knowledge Next、Executor Control 与三个 Worker runtime 就绪。远程服务调用及真实 Provider 本卡 NOT_RUN |
| 差异检查 | `git diff --check` 退出 0。本卡仅更新计划证据，产品测试留待对应任务卡 |
