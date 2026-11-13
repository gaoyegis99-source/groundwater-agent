# SYS-11：Workflow Draft DAG 展示与缺项交互

## 1. 目标
提高草稿可读性和缺项处理体验，不改变草稿不可执行边界。

## 2. 非目标
- 不重写整个平台，不创建平行服务、状态表或执行运行时。
- 不修改任务卡范围外的模块；发现必须扩大范围时先提交影响分析。
- 不将 NOT_RUN、UNKNOWN 或 SKIP 写成 PASS。

## 3. 源码范围
- `source/web/src/WorkflowDraftCard.tsx`（以本地实际路径为准）
- `Workflow Draft API contracts`
- `Kernel workflow draft validation`

## 4. 前置依赖
SYS-08

## 5. Codex 执行指令
先做只读 DAG 可视化，表达节点、端口、依赖、分支、汇合和多输出。缺项显示输入/参数/证据来源。若做局部修订，必须产生新 revision/草稿并由服务端重新编译。

## 6. 测试命令
```bash
WorkflowDraft 组件 tests；Kernel workflow draft tests；Web typecheck/build。
```
执行前必须核实本地脚本、参数、package scripts 和 toolchain。命令不可用时先读取项目执行手册，不得猜测替代命令。

## 7. 验收标准
图与 JSON 一一对应；缺项可定位；旧草稿不可变；没有执行按钮或前端 Job 旁路。

## 8. 完成回报
- 本地 HEAD 与初始工作区状态；
- 当前问题是否仍存在及复现证据；
- 修改文件和关键实现；
- 测试命令、退出码、passed/failed/skipped；
- 科学验证结果（适用时）；
- NOT_RUN 项和原因；
- 进度表与缺陷台账更新。
