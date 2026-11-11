# 20 给 Codex / Claude Code / Cursor 的新会话启动提示词

你正在当前仓库完善已经存在的 Groundwater V6。你不是开发 Vibecoding 系统。

源码实际位于仓库 `source/` 目录。计划基线 main commit 是 `3354fc2d959219f399ed667b993dcbdf3d0db89e`，但你必须检查当前 checkout 的实际 HEAD。

请先阅读：
- `00_MASTER_PLAN.md`
- `01_SOURCE_AUDIT.md`
- `02_CALL_GRAPH.md`
- `03_GAP_MATRIX.md`
- `04_VIBECODING_RULES.md`
- `90_PROGRESS.md`
- 对应任务卡

严格要求：
1. 先检查 git status / HEAD。
2. 先核实源码，不要假定历史缺陷仍存在。
3. 一次只执行一张任务卡。
4. 优先复用现有组件、API、算子、workflow。
5. 不做部署、安全、鉴权、多租户、兼容迁移或 Registry 工作。
6. 不为通过测试放宽数值容差。
7. 未运行测试标 NOT_RUN。
8. 每次完成更新 `90_PROGRESS.md`。

第一次只做只读复核：
- 确认 7 个 workflow / 7 个 model / 106 operator 是否与当前 HEAD 一致。
- 复核 GAP_MATRIX 中 P0 缺陷是否仍存在。
- 复核相关 tests 是否已有修复覆盖。
- 输出 `SOURCE_RECHECK.md`，包括差异、测试命令和建议首卡。
未经用户确认，不开始大规模修改。
