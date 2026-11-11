# 10 Knowledge：证据检索、引用和原文回溯

## 当前能力
Knowledge Next 服务路径 `source/services/knowledge-next/`；已提供 retrieval、lookup、read evidence、lifecycle、import preflight、retrieval receipt 等合同和 UI。
主 Web 的来源展示在 `source/web/src/MessageContent.tsx`、`Drawers.tsx`、`ProductDrawers.tsx` 等。

## KNOW-01 引用回原文（P1）
1. 点击 source 时使用精确 `versionId/unitId/checksum`。
2. 抽屉显示文档标题、知识版本、unit id、chunk 原文和上下文。
3. 状态非 OK 时不显示为有效依据。
4. 数据绑定知识显示精确 project/dataset/version/semantic match。
5. 不把裸 ID 或 checksum 当作用户可理解内容。

## KNOW-02 Retrieval receipt 可解释（P1）
读取 Knowledge retrieval receipt，展示查询、命中数量、检索模式、证据版本、是否完整 read。
不得将 retrieval candidate 当作已读取可引用证据。

## KNOW-03 证据生命周期提示（P1）
STALE、WITHDRAWN、EXPIRED、NOT_FOUND、MISMATCH 等状态应有明确提示和重新检索动作。旧 checksum 不得继续作为有效引用。

## KNOW-04 中文检索基准（P2）
建立领域 query set，覆盖：
- 地下水术语同义词
- 标准号
- 方法名称
- 数据字段语义
- 业务自然语言问题
报告 Recall@k / MRR，按 query 类别拆分。先测当前检索，再决定是否调整分词/embedding/rerank。
