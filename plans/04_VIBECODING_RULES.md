# 04 给 Vibecoding AI 的实施规则

## 1. 项目身份
你在完善已存在的 Groundwater V6，不是在开发 Vibecoding 产品。项目根路径是仓库的 `source/`。

## 2. 先核实
每张卡开始前：
```bash
git status --short
git rev-parse --short HEAD
```
然后读取本卡所列源文件、相邻测试、AGENTS.md 和 owning docs。检查本卡问题在当前 checkout 是否仍存在。

## 3. 修改原则
- 优先修改已有组件和服务，不复制另一套状态管理。
- 不新建平行 API / 平行 executor / 平行 Data 存储。
- 不从 prompt 单独实现本应由 schema/代码强制的规则。
- 不从前端直接创建 Job。
- 不把 WorkflowDraft 当成 executable workflow。
- 不从历史会话复制旧 Data version id。
- 不把模型默认值描述为观测事实或标准阈值。
- 不新增大型依赖，除非任务卡要求且已核实 license / 兼容性。
- 不顺手改部署、安全、鉴权、多租户或兼容迁移。
- 不修改生成文件；按 owning command 生成。
- 不为通过测试删负例或放宽科学容差。

## 4. 测试先行
先写失败测试，再实现。修 UI 缺陷时优先增加组件测试；修 Agent 时增加 ScriptedModel/replay case；修 operator 时增加 fixture + smoke + 数值断言。

## 5. 必须分清四种状态
- PASS：真实检查执行且通过。
- FAIL：真实检查执行且失败。
- UNKNOWN：数据或证据不足，不能判定。
- NOT_RUN：没有运行测试，不能冒充通过。

## 6. 每次只做一张任务卡
跨 Kernel、Data、Executor、Web 的改动要拆分子卡，并明确接口依赖。若当前任务不得不变更公共合同，暂停并提出影响，不擅自扩大任务。

## 7. 每卡完成回报
```text
卡片 ID：
源码基线：
修改文件：
用户可见变化：
测试命令和真实结果：
未运行项：
新发现/阻塞：
进度日志是否更新：
下一张建议任务：
```
