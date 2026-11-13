# SYS-06：Data 目录分页版本和查询链路

## 1. 目标
完善 Data 通用目录、版本、查询和预览的稳定性。

## 2. 非目标
- 不重写整个平台，不创建平行服务、状态表或执行运行时。
- 不修改任务卡范围外的模块；发现必须扩大范围时先提交影响分析。
- 不将 NOT_RUN、UNKNOWN 或 SKIP 写成 PASS。

## 3. 源码范围
- `source/services/data/`
- `source/packages/groundwater-api-client/`（如存在）
- `source/web/src/api.ts`
- `Data tests/OpenAPI`

## 4. 前置依赖
SYS-01, SYS-02

## 5. Codex 执行指令
核实目录分页合同和查询排序分页语义；检查版本 identity、semantic identity、preview 上限、取消、错误和 stale request。修复通用 Data 问题，不添加地下水专属质量规则。

## 6. 测试命令
```bash
`../bin/dev check --scope data`（先核实）；相关 Data API tests；适用 Data UI tests。
```
执行前必须核实本地脚本、参数、package scripts 和 toolchain。命令不可用时先读取项目执行手册，不得猜测替代命令。

## 7. 验收标准
分页无遗漏/重复；版本身份不被替换；查询排序确定；预览截断和错误可见；取消请求不会覆盖新状态。

## 8. 完成回报
- 本地 HEAD 与初始工作区状态；
- 当前问题是否仍存在及复现证据；
- 修改文件和关键实现；
- 测试命令、退出码、passed/failed/skipped；
- 科学验证结果（适用时）；
- NOT_RUN 项和原因；
- 进度表与缺陷台账更新。
