# v11 领域包

当前 Groundwater 产品精确选择 `groundwater@5.0.0`，产品清单固定 ID、版本和 checksum。
运行时服务只读取 Assembler 生成配置，不读取领域包目录。当前源码只保留这个领域包；
退休版本通过 Git 标签查阅。

```bash
../bin/dev compile --product groundwater
platform-domain check domains/groundwater/6.0.0 --root .
```

包内包含 `domain-manifest.json`、指令、Workflow、Executable Model、Knowledge 文档和
可选固定执行案例。包中没有可执行扩展、服务实现、运行时注册或继承。

Groundwater 暴露六个模型：描述统计、监测趋势、流场、监测覆盖范围、迁移学习分析和
简单 PNG 制图。Workflow v6 与 Executable Model v4 使用精确 Data 版本输入和命名输出；
制图通过静态算子发布 PNG，不声明独立地图服务。

七份 Knowledge 文件提供 99 篇文档、291 个单元；Knowledge Next 负责导入和检索。
固定案例由 `execution-case.v1` 声明别名、模型引用、同批输入角色及参数档位。
计划与恢复展示规则见[协议与持久化](../reference/contracts.md)，
Kernel 不硬编码地下水专属名称或训练参数文案。

公共 Data 能力提供目录概览与结构化发现；领域包的 Agent 指令负责把业务表达映射为受控的 query、field、unit 和 format 条件，并要求在确定候选后读取精确版本。领域包不拥有 Data 目录副本，也不能把检索命中直接解释为业务适用。执行准备通过 Kernel 的通用 `prepare_task_inputs` 与 `TaskPreparationV1` 完成，因此新增领域仍复用同一准备、确认和恢复合同。

更改领域声明或知识内容必须生成新的包身份，同步产品 pin 与相关评测 fixture；不得
原地改写已发布身份。当前协议、数量和归档条件以[科研验收](../operations/acceptance-gates.md)
为准，旧验证记录不代表新版本已经通过验收。
