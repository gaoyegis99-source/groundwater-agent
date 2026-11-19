"""Generate exhaustive declaration tables from current tracked source."""
import ast,collections,hashlib,json,pathlib,re
R=pathlib.Path(__file__).resolve().parents[3];O=pathlib.Path(__file__).resolve().parent
D=R/'source/domains/groundwater/6.2.0';M=json.loads((D/'domain-manifest.json').read_text());P=json.loads((R/'source/products/groundwater/product-manifest.json').read_text())
ops=json.loads((O/'operators.json').read_text()); tests=[x.split('\t') for x in (O/'TEST_FUNCTIONS.tsv').read_text().splitlines()[1:]]
def link(p,label=None):
 return '['+(label or str(p))+']('+str(R/p)+')'
def write(name,s): (O/name).write_text(s+'\n')
wf={json.loads((D/p).read_text())['workflowId']:json.loads((D/p).read_text()) for p in M['workflowDefinitions']}
core=set()
for k,v in wf.items():
 if k in ['groundwater.monitoring-trend','groundwater.flow-field','groundwater.summary','groundwater.render-map','groundwater.monitoring-coverage']:
  core.update(n['operatorId'] for n in v['nodes'])
implfile=R/'source/packages/operator-pack/src/moperators/static_implementations.py';tree=ast.parse(implfile.read_text());impl={}
for n in ast.walk(tree):
 if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and n.target.id=='IMPLEMENTATIONS':
  impl={f'{ast.literal_eval(k)[0]}@{ast.literal_eval(k)[1]}':ast.unparse(v) for k,v in zip(n.value.keys,n.value.values)}
rows=['# 算子逐项清单与处置建议','', '113 行按 `id@version` 计，111 个 ID。每行已核对 Manifest、输入输出、静态实现登记及当前 Workflow 引用。smoke=夹具存在，不表示已运行。远程条目由 Executor remote_http 调用，静态占位函数故意拒绝本地运行。未逐一执行全部算子，未列专门测试的算子只可认定有目录/实现/夹具；科学验证 UNKNOWN。','', '| 算子版本 | runtime/实现入口 | 分类/建议 | 当前 Workflow | 输入→输出 | 合同与 smoke |','|---|---|---|---|---|---|']
categories=collections.Counter()
for o in ops:
 key=o['id'];id,version=key.split('@');refs=o['referenced_by'];c='共用基础算子';decision='KEEP';why='按需基础处理'
 if key in ['raster.reproject@1.0.0','groundwater.porous.pest_calibrate@1.0.0']:c='弃用候选';decision='DEPRECATE';why='存在新版本，先查历史身份引用'
 elif id in ['groundwater.porous.inversion_sample','groundwater.porous.inversion_train','groundwater.porous.agri_invert'] or id.startswith('ml.'):c='实验性算子';decision='EXPERIMENTAL';why='限定训练/研究输入'
 elif id.startswith('hydrology.'):c='高级专业算子';decision='SHRINK';why='地表水文辅助，退出默认目录'
 elif id.startswith('groundwater.porous.') or id in ['points.interpolate.kriging','groundwater.drastic.evaluate','table.classify','groundwater.source_sustainability.evaluate','groundwater.source_sustainability.prepare','groundwater.drawdown.calculate']:c='高级专业算子';decision='KEEP';why='独立专题包，先验证适用范围'
 elif id in core:c='核心算子';decision='KEEP';why='默认科研闭环'
 elif id.startswith('document.'):c='共用基础算子';decision='SHRINK';why='知识摄取外部预处理，不扩展RAG服务'
 elif id in ['timeseries.autocorrelation','timeseries.cross_correlation','timeseries.seasonal_anomaly']:c='高级专业算子';decision='ADVANCE';why='有实现，应建立适用门禁后接入业务'
 categories[c]+=1
 o.update(category=c,decision=decision,rationale=why,implementation=impl.get(key,'UNKNOWN'))
 path=pathlib.Path(o['path']);smoke=o['smoke_files'];
 rows.append('| `'+key+'` | '+o['runtime']+' / `'+impl.get(key,'UNKNOWN')+'` | '+c+' / '+decision+'；'+why+' | '+('<br>'.join(x.removeprefix('groundwater.') for x in refs) or '无当前引用；不等于无用')+' | '+','.join(o['inputs'])+' → '+','.join(o['outputs'])+' | '+link(path,'manifest')+'；'+str(len(smoke))+' smoke 文件 |')
rows+=['','分类统计：'+json.dumps(categories,ensure_ascii=False)+'。没有仅凭名称确认的“重复算子”；不同版本不是重复算法证据。“未知用途”类别为0，因为合同描述均有用途；实际用户需求和历史使用仍可为 UNKNOWN。','', '测试定位：`test_static_pack.py`核对目录/实现一致性；8 个专业算子测试文件本轮 45 项通过（operators.log）；其它 smoke/QGIS/远程完整运行未执行。各算子对具体科学问题的适用性不能由静态目录通过推断。']
write('OPERATOR_MATRIX.md','\n'.join(rows));write('operators.json',json.dumps(ops,ensure_ascii=False,indent=2))
rows=['# 模型、Workflow 与执行案例逐项清单','','数据身份均经 Model inputBindings→Workflow inputs→Operator ports；字节所有权归 Data，执行状态归 Executor。每项科学/现场验证默认为 UNKNOWN，代码/合同和定向测试见主报告。','','| 模型/工具 | Workflow/节点/runtime | 输入参数 | 输出/字段与语义门禁数 | 依据 |','|---|---|---|---|---|']
models=[]
for rel in M['executableModels']:
 model=json.loads((D/rel).read_text());flow=wf[model['workflowRef']['workflowId']]; runt=collections.Counter()
 for n in flow['nodes']:
  key=n['operatorId']+'@'+n['operatorVersion'];runt[next(o['runtime'] for o in ops if o['id']==key)]+=1
 models.append({'path':str((D/rel).relative_to(R)),'id':model['modelId'],'version':model['version'],'tool':model['tool']['name'],'workflow':flow['workflowId'],'node_count':len(flow['nodes']),'input_count':len(model['inputBindings']),'output_count':len(model['finalOutputs']),'field_requirements':len(model.get('fieldRequirements',[])),'semantic_requirements':model.get('semanticRequirements',[]),'runtimes':dict(runt)})
 rows.append('| `'+model['modelId']+'@'+model['version']+'`<br>`'+model['tool']['name']+'` | '+flow['workflowId']+'@'+flow['version']+' / '+str(len(flow['nodes']))+' / '+str(dict(runt))+' | '+', '.join(model['inputBindings'].values())+' | '+str(len(model['finalOutputs']))+' outputs / '+str(len(model.get('fieldRequirements',[])))+' field / '+str(len(model.get('semanticRequirements',[])))+' semantic | '+link((D/rel).relative_to(R),'model')+' |')
rows+=['','## Execution Case','']
for rel in M['executionCases']:
 d=json.loads((D/rel).read_text());rows+=['- '+link((D/rel).relative_to(R),d['caseId'])+'：绑定 '+str(d['modelRef'])+'；'+str(len(d['inputRoles']))+' 项角色；标签 `'+d['datasetTag']+'`。载荷在仓库外，本轮未验证其物化、授权和真实运行。']
write('MODEL_WORKFLOW_MATRIX.md','\n'.join(rows));write('models.json',json.dumps(models,ensure_ascii=False,indent=2))
assets=json.loads((O/'knowledge_assets.json').read_text());rows=['# 当前 Manifest 全部知识资产与文档','','全文均作结构读取、源引用/精确重复检查；逐篇标题和摘要核阅，专业内容定向核阅。不是121篇知识逐句专家认证。未检查的外部来源真实性记 UNKNOWN。','','| 资产 | 文档 | 分块 | modelRef | 来源缺引用/内容缺locator |','|---|---|---:|---|---|'];chars=0;chunks=0;docids=[];sources={};missingrefs=0;missingloc=0;demounits=0;related=[]
for a in assets:
 smap={s['sourceId']:s for s in a['sources']};sources.update(smap)
 for doc in a['documents']:
  us=doc['chunks'];chunks+=len(us);chars+=sum(len(u['content']) for u in us);docids.append(doc['knowledgeId']);miss=sum(not u.get('sourceRefs') or any(ref not in smap for ref in u.get('sourceRefs',[])) for u in us); loc=sum(not u.get('locator') for u in us);missingrefs+=miss;missingloc+=loc
  demo=sum(any(smap[r]['kind'] in ['DEMO_ASSUMPTION','PROJECT_TEMPLATE'] for r in u.get('sourceRefs',[]) if r in smap) for u in us);demounits+=demo
  rows.append('| '+link(pathlib.Path(a['path']),pathlib.Path(a['path']).name)+' | `'+doc['knowledgeId']+'` '+doc['title']+' | '+str(len(us))+' | '+str(doc.get('modelRef',{}).get('modelId','—'))+' | '+str(miss)+' / '+str(loc)+('；演示/模板来源块 '+str(demo) if demo else '')+' |')
rows+=['','## 完整结构统计','',json.dumps({'documents':len(docids),'unique_knowledge_ids':len(set(docids)),'units':chunks,'content_chars':chars,'unique_source_ids':len(sources),'units_with_demo_or_template_source':demounits,'missing_or_invalid_source_refs':missingrefs,'missing_locators':missingloc,'exact_duplicate_units':len(json.loads((O/'inventory.json').read_text())['knowledge_exact_duplicates'])},ensure_ascii=False),'','所有当前资产均无 Data bindings。Model Cards 使用 modelRef，不代表模型执行和来源科学验证；kind 缺省在转换器中为 knowledge。','', '## 来源表','', '| sourceId | kind | 标题/定位 | 外部核验状态 |','|---|---|---|---|']
for k,s in sorted(sources.items()):
 status='UNKNOWN（本轮未核全文）';url=s.get('url');title=s.get('title','')
 if k=='usgs-circular-1139':status='已核 USGS 标题：本地 Ground Water Atlas reference 不符，应改引用元数据'
 elif k=='usgs-modflow6-tm6a55':status='已核官方题录，未逐句核知识摘要'
 elif not url:status='本地声明，不等于独立专家验证'
 rows.append('| '+k+' | '+s['kind']+' | '+('['+title+']('+url+')' if url else title)+' | '+status+' |')
write('KNOWLEDGE_INVENTORY.md','\n'.join(rows))
# API enumeration and file-level code counts
api=[]
for rel in ['source/products/groundwater/contracts/public-api-v12.json','source/services/data/contracts/data-http-v3.json','source/services/knowledge-next/contracts/knowledge-http-v3.json']:
 p=R/rel
 if p.exists():
  d=json.loads(p.read_text());api.append({'path':rel,'paths':len(d.get('paths',{})),'operations':sum(sum(k in ['get','post','put','delete','patch','options','head'] for k in v) for v in d.get('paths',{}).values())})
write('api_counts.json',json.dumps(api,indent=2))
print('operator categories',dict(categories));print('knowledge',len(docids),chunks,len(sources),demounits,missingloc,missingrefs);print('api',api)
