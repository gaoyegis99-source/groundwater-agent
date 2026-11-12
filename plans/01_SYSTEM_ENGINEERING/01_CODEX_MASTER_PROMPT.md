# Codex 主提示词：Package 01 系统级工程完善

你正在本地完善现有 Groundwater V6 地下水领域工作流系统。本包只处理系统级工程问题，不负责地下水专业方法和算法扩展。

## 约束
- 当前本地仓库是唯一事实来源。
- 不访问 GitHub，不 clone/fetch/pull。
- 不重写系统，不建立平行 Runtime/API/Data store。
- 不覆盖用户已有修改。
- 不顺手做领域算法扩展。
- 不把 Workflow Draft 变成执行授权。
- 不绕过确认状态机。
- 不以测试数量代替行为正确性。

## 启动
```bash
pwd
git rev-parse --show-toplevel
git rev-parse --short HEAD
git status --short
find .. -name AGENTS.md -print
```
读取本包总计划、执行规则、本地基线、当前任务卡，以及相关 AGENTS.md、架构契约和执行手册。

## 单卡执行
1. 只执行用户指定的一张任务卡。
2. 阅读任务卡源码和相邻测试。
3. 核实问题仍存在；若已修复，执行验证后标记 `ALREADY_FIXED_VERIFIED`。
4. 先写失败测试并证明测试能捕获目标问题。
5. 最小修改。
6. 执行卡片测试、相关 typecheck/build 和范围检查。
7. `git diff --check`，检查无关修改。
8. 更新 progress 与 defect ledger。
9. 输出真实测试结果和未运行项。

## 状态
PASS / FAIL / SKIP / NOT_RUN / UNKNOWN / ALREADY_FIXED_VERIFIED 必须严格区分。

## 报告格式
任务卡、本地 HEAD、初始工作区、问题是否复现、修改文件、测试命令与退出码、通过/失败/跳过数量、未运行项、风险、进度更新、下一张卡。
