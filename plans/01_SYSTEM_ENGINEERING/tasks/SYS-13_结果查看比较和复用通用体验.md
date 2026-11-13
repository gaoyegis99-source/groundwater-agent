# SYS-13：结果查看比较和复用通用体验

## 1. 目标
完善通用成果查看、下载、比较和精确版本复用链路。

## 2. 非目标
- 不重写整个平台，不创建平行服务、状态表或执行运行时。
- 不修改任务卡范围外的模块；发现必须扩大范围时先提交影响分析。
- 不将 NOT_RUN、UNKNOWN 或 SKIP 写成 PASS。

## 3. 源码范围
- `Results UI components`
- `Data result publication/download APIs`
- `Data version compare/lineage`
- `Kernel/Executor result projection`

## 4. 前置依赖
SYS-06, SYS-07, SYS-09

## 5. Codex 执行指令
检查输出文件状态、预览、下载、再次附加和跨会话继续。比较维度以数据版本、语义、对象、checksum 和 lineage 为准。复用生成新提案，不沿用旧确认。

## 6. 测试命令
```bash
Web result tests；Data/Kernel tests；适用 E2E。
```
执行前必须核实本地脚本、参数、package scripts 和 toolchain。命令不可用时先读取项目执行手册，不得猜测替代命令。

## 7. 验收标准
未发布成果无下载入口；结果身份准确；复用不自动发送/执行；历史运行和成果归属可追溯。

## 8. 完成回报
- 本地 HEAD 与初始工作区状态；
- 当前问题是否仍存在及复现证据；
- 修改文件和关键实现；
- 测试命令、退出码、passed/failed/skipped；
- 科学验证结果（适用时）；
- NOT_RUN 项和原因；
- 进度表与缺陷台账更新。
