# 地下水领域分析系统边界章程（V12）

V12 是可信单机或小型实验室中的项目级分析系统。Groundwater 产品拥有领域包、产品装配、
固定案例与从输入到可追溯结果的链路；Kernel、Data、Knowledge 和 Executor 只拥有各自的
通用合同、状态与运行机制。

## 状态与依赖所有权

- 可写状态按 XDG 分为配置、权威实例数据、审计状态、可重建缓存和瞬时 runtime。
- 所有容器操作只能经项目 `../bin/docker` 访问项目专属 rootless daemon。
- 实例持久数据只在 `$XDG_DATA_HOME/groundwater-v6/instances/<id>` 的显式 bind mount 中。
- 日常装配从 digest 固定的基础镜像引用、本地依赖缓存和当前源码构建；允许拉取缺失镜像。
- 跨产品交换必须使用版本化导入/导出合同，不共享数据库、checkpoint、凭据或运行目录。

历史设计和旧验收结果只通过 Git 查询。当前源码只维护 V12 运行合同和 v11→v12 的只读迁移入口。
