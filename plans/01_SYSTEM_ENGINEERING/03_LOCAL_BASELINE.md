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
