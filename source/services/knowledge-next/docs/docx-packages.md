# 从项目报告构建可追溯知识包

离线编译器把 DOCX 正文和表格转换为现有 `knowledge-package.v1`，供知识页面上传。
它解决中文报告直接按长度切分时丢失表头、合并单元格、单位与出处的问题。
它不改变 Kernel 路由、模型契约或已安装领域包，也不执行网络请求。

## 生成

在已配置的项目 Python 环境中运行：

```bash
python -m platform_knowledge_next.docx_package /absolute/report.docx \
  --output /absolute/new-output-directory \
  --project local-data --prefix project-report \
  --title '项目报告' --publisher '报告提供者' --version 1.0.0 \
  --accessed-at 2026-11-20 --review-after 2026-11-20
```

输出目录必须不存在，避免覆盖已有知识包。编译仅需标准库；没有安装模块时，
可从 `source/` 设置 `PYTHONPATH=services/knowledge-next/src` 再运行。
日期、项目和版本须由操作者按实际来源填写。复核日期不是来源认证。

输出包括 `source-blocks.json` 和一个或多个 `source-package-NN.json`。
前者保存文件 SHA-256、正文定位、原始表格、OMML 数学表达式与图像位置；
后者可以使用现有 KnowledgePackageV1 验证并导入。首次创建不填 expectedVersion；
后续更新先 preflight，按服务返回的当前版本填写 CAS 条件。
内容或项目改变会生成新的幂等键。每包至多128篇，每篇最多64单元，
原文分块目标2800字符，避免上下文装配时过长。超长表头明确失败，须人工整理。

## 证据与人工整理

`bNNNN` 是 `word/document.xml` 中正文 `w:body` 直属子元素的一基索引，
不是页面编号。段落另保留片段号，表格另保留行号。横纵合并单元格的标签展开到每行。
每个原文单元必须带来源范围说明；表格行还依赖原表前两行及可识别的表注。
前两行只是原样上下文，不猜测多级表头含义。复杂表头、跨段脚注、嵌套表格与浮动文本框
仍须人工检查；它不是完整的 Word 排版解析器。

公式位置保留“公式见原件”标记，不能把扁平数学字符误作可运行公式。
图像/嵌入对象仅记录位置，不自动 OCR；页眉、页脚、批注、脚注和尾注被列为排除部件。
修订文字使用正文最终视图（排除删除，包含插入），不能据此宣称修订已经审定。
必须保留原件以查证图表和公式，不能仅凭 JSON 宣称完成全文科学复核。

专业解释另建 `definition/method/case/rule` 文档：

- 区分原文陈述、编者解释、模型情景、经验代理与待核查项。
- 解释单元通过 requiredUnits 带入适用范围和原文引文。
- 每个数值保留对象、时间、层位、单位、分母、化学口径及计算方式。
- 可执行规则另走现有核验流程，原文中出现标准名称不能使它成为 VERIFIED。
- 项目特定知识没有真实 Data 身份时不伪造 bindings；GENERAL 仅表示未绑定，不代表普适。

## 意图辅助与接入

意图示例可作为 rule 知识上传，辅助理解术语解释、报告事实、机制、比较、结果审阅、
方案设计、明确计算、工作流草稿、状态、否定执行和上下文追问。
示例沿用 Kernel 的 responseKind/semanticIntent 词汇，不能替代路由器或执行授权。
运行时仍按领域说明读取精确证据和 Data 身份，完成输入准备与确认。
不要把报告中的部署示意图当作当前代码已实现的能力清单。

管理员在实际项目的知识页面上传包。服务验证和向量化成功后才算入库。
多个包是分别原子生效，不存在跨包事务；部署期间先检查全部包成功再交付问答。
导入后的资源可见性按目标项目和实际权限配置核查。
离线校验通过不代表 PostgreSQL、Embedding、召回或真实意图模型已经验证。
项目报告内容可以保留在私有交付目录，不必提交到公共源码仓库。

## 验证

适用统一检查为 `../bin/dev check --scope knowledge`。独立离线编译测试：

```bash
PYTHONPATH=services/knowledge-next/src:packages/protocol-contracts/src \
  python -m unittest discover -s services/knowledge-next/tests -p test_docx_package.py -v
```

测试覆盖合并单元格、表注依赖、跨文档大表、长文不丢失、跨包界限、
幂等身份、公式/修订边界、图像位置与错误输入。测试仅使用合成材料。
