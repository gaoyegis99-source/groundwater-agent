# WebUI、终端与 API 联合验收

三个交互入口统一使用 Gateway Public API v12 和同一实例内的会话、确认、任务及成果身份。WebUI 保留浏览器鉴权和公开合同生成的 TypeScript 类型。新终端入口 `groundwater-tli` 通过同一公开 OpenAPI 生成的 Python SDK 调用 Gateway，支持交互模式和单次命令。会话和执行状态仍由 Kernel 管理，数据版本及下载对象由 Data 管理。 [使用说明](/home/mtd/liweigis/workflow/groundwater-v6/source/docs/capabilities/http-api.md)

## 真实联测

本地研究 Gateway 的 1440 × 1000 Chromium 联测通过，单条场景耗时 25.7 秒。

1. 安装的 TLI 查询已发布候选、创建研究会话。WebUI 通过同一会话 ID 打开并附加精确候选。
2. WebUI 发送模拟综合评价请求。终端读取到同一待确认批次，确认前无工作流执行。
3. Python SDK 核对模型、确认 ID、输入版本及模拟标记，提交这次明确批准。SDK 从确认前的事件游标续接，读到排队、阶段变化、执行和成功事件。WebUI 随之显示完成。
4. 终端查询同一工作流的成果并下载三个对象。文件的长度与 SHA-256 全部匹配。结构化评价显示 Ⅱ级并保留模拟标记。浏览器刷新后仍显示同一成功工作流。

测试用的临时 API 令牌已撤销。验收账号通过网页会话及临时令牌访问同一身份。 [验证会话](http://127.0.0.1:28186/#/sessions/80b770f1-3c0a-4c5e-9253-af8ea4a9cbda) · [执行与事件证据](/home/mtd/.local/state/groundwater-v6/validation/interaction-interfaces-20261111/live-3/artifacts/v12-interaction-parity-Web-1857f--confirmed-Gateway-workflow/interaction-verification.json) · [Playwright 报告](/home/mtd/.local/state/groundwater-v6/validation/interaction-interfaces-20261111/live-3/results.json)

首次联测将已有会话的“实时更新已连接”误判为未连接。第二次使用的 CSV 将列登记为字符串，模型按数值类型前置检查拒绝统计请求。最终联测复用已通过验证的模拟候选，完整验证三个入口的接力。

## 模块与回归

| 检查 | 实测 |
| --- | --- |
| 客户端与边界 | 80 项通过，含终端 17 项。SDK 缺省游标过去被序列化为空字符串，真实 Gateway 返回 422。现在省略未指定参数，事件使用 `afterEventId`。SSE 错误响应现在可按公开 Problem 合同解码 |
| WebUI | 33 个测试文件、288 项通过，类型检查、生产构建、Ruff 和 diff 检查通过 |
| 公共合同 | 186 项通过，运行时 OpenAPI、TypeScript 和 Python 生成快照一致。Pytest 在清理历史临时工作流目录时发出警告，命令退出码为 0 |
| 安装交付 | 两个 wheel 离线安装到独立 Python 环境，`groundwater-tli --help` 可运行，Kernel 与 Data 服务包均未安装 |

各回归套件有重叠。当前本地 Gateway 的认证 readiness 返回 200，Kernel、Data、Executor、Knowledge、Agent Provider、Intent Router Provider 和 Embedding 均为 UP。本报告覆盖当前变更和实际交互接力。完整项目门禁以既有全量报告为准。

[客户端与边界日志](/home/mtd/.local/state/groundwater-v6/validation/interaction-interfaces-20261111/clients-and-boundaries.log) · [WebUI 检查](/home/mtd/.local/state/groundwater-v6/validation/interaction-interfaces-20261111/web-check.log) · [公共合同检查](/home/mtd/.local/state/groundwater-v6/validation/interaction-interfaces-20261111/contracts-check.log) · [离线安装和 wheel 校验和](/home/mtd/.local/state/groundwater-v6/validation/interaction-interfaces-20261111/wheel-delivery/sha256.txt)
