# 仓库目录与文件归属

源码树只包含可审查输入，不拥有机器运行态。

| 路径 | 内容 |
| --- | --- |
| `kernel/` | Public API v12、Agent、Session、确认与 Workflow |
| `services/` | 独立运行服务与其客户端、管理 UI |
| `packages/` | 跨模块合同、纯编译器、SDK 与 UI 边界 |
| `domains/`、`products/` | 版本化领域和产品声明 |
| `platform/assembler/` | 确定性静态装配 |
| `environments/` | Python ABI 与系统依赖输入 |
| `schemas/operations/` | 当前实例与布局 JSON Schema |
| `scripts/development/` | 编译、合同生成、快速运行与按范围选测 |
| `scripts/maintenance/` | XDG、依赖、实例、快照、归档、迁移与清理 |
| `tests/` | 跨模块、架构、CLI 与合同测试 |

`.env`、虚拟环境、`node_modules`、`.generated`、`dist`、`build`、数据库、对象数据、密钥、
活动 manifest 和长期 cache 禁止进入源码事实。生成输出写入 XDG cache，回执写入 XDG state，
实例写入 XDG data。服务之间只通过声明的 HTTP/SDK 合同协作。
