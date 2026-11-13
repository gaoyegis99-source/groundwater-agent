# UI-04：Turn 时间线

## 目标
统一 Turn 分组、消息顺序、Activity 和业务卡布局。

## 前置依赖
UI-01, UI-02

## 源码范围
- `App.tsx`
- `ActivityPanel.tsx`
- `ChatCards.tsx`
- `MessageContent.tsx`
- `use-session-stream.ts`

## Codex 执行步骤
抽离纯展示层，不改消息存储；历史卡只读；临时流式内容标未核验。

## 测试命令
```bash
Turn/Activity tests；SSE replay tests。
```
执行前核实本地 package scripts、pytest 配置、执行手册和环境，不得猜测不存在的命令。

## 验收标准
刷新恢复、事件隔离、正式回复替换预览、不展示隐藏推理。

## 完成报告
本地 HEAD、初始工作区、真实合同证据、当前缺口、修改文件、测试命令/退出码/统计、未运行项、风险、progress 更新。
