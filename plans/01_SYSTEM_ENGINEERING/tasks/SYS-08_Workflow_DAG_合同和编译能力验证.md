# SYS-08：Workflow DAG 合同和编译能力验证

## 1. 目标
验证 Workflow DAG 的输入输出、参数绑定、拓扑和错误诊断。

## 2. 非目标
- 不重写整个平台，不创建平行服务、状态表或执行运行时。
- 不修改任务卡范围外的模块；发现必须扩大范围时先提交影响分析。
- 不将 NOT_RUN、UNKNOWN 或 SKIP 写成 PASS。

## 3. 源码范围
- `source/packages/workflow-compiler/`
- `source/kernel/src/platform_kernel/agent_runtime/workflow_draft.py`
- `Workflow schema/contracts`
- `compiler tests`

## 4. 前置依赖
SYS-02, SYS-05

## 5. Codex 执行指令
梳理 cardinality、many-input、artifact/value outputs、constant bindings、fan-out/fan-in 和循环检查的真实支持范围。只修复明确合同缺陷；扩展表达能力必须单独设计和测试。

## 6. 测试命令
```bash
workflow-compiler pytest；Kernel workflow draft tests；产品 compile。
```
执行前必须核实本地脚本、参数、package scripts 和 toolchain。命令不可用时先读取项目执行手册，不得猜测替代命令。

## 7. 验收标准
合法 DAG 正确编译；非法端口/循环/不可达节点/参数被拒绝；错误路径稳定；draft 不获得执行授权。

## 8. 完成回报
- 本地 HEAD 与初始工作区状态；
- 当前问题是否仍存在及复现证据；
- 修改文件和关键实现；
- 测试命令、退出码、passed/failed/skipped；
- 科学验证结果（适用时）；
- NOT_RUN 项和原因；
- 进度表与缺陷台账更新。
