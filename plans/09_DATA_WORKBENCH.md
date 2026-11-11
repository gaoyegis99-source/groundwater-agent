# 09 Data：数据目录、画像、预览与质量体验

## 当前 Data 能力
源码目录 `source/services/data/src/platform_data_service/` 已包含：
- catalog discovery/service
- ingestion
- semantics
- preview
- query
- publication
- quality
- MLOps
- openlineage/outbox
- readers/storage/sandbox adapters

前端独立 UI 在 `source/services/data/web/src/`，主 Web 的资源查看器在 `source/web/src/viewers/`。

## DATA-01 字段画像体验（P1）
先读：
- `services/data/src/platform_data_service/table_inspection.py`
- `api/routers/catalog.py`
- `api/routers/preview.py`
- `web/src/CatalogPanel.tsx`
- `web/src/PreviewPanel.tsx`
- `web/src/SemanticEditor.tsx`

目标：让用户看懂字段名、field id、类型、空值、样例、数值范围、单位、时间覆盖、空间范围。
步骤：
1. 核实现有 descriptor 是否已经包含相同统计，避免重复计算。
2. 区分全量统计和抽样统计。
3. 对日期字段显示 parse success rate 与时区。
4. 对空间字段显示 geometry type、CRS、bbox。
5. 对数字字符串提示可能需要类型转换。
6. Preflight 使用 descriptor 结果，避免重新实现画像逻辑。

## DATA-02 Discovery 解释性（P1）
先读 `catalog/discovery.py` 和对应 API/tests。
展示每个候选命中的原因：名称、字段、标签、格式、时间范围等。多候选冲突时不要自动选错版本。
支持分页时，Agent 必须完成分页才能声称覆盖完整目录。

## DATA-03 DatasetVersion 对比（P2）
基于不可变版本：
- 行列数变化
- 字段增删
- 类型变化
- 空值率和数值范围变化
- 若存在可靠主键，记录新增/删除/变更记录数
无主键时只报告 aggregate diff，不伪装逐行比对。

## DATA-04 Data preview 性能（P2）
先审计现有 CSV/Parquet query、XLSX/GPKG preview 和 COG Range read 的限制。按需加载，不在主会话首屏读取完整对象。
明确截断、行数上限、过期 preview 和下载路径。

## DATA-05 Quality 控制台体验（P2）
Data 已有 quality suite/run/alerts。完善：
- 当前数据版本对应的质量结果
- 规则版本和执行时间
- 失败字段/行数/原因
- 质量趋势与历史 run 对比
- 告警与修复动作入口
不得把 descriptor 的旧统计冒充最新 quality run。
