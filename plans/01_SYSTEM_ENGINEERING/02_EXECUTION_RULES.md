# 系统工程包执行规则

## 本地工程入口
项目已有统一入口时优先使用：
```bash
../bin/gw bootstrap --status
../bin/dev compile --product groundwater
../bin/dev check --scope <scope>
../bin/dev capability-inventory --verify
```
先核实本地 `--help` 和执行手册。Web 相关脚本从 `source/web/package.json` 读取，不得猜测。

## 质量要求
- 每张卡有目标行为测试和相邻回归测试。
- 不能用错误 Python/Node 环境造成的导入失败判定产品缺陷。
- 测试结果、缓存、build 产物不得污染源码树。
- 公共合同变化先做影响分析。
- Data version/semantic/object/checksum 不得被静默替换。
- Agent 文本不得建立执行事实；确认仍是执行授权边界。
- SSE 必须保留 event ID、snapshot reconciliation 和断线恢复语义。
- 节点状态来自持久化执行事实，不从模型回复猜测。

## 进度
每张卡结束更新 `progress/PROGRESS.md` 和 `progress/DEFECT_LEDGER.md`。不覆盖旧审计证据，更新 owning document。
