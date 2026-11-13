# 系统工程包验收矩阵

- [x] 本地产品版本、工具链、HEAD 和工作区状态有证据。见 [本地基线](03_LOCAL_BASELINE.md)。
- [x] Kernel/Data/Knowledge/Executor 的状态所有权清楚，无平行真相。见 SYS-02。
- [x] Agent 路由、确认、拒绝、恢复和断线重连符合既有状态机。见 SYS-04、SYS-05 和隔离跨端验收。
- [x] Data 目录、分页、版本、查询、预览接口契约一致。见 SYS-06 和 Data/Knowledge 隔离验收。
- [x] Knowledge 检索结果必须完整读取后才可作为引用证据。见 SYS-07。
- [x] Workflow DAG 编译、端口绑定、循环检测和输出验证正确。见 SYS-08 和三个 Workflow 金样。
- [x] Executor 节点状态、重试、恢复和成果发布可追溯。见 SYS-09 和隔离跨端恢复。
- [x] ConfirmationEditor 能检索和选择数据集、重试读取并保留选择时的语义身份。隔离三端验证语义变更时保存返回 409，重新选择后返回待批准批次。见 SYS10-03。
- [x] Workflow Draft 展示准确且不产生执行授权。见 SYS-11。
- [x] 地图/图表通用组件正确处理大数据、排序、轴类型、CRS 和 NoData。见 SYS-12 和 Chromium 性能样本。
- [x] 结果查看、下载、比较和复用不混淆数据版本。见 SYS-13 和隔离跨端下载验收。
- [x] 相关单元、子系统、E2E、typecheck/build 和性能检查真实执行。全量复跑退出 0，四项独立领域试验按实际结果记录为 SKIP。
- [x] 所有 NOT_RUN/UNKNOWN 项明确保留。

各任务的执行结果、性能数据和未解决项见 [进度与阶段验收报告](progress/PROGRESS.md)；复现及修复证据见 [缺陷台账](progress/DEFECT_LEDGER.md)。
