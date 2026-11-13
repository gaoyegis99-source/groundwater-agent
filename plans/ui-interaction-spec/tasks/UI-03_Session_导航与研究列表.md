# UI-03：Session 导航与研究列表

## 目标
将会话列表升级为研究导航。

## 前置依赖
UI-01, UI-02

## 源码范围
- `App.tsx`
- `use-project-sessions.ts`
- `session list components`
- `style.css`

## Codex 执行步骤
保留搜索、游标分页、重命名、归档；Task 摘要只使用真实字段；普通咨询显示最近活动。

## 测试命令
```bash
Session tests；Web typecheck/build。
```
执行前核实本地 package scripts、pytest 配置、执行手册和环境，不得猜测不存在的命令。

## 验收标准
迟到响应隔离、分页去重、project 隔离、窄屏可用。

## 完成报告
本地 HEAD、初始工作区、真实合同证据、当前缺口、修改文件、测试命令/退出码/统计、未运行项、风险、progress 更新。
