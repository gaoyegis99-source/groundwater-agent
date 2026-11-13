# Groundwater V6 — Package 01：系统级工程完善

本包只解决 Groundwater 现有系统的工程问题：架构边界、Agent 运行机制、Data/Knowledge 基础设施、Workflow/Executor、Web 工作台、测试与性能。它不负责扩展地下水科学方法和模型。

## 适用对象
本地 Codex，在当前 Groundwater 仓库中执行。

## 安装位置
建议放置为：
`plans/01_SYSTEM_ENGINEERING/`

## 阅读顺序
1. `01_CODEX_MASTER_PROMPT.md`
2. `00_MASTER_PLAN.md`
3. `02_EXECUTION_RULES.md`
4. `03_LOCAL_BASELINE.md`
5. `stages/` 对应工作包
6. `tasks/` 原子任务
7. `04_ACCEPTANCE_MATRIX.md`

## 执行原则
- 不访问 GitHub；不 clone/fetch/pull。
- 以当前本地 checkout 为唯一事实来源。
- 每次只执行一张任务卡。
- 先核实缺陷仍存在，再先测试后修复。
- 不改变产品定位，不扩张领域算法。
- 不把系统工程验收等同于地下水科学验收。

## 包内任务
共 14 张原子任务卡，分为六个工作包 E1–E6。
