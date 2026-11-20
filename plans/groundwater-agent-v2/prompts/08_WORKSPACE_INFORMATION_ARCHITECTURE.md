# 阶段 7：AI 原生工作台信息架构

## 用户目标
用户在连贯界面中切换 WorkItem、对话、资料、任务和成果，不必在多个抽屉、Session 列表和标签间寻找状态。

## 目标结构（增量演进，不要求一次性重写）
- 全局导航：工作项、数据、知识/方法、能力目录。
- WorkItem 导航：最近工作、筛选/归档（仅后端支持时）。
- WorkItem 主视图：概览、对话、资料、任务/进度、成果、活动。
- Agent Composer：当前 WorkItem 上下文和已附加资料。
- 次级面板：参数、数据详情、诊断和来源。

## 重点审查
App.tsx 状态是否过载；useProjectSessions 与 WorkItem selection 边界；useWorkspaceController 与路由/焦点恢复；TurnTimeline/TaskCard 职责；ProjectTaskHistory 是否重复；ProductDrawers 是否过度依赖 Drawer；Result Viewer 与 Data Workbench 联动。

## 交互规则
- 当前 WorkItem 始终可识别。
- 新 Session 明确归属。
- 无 WorkItem 的普通咨询仍可用。
- 不把旧 Session 临时草稿显示到新工作项。
- 写操作具备 pending/success/error/conflict。
- 刷新从 API 恢复。
- 状态来自权威 API。
- 专业信息渐进披露。

## 无障碍
键盘导航、heading 层级、标签、Dialog 焦点管理、aria-live、非颜色状态表达、loading/empty/error/offline/stale/permission-denied 设计、窄屏适配。

## 验收
提供信息架构图、组件职责表、关键页面实现、交互/无障碍测试和迁移说明，避免无关大规模重写。
