# Groundwater V12 科研验收

V12 将验收拆成互不冒充的三项状态：

| 状态 | 内容 | 是否阻塞 benchmark 归档 |
| --- | --- | --- |
| `scientificStatus` | 输入 checksum、参数、随机种子、算法版本、数值基准、容差、lineage、领域验证 | 是 |
| `engineeringStatus` | 受影响测试、合同、迁移、Compose smoke、快照恢复、归档重建 | 是 |
| `agentExperienceStatus` | Provider 回复质量和人工评审 | 否 |

每项使用 `PASS`、`FAIL` 或 `NOT_ASSESSED`。`experiment` 归档接受三种结果，以便保存失败实验；`benchmark` 归档要求 scientific 与 engineering 均为 `PASS`。

日常变更至少运行受影响测试、静态检查和 `git diff --check`。涉及合同、迁移、权限或存储时，必须补充对应矩阵、逐版本迁移和失败注入测试。完整运行态验证应检查容器、服务 readiness、Executor control/native/QGIS 能力以及实际镜像和源码摘要。

归档入口：

```bash
gw archive create --instance ID --run RUN_ID --level experiment
gw archive create --instance ID --run RUN_ID --level benchmark
gw archive verify --archive PATH
```

研究包保存源码快照、dirty patch、依赖锁、装配配置、环境摘要、输入/输出 checksum、参数、随机种子、实现摘要、lineage 和三类验收报告，不保存 secret。签名和加密是可选增强，不是归档前置条件。
