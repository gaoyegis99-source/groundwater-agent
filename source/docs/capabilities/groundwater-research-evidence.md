# V6 受控地下水研究证据链

## 产品边界

V6 的后续研发方向是**受控授权语料上的证据型地下水研究**，不是开放网络检索、
通用知识平台或自动业务决策。原始资料、授权文件和任何含敏感内容的证据包始终位于
仓库外；仓库只保留可审查的身份、校验和、规则和脱敏评测摘要。

V7 不读取此资料、运行态或 Docker 资源。若将来要交换结果，必须定义新的、版本化的
导入/导出合同，不能添加在线调用。

## 已实现的首期合同

`products/groundwater/research/research_corpus.py` 提供
`groundwater-controlled-corpus.v1`：每份授权资料必须声明来源身份、授权记录、版本、
SHA-256、时间范围、坐标系、单位、质量规则和精确 Data 绑定。它只接受相对于外部
私有根目录的路径；验证器拒绝仓库内根、符号链接、路径逃逸、未授权资料和校验和漂移。

固定领域基线仍是 **99 份文档 / 291 个 Evidence Unit**，但不是“已完成真实导入”的
声明。只有取得资料所有者授权、冻结 manifest、完成全新导入并运行 import baseline
对账后，才可把这一基线用于研发门禁。

`products/groundwater/research/research_package.py` 提供四种回答状态：

| 状态 | 允许的内容 |
| --- | --- |
| `EVIDENCED_FACT` | 必须有精确 `versionId/unitId/checksum`、读取回执和活动生命周期的引用。 |
| `HYPOTHESIS` | 明确是假设，不能标为高置信事实。 |
| `INSUFFICIENT_EVIDENCE` | 没有引用、置信度为未确定；不能伪装成确定性结论。 |
| `ANALYSIS_RECOMMENDATION` | 给出下一步分析建议，仍受现有确认流程约束。 |

研究包固定 Domain Package/产品版本、模型 ID/版本/checksum、Data 选择、知识引用和参数，始终标为
`AWAITING_CONFIRMATION` 与 `executionAllowed=false`。生成包不会执行模型、修改 Data
或绕过 Kernel 的确认、租约和审计链。

## 运行顺序

资料所有者在仓库外准备来源树后，先执行只读验证：

```bash
python products/groundwater/scripts/verify_controlled_corpus.py \
  --manifest /absolute/private/groundwater-corpus.manifest.json \
  --source-root /absolute/private/groundwater-authorised-sources \
  --output /absolute/private/reports/corpus-verification.json
```

验证通过不等于已导入。接下来要在全新的隔离 Knowledge/Data 实例中导入；从导入回执定位
每个 `VersionResponse` 后，仅提取 `knowledgeId/contentVersion/unitCount` 形成不含正文的紧凑
审阅清单并运行：

```bash
python products/groundwater/scripts/verify_import_baseline.py \
  --manifest /absolute/private/groundwater-corpus.manifest.json \
  --versions /absolute/private/reports/knowledge-version-views.json \
  --output /absolute/private/reports/import-baseline.json
```

该验证器也能读取临时的 `VersionResponse` 形状，但正式审阅清单不应复制正文；无论哪种输入，
它都使用 `knowledgeId/contentVersion/units`，而不是把随机生成的 import/version ID 误作冻结内容
身份。随后才运行 297 题评测。评测现在会逐题记录
请求状态、排名、返回的 `versionId/unitId/checksum` 身份和明确失败分类；命中不是事实，
仍须经精确 evidence read 才能被引用。

准备研究提案时只生成 review-only 包：

```bash
python products/groundwater/scripts/build_research_package.py \
  --proposal /absolute/private/proposal.json \
  --answer /absolute/private/evidence-answer.json \
  --output /absolute/private/research-package.json
```

只有用户在既有 V6 确认界面明确确认后，适配层才可将固定输入交给六类地下水模型；该
运行与结果必须继续保存模型版本、知识引用、Data 版本、参数和执行输入。

## 六个月交付节奏

| 月份 | V6 可验收输出 | 非目标 |
| --- | --- | --- |
| 1–2 | 授权清单、99/291 对账规则、领域专家问题集和质量准则 | 导入未经授权的资料、开放网络抓取 |
| 3–4 | 隔离全新导入、297 题证据门禁、证据型问答原型 | 用检索命中直接生成事实 |
| 5–6 | 代表场景的问答→确认提案→可复现研究包闭环 | 自动执行或自动业务决策 |

新的地下水模型、语义规则或知识内容必须发布新的 Domain Package/产品版本和评测夹具，
不得原地修改既有身份。
