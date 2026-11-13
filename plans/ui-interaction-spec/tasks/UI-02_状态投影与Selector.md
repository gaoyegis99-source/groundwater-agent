# UI-02：状态投影与Selector

## 目标
建立纯函数 selector，把权威状态映射为 UI label/tone/action。

## 前置依赖
UI-01

## 源码范围
- `web/src/chat-state.ts`
- `web/src/types.ts`
- `Session/Task/Executor schemas`

## Codex 执行步骤
区分 Task lifecycle/state、stop request/cancelled、Run success/publication/scientific validation；unknown enum safe fallback。

## 测试命令
```bash
Web selector tests；`npm --prefix source/web run typecheck`（先核实脚本）。
```
执行前核实本地 package scripts、pytest 配置、执行手册和环境，不得猜测不存在的命令。

## 验收标准
无第二套业务状态；unknown 不默认成功；所有映射有来源。

## 完成报告
本地 HEAD、初始工作区、真实合同证据、当前缺口、修改文件、测试命令/退出码/统计、未运行项、风险、progress 更新。
