# SYS-12：地图和图表查看器通用缺陷修复

## 1. 目标
修复通用地图/栅格/图表的性能、排序、轴类型和生命周期问题。

## 2. 非目标
- 不重写整个平台，不创建平行服务、状态表或执行运行时。
- 不修改任务卡范围外的模块；发现必须扩大范围时先提交影响分析。
- 不将 NOT_RUN、UNKNOWN 或 SKIP 写成 PASS。

## 3. 源码范围
- `source/web/src/viewers/SpatialViewers.tsx`
- `source/web/src/viewers/ChartView.tsx`
- `source/web/src/viewers/raster-probe.ts`
- `对应 tests`

## 4. 前置依赖
SYS-01, SYS-06

## 5. Codex 执行指令
先复核当前代码。覆盖 GeoJSON 大数组 min/max、图表 query sort、时间/数值/category 轴、Scatter 轴、空值、截断、Raster transform/NoData/Scale/Offset。只做通用 viewer 能力，不加入地下水专业图例/解释。

## 6. 测试命令
```bash
在 `source/` 下执行 Web viewer tests；`npm --prefix web run typecheck`; `npm --prefix web run build`。
```
执行前必须核实本地脚本、参数、package scripts 和 toolchain。命令不可用时先读取项目执行手册，不得猜测替代命令。

## 7. 验收标准
大数据不崩溃；轴语义正确；排序确定；栅格探针物理值正确；地图生命周期不因样式切换重建。

## 8. 完成回报
- 本地 HEAD 与初始工作区状态；
- 当前问题是否仍存在及复现证据；
- 修改文件和关键实现；
- 测试命令、退出码、passed/failed/skipped；
- 科学验证结果（适用时）；
- NOT_RUN 项和原因；
- 进度表与缺陷台账更新。
