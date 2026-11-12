# 腰站堡迁移学习案例载荷

本目录使 V6 的腰站堡迁移案例不再读取工作区的 `data/`、
`deliverables/` 或旧的 `projects/platform/groundwater/v6-imports/` 目录。

- `relocation.v1.json` 和 `integrity.v1.json` 受 Git 跟踪，定义唯一的
  本地载荷布局、原始清单摘要与每项资源的内容身份。
- `payload/` 保存在 `$XDG_DATA_HOME/groundwater-v6/case-assets/yaozhanbao-transfer-case/`
  下；它包含不可变导入证据、19 项开发案例输入及其二进制资料。
- 原始 `inputs.json`、迁移回执和证据文件必须逐字节保留。其旧绝对路径是历史
  来源定位，不是当前读取路径。

从旧工作树位置迁移时，先生成零写入计划，再用计划中的摘要确认：

```bash
python -m scripts.migration.import_yaozhanbao_case \
  --relocate-from /absolute/legacy/yaozhanbao-transfer-case
python -m scripts.migration.import_yaozhanbao_case \
  --relocate-from /absolute/legacy/yaozhanbao-transfer-case \
  --confirm sha256:PLAN_DIGEST
```

迁移后先执行：

```bash
python -m scripts.migration.import_yaozhanbao_case --verify-only
```

通过后可在隔离候选上运行导入：

```bash
python -m scripts.migration.import_yaozhanbao_case \
  --gateway http://127.0.0.1:28186 \
  --receipt /tmp/yaozhanbao-import-receipt.json
```

命令不再接受旧绝对路径 `--manifest`。XDG 载荷缺失时会返回
`CASE_PAYLOAD_NOT_MATERIALIZED`，不会扫描工作区。案例副本仅用于固定开发验证，
不替代产品 Data 服务中的权威数据或发布交付物。
