# Codex 总提示词：Groundwater Agent 前端交互 UI

你负责在本地 Groundwater Agent 仓库实施 `plans/ui-interaction-spec/`。

产品是地下水垂直领域科研 Agent，不是传统 WebGIS。主体验围绕研究会话、Agent 协作、持续任务、执行控制和科研成果。地图/图表是按需视图。

## 最高优先级
先完成 Session、Task、Turn、WorkflowRun 四对象交互合同，再改页面。不得先做大规模视觉重构。

## 必读
`00_MASTER_SPEC.md`、`contracts/`、`acceptance/`、`tasks/README.md`、对应任务卡、项目 AGENTS.md、`source/docs/architecture/continuous-collaboration-design.md`、`source/docs/capabilities/webui.md`、相关 API schema/types/persistence/tests。

## 启动
```bash
pwd
git rev-parse --show-toplevel
git rev-parse --short HEAD
git status --short
find . -name AGENTS.md -print
```
不得访问 GitHub、clone/fetch/pull。保留用户改动，不得 reset/clean/stash。

## 单卡协议
1. 一次只执行一张任务卡。
2. 审计真实 API/schema/persistence，不能臆造字段。
3. 核实缺口仍存在；已修复则验证后标记 ALREADY_FIXED_VERIFIED。
4. 先写失败测试，再最小修改。
5. 后端合同缺口先出 ADR/影响分析，未经批准不改公共合同。
6. UI 绑定真实服务端快照；禁止 setTimeout、固定百分比或本地假状态模拟进度。
7. 保留确认、拒绝、停止、恢复、requestId 幂等和 snapshot reconciliation。
8. 更新 progress，执行相关测试/typecheck/build 和 git diff --check。

## 禁止
不做传统 WebGIS 首页；不将完整 DAG 放在对话首屏；不合并 Task/Turn/WorkflowRun；不把 Draft 当执行授权；不从 LLM 文本推断执行状态；不展示隐藏推理；不大规模重写 App.tsx；不新建平行状态管理系统。

## 顺序
UI-01 合同审计；UI-02 状态投影；UI-03 Session 导航；UI-04 Turn 时间线；UI-05 Task 工作台；UI-06 数据准备；UI-07 方案详情；UI-08 Confirmation；UI-09 WorkflowRun；UI-10 失败恢复；UI-11 成果评审；UI-12 视觉响应式；UI-13 E2E。

## 最终交付
四对象交互合同矩阵、状态映射、页面组件实现、关键路径 E2E、桌面/窄屏验收、contract gaps 和实施总结。不得只产出静态 mock，必须接入真实 API。
