# 三端交接与恢复验收

**通过。** 2026-11-12 在 `0c9afde` 基础上的当前工作区，使用新增的 `--interaction-only` 入口两次启动独立 Compose 栈。第二轮使用项目 `platform-groundwater-e2e-cbf11aba133b`、临时 loopback 网关和新数据目录。TLI 和 Python SDK 从当前源码离线构建 wheel，并安装到独立环境。浏览器使用 Cookie 登录，TLI 和 SDK 使用同一临时身份的 Bearer。 [运行结果](/home/mtd/.local/state/groundwater-v6/validation/interaction-optimization-20261112/compose/platform-groundwater-e2e-cbf11aba133b/gate.json)

## 实际链路

| 场景 | 结果 |
| --- | --- |
| 正常交接 | TLI 创建会话，WebUI 附加精确版本并提交，SDK 核对确认前零执行后批准。三端各下载同一工作流的两份成果，核对发布对象身份、大小及 SHA-256。WebUI 刷新后恢复成果。 |
| 确认回执丢失 | 测试传输层在收到实际 Gateway 的 HTTP 202 后丢弃该回执，SDK 收到 `HTTP_TRANSPORT_FAILED`。读取会话后重投原确认 ID，仍是同一工作流，排队事件和执行记录各一条。SDK 的 `workflow_run` 读取类型化状态。 |
| 超时与跨端恢复 | 停止本次隔离栈的 Native Worker，原工作流在 61.075 秒后以 `JOB_DEADLINE_EXCEEDED` 超时，发布零成果。TLI 持续跟踪 61.658 秒，跨过 25 秒的 SSE 截止时间，事件 ID 严格递增。Worker 恢复后，WebUI 发起恢复，TLI 读取同一冻结确认，SDK 以同一请求 ID 复核后批准。第二次执行用时 4.140 秒，发布两件并校验下载。刷新后保留原超时和唯一一次恢复成功。 |

第二轮 Playwright **3/3 通过**，耗时 109 秒，无跳过或重试。首轮正常和丢回执两项通过，恢复测试在准备阶段发现 rootless Docker 缺少 freezer cgroups。改用停止并启动同一隔离 Worker 后通过。 [完整浏览器结果](/home/mtd/.local/state/groundwater-v6/validation/interaction-optimization-20261112/compose/platform-groundwater-e2e-cbf11aba133b/results.json) · [首轮结果](/home/mtd/.local/state/groundwater-v6/validation/interaction-optimization-20261112/compose/platform-groundwater-e2e-eea005bbcb82/gate.json) · [恢复截图与证据](/home/mtd/.local/state/groundwater-v6/validation/interaction-optimization-20261112/RECOVERY_REPORT.md)

## 代码回归与范围

- SDK 增加 `workflow_run(run_id, project_id=...)`，按公开合同传入 `projectId`。TLI 增加 `events --cursor N --follow`，按最后一个完整事件续接，过滤重复与历史终态，并核对当前轮或恢复批次何时结束。
- 固定开发案例的恢复回复现在保留原回复中已生成的数据使用限制。该缺陷先在 Kernel 回归中复现，修正后通过。实际跨端恢复用例使用合成摘要数据，固定案例限制文字由定向回归覆盖。
- 定向 SDK、TLI、Compose 合同和 Kernel 恢复测试合计 61 项通过，Kernel 任务控制与恢复套件 25 项通过，两套有重叠。新增浏览器测试按仓库的 Bundler 和 JSX 配置通过严格类型检查，文档检查及 diff 检查通过。 [定向结果](/home/mtd/.local/state/groundwater-v6/validation/interaction-optimization-20261112/focused-final.log) · [任务控制结果](/home/mtd/.local/state/groundwater-v6/validation/interaction-optimization-20261112/kernel-controls.log)
- 运行使用固定验收 Provider 和合成数据，实际 Gateway、Kernel、Data 和 Native Worker 完成任务。两个隔离项目的容器、网络、卷均已清理，原研究实例容器 ID 与测试前一致。 [清理记录](/home/mtd/.local/state/groundwater-v6/validation/interaction-optimization-20261112/cleanup.txt)

在已物化的项目环境中，从 `source/` 运行：

```bash
python -m scripts.checks.check_compose_e2e --product groundwater --interaction-only
```

`--webui-report` 可指定源码树外的证据目录。每次运行使用独立子目录，保存 `gate.json`、Playwright 结果及每个场景的校验记录。
