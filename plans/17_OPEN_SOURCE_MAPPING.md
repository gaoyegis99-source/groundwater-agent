# 17 开源项目借鉴：映射到当前 V6 源码

| 开源项目 | 借鉴重点 | 对应现有代码 | 不应照搬 |
|---|---|---|---|
| GISclaw | GIS 数据画像、plan/execute/replan、空间工具选择 | Data inspection、Kernel intent routing、WorkflowDraft | 任意 Python 执行与替换 Kernel |
| GeoAgent | GIS 工具元数据、地图状态/图层交互 | Operator Catalog、SpatialViewers | 直接替换现有 OpenLayers 与执行治理 |
| Data Formulator | 对话式数据探索、分析分支、图表迭代 | DatabaseWorkbench、ChartView、Task workspace | 重建 Data service |
| Magentic-UI | 用户可编辑计划、审批前检查 | ConfirmationEditor、WorkflowDraftCard | 替换 Confirmation 状态机 |
| HydroMT | 数据目录、模型配置、模型构建配方 | Data catalog、groundwater domain workflows | 新建第二套 workflow engine |
| RAGFlow | chunk 管理、原文定位、引用 UI | Knowledge Next、Knowledge drawer | 替换 Knowledge Next 全部服务 |
| FloPy / MODFLOW examples | 模型构建、求解、输出验证 | Native operator pack / groundwater model | 将例子代码直接作为生产算法 |
| GSTools / PyKrige | Kriging、variogram、variance、CV | 新增 Kriging operator | 不核实 license/ABI 就复制代码 |

## 一手项目入口
- GISclaw: https://github.com/geumjin99/GISclaw
- GeoAgent: https://github.com/opengeos/GeoAgent
- Data Formulator: https://github.com/microsoft/data-formulator
- Magentic-UI: https://github.com/microsoft/magentic-ui
- HydroMT: https://github.com/Deltares/hydromt
- RAGFlow: https://github.com/infiniflow/ragflow
- FloPy: https://github.com/modflowpy/flopy
- MODFLOW 6 examples: https://github.com/MODFLOW-ORG/modflow6-examples
- GSTools: https://github.com/GeoStat-Framework/GSTools

## 采用原则
先借鉴交互设计、数据结构和测试方法。只有现有模块确实无法满足需求时才考虑引入依赖。实施时核对目标 tag 的 LICENSE、依赖树和更新活跃度。不能把“项目功能相似”当作可以直接复制代码的许可。
