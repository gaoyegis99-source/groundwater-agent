# SYS-10：ConfirmationEditor 数据集分页与错误状态

## 1. 目标
让确认参数编辑器可以搜索/分页选择数据版本，并可靠显示加载和失败状态。

## 2. 非目标
- 不重写整个平台，不创建平行服务、状态表或执行运行时。
- 不修改任务卡范围外的模块；发现必须扩大范围时先提交影响分析。
- 不将 NOT_RUN、UNKNOWN 或 SKIP 写成 PASS。

## 3. 源码范围
- `source/web/src/ConfirmationEditor.tsx`
- `source/web/src/api.ts`
- `source/web/src/api-contract.ts`
- `Data datasets API routes/contracts`

## 4. 前置依赖
SYS-06

## 5. Codex 执行指令
核实 `/data/datasets` 真实分页/搜索合同。构造超过 50 个数据集的测试。实现服务端分页或搜索；保留 loading/empty/error/retry/abort/stale 状态。精确版本 identity 不得静默替换。

## 6. 测试命令
```bash
在 `source/` 下执行：`npm --prefix web test -- ConfirmationEditor`; `npm --prefix web run typecheck`; `npm --prefix web run build`。
```
执行前必须核实本地脚本、参数、package scripts 和 toolchain。命令不可用时先读取项目执行手册，不得猜测替代命令。

## 7. 验收标准
第 51 个及后续数据集可检索；选择后 dataset/version/semantic/object/checksum 正确；切换项目旧响应不覆盖新状态。

## 8. 完成回报
- 本地 HEAD 与初始工作区状态；
- 当前问题是否仍存在及复现证据；
- 修改文件和关键实现；
- 测试命令、退出码、passed/failed/skipped；
- 科学验证结果（适用时）；
- NOT_RUN 项和原因；
- 进度表与缺陷台账更新。
