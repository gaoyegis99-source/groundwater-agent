# UI-08：Confirmation 修订与审批

## 目标
完善冻结批次、参数差异、过期失效和回执。

## 前置依赖
UI-01, UI-02, UI-06, UI-07

## 源码范围
- `ChatCards.tsx`
- `ConfirmationEditor.tsx`
- `use-message-actions.ts`
- `Kernel confirmation API`

## Codex 执行步骤
显示 revision、冻结输入、参数来源和差异；编辑后生成新修订；旧确认禁用；不确定回执核对原 requestId。

## 测试命令
```bash
Confirmation tests；API idempotency/revision tests；E2E approve/reject/edit。
```
执行前核实本地 package scripts、pytest 配置、执行手册和环境，不得猜测不存在的命令。

## 验收标准
重复点击不重复执行；旧确认不可操作；未确认不创建 Run。

## 完成报告
本地 HEAD、初始工作区、真实合同证据、当前缺口、修改文件、测试命令/退出码/统计、未运行项、风险、progress 更新。
