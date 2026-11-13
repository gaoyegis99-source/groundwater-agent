# UI-12 视觉与响应式

基线 `f0b9f6d`。Groundwater Web 以已校验的装配产品 ID 应用设计系统中的蓝色、浅色背景、正文色、边框及圆角。其他产品和共享管理台继续读取已有公共样式。首页保留科研输入与示例；工作台和成果继续按需打开。沿用现有 focus-visible、减少动效、表格替代和数据视图布局。

修改 `style.css`、`product-ui.ts`、`e2e/application-accessibility.spec.ts` 与进度。Playwright 先复现 Groundwater 画布颜色未接入设计变量，修复后 18 项通过。核验 1440×900、1024×768、768×1024、390×844 与额外小视口的重排、键盘、确认可达、输入可见及 axe。桌面与移动截图均取自 Chromium 实际生产构建。浏览器使用合同兼容测试响应。

`../bin/dev check --scope web` 退出 0，371 项 Web 测试、TypeScript、生产构建、Ruff 与 whitespace 检查通过。

## 授权复核

本地 HEAD 为 `f0b9f6d6d5e1df970794d2cd16b9cdcec387b7fc`。开始时工作区有 56 个已修改的跟踪文件和 11 项未跟踪记录。沿用设计系统与参考图的信息层级，实际内容沿用 UI-02 状态投影以及会话、确认、运行和成果接口。

- 矮视口将待发送队列限制在可独立滚动的区域，键盘可访问队列后部，编辑器和当前轮次仍可到达。
- 窄屏导航进入桌面断点时关闭窄屏视图。若焦点位于已消失的关闭按钮，交接到桌面导航控件。从刷新后的工作台直接返回时，焦点回到对话输入框。已有可见入口时仍优先恢复该入口。
- 768px 平板视口的独立工作台明确显示页面标题和内容区，沿用路由的返回入口。四个目标视口同时断言数据内容与成果摘要可见，并检查页面无横向溢出。
- 修改 `source/web/src/App.tsx`、`source/web/src/conversation-polish.css`、`source/web/src/workbench-polish.css`、`source/web/e2e/{application-accessibility,build-task-overview,build-result-formats}.spec.ts`、本报告、`PROGRESS.md` 和 `DECISIONS.md`。
- 本轮沿用既有合同缺口。科学验证状态、完整运行关联等继续依赖已登记的服务端合同。未运行跨服务生命周期门禁、实体移动设备、Safari 或 Firefox。

### 验证

- 生产 Chromium 的三个回归场景初次运行均失败，分别复现长队列遮挡、窄屏导航跨断点失焦、工作台刷新后返回失焦。初次修复复跑还发现成果列表 fixture 缺少服务端必需的会话及项目字段，以及运行中 fixture 错误包含待确认批次，已修正。短视口回归补充验证当前轮次可滚动到可见位置。
- 从 `source/` 执行 `../bin/dev check --scope web`，退出 0，38 个文件、396 项通过，含 TypeScript、生产构建、Ruff 和差异空白检查。
- 首次完整 Chromium 运行有 34 项通过，工作流恢复用例在刷新后等待返回按钮时达到 30 秒超时。工作台定向复跑 2 项通过，包含该恢复用例及四视口数据准备检查。
- 与 Web 门禁并行的第二次完整浏览器运行有 34 项通过，GeoJSON 压测等待图例超时。最终单独运行 `npm --prefix source/web run test:e2e:build` 退出 0，35 项通过，无失败、跳过或 flaky。可访问性与重排文件 20 项通过，包含 axe 对严重或关键问题的检查。浏览器使用合同兼容测试响应。
- `ui-12-responsive-final` 保存 Chromium 原始截图与 `results.json`。首页、工作台数据页和成果页各有 1440×900、1024×768、768×1024、390×844 四张截图，另附桌面和移动端对话、移动参数页及 720×450 长队列截图。
