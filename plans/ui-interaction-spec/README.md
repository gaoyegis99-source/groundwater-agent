# Groundwater Agent 前端交互 UI 详细设计与 Codex 实施任务集

定位：地下水垂直领域科研 Agent，不是传统 WebGIS，也不是通用 GIS Agent。

本包先定义 Session、Task、Turn、WorkflowRun 的交互合同，再定义页面、组件、状态和实施任务。所有状态必须映射 Kernel、Executor、Data 的权威事实；不得在前端创造第二套业务状态机。

推荐放置：`/home/mtd/liweigis/workflow/groundwater-v6/plans/ui-interaction-spec/`

执行顺序：UI-01 合同审计 → UI-02 状态投影 → Session/Turn → Task/数据 → Draft/Confirmation → WorkflowRun/恢复 → 成果 → 视觉 → E2E。

包含：6 份对象合同、6 份页面规范、11 份组件规范、13 张原子任务卡、状态矩阵和 18 个 E2E 场景。

## 视觉参考图片
`design-references/` 内含一张总览图、八张页面级裁切参考图，以及统一设计系统规范。图片均来自同一套视觉稿，作为 Codex 实施时的视觉基准。图片中的文字和示例数据不作为真实产品事实。
