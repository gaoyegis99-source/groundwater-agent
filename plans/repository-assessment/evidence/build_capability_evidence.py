"""Static navigation evidence; test references do not assert execution/coverage."""
import ast,json,pathlib,re,subprocess
R=pathlib.Path(__file__).resolve().parents[3];O=pathlib.Path(__file__).resolve().parent
D=R/'source/domains/groundwater/6.2.0'; manifest=json.loads((D/'domain-manifest.json').read_text())
ops=json.loads((O/'operators.json').read_text()); assets=json.loads((O/'knowledge_assets.json').read_text())
tracked=subprocess.check_output(['git','ls-files','-z'],cwd=R).decode().split('\0')
test_paths=[R/p for p in tracked if p and p.endswith('.py') and pathlib.Path(p).name.startswith('test_')]
texts={p:p.read_text() for p in test_paths}
operator_sources=list((R/'source/packages/operator-pack/src/moperators/operators').glob('*.py'))
source_nodes={p:ast.parse(p.read_text()) for p in operator_sources}
def link(p,label,line=None):
 return f'[{label}]({p}'+(f':{line}' if line else '')+')'
def literal_refs(identifier,paths):
 found=[]
 pattern=re.compile(r'([\"\'])'+re.escape(identifier)+r'\1')
 for p in paths:
  match=pattern.search(texts[p])
  if match:found.append((p,texts[p][:match.start()].count('\n')+1))
 return found
rows=['# 算子实现与测试导航补表','','每行给出实际代码入口或分派、全部smoke文件及显式提到算子ID的测试位置。字面引用只是导航，**不是逐算子测试覆盖率**；测试可能只验证合同，也可能通过间接调用覆盖而未出现字面ID。无直接引用时标UNKNOWN，不推断无测试。所有现场/独立科学验证仍UNKNOWN。','', '| 算子版本 | 源码定位 | smoke | 显式测试引用（最多3处） |','|---|---|---|---|']
records=[]
for o in ops:
 identifier=o['id'].split('@')[0];entry=o['implementation'];impl=[]
 if entry in ('_native','_qgis'):
  for p,t in source_nodes.items():
   if p.name in ('qgis.py',) and entry!='_qgis':continue
   if entry=='_qgis' and p.name!='qgis.py':continue
   constants=[n for n in ast.walk(t) if isinstance(n,ast.Constant) and n.value==identifier]
   if constants:
    line=min(n.lineno for n in constants);owners=[n for n in ast.walk(t) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.lineno<=line<=n.end_lineno]
    name=min(owners,key=lambda n:n.end_lineno-n.lineno).name if owners else 'module'
    impl.append((p,name,line))
 elif entry=='_remote_http':
  p=R/'source/services/executor/src/platform_executor_service/remote_http.py';impl=[(p,'remote_http worker',1)]
 elif entry=='_reference_grid':impl=[(R/'source/packages/operator-pack/src/moperators/operators/raster_reference_grid.py','run',1)]
 elif '.' in entry:
  mod,fun=entry.split('.',1);p=R/'source/packages/operator-pack/src/moperators/operators'/f'{mod}.py'
  candidates=[n for n in ast.parse(p.read_text()).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==fun]
  impl=[(p,fun,candidates[0].lineno if candidates else 1)]
 if not impl:impl=[(R/'source/packages/operator-pack/src/moperators/static_implementations.py',entry,1)]
 references=literal_refs(identifier,test_paths)
 references.sort(key=lambda x:(0 if '/operator-pack/tests/' in str(x[0]) else 1,str(x[0])))
 smoke=[R/p for p in o['smoke_files']]
 rows.append('| `'+o['id']+'` | '+'<br>'.join(link(p,f'{p.name}::{fun}',line) for p,fun,line in impl)+' | '+'<br>'.join(link(p,p.name) for p in smoke)+' | '+('<br>'.join(link(p,p.name,line) for p,line in references[:3]) or 'UNKNOWN：本轮未定位直接字面引用')+' |')
 records.append({'operator':o['id'],'implementations':[{'path':str(p.relative_to(R)),'symbol':fun,'line':line} for p,fun,line in impl],'smoke':[str(p.relative_to(R)) for p in smoke],'literal_test_references':[{'path':str(p.relative_to(R)),'line':line} for p,line in references],'scientific_validation':'UNKNOWN'})
(O/'OPERATOR_TEST_INDEX.md').write_text('\n'.join(rows)+'\n');(O/'capability_test_index.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
rows=['# 模型、工作流、知识卡与测试连接','','全部模型已声明/有实现；下表连接当前Workflow、各算子节点、modelRef知识卡和显式模型ID测试引用。测试引用不是通过状态；本轮通过范围见TEST_EVIDENCE。所有真实完整运行及现场科研验证为UNKNOWN。','', '| 模型 | Workflow/算子依赖 | modelRef知识 | 测试导航（最多3处） |','|---|---|---|---|']
for rel in manifest['executableModels']:
 m=json.loads((D/rel).read_text());identifier=m['modelId'];wpath=next(D/w for w in manifest['workflowDefinitions'] if json.loads((D/w).read_text())['workflowId']==m['workflowRef']['workflowId']);w=json.loads(wpath.read_text())
 cards=[(R/a['path'],d['knowledgeId']) for a in assets for d in a['documents'] if d.get('modelRef',{}).get('modelId')==identifier]
 references=literal_refs(identifier,test_paths)
 ids=list(dict.fromkeys(n['operatorId']+'@'+n['operatorVersion'] for n in w['nodes']))
 rows.append('| '+link(D/rel,identifier)+' | '+link(wpath,'Workflow')+'<br>'+'<br>'.join('`'+i+'`' for i in ids)+' | '+('<br>'.join(link(p,k) for p,k in cards) or 'UNKNOWN：未找到modelRef')+' | '+('<br>'.join(link(p,p.name,line) for p,line in references[:3]) or 'UNKNOWN：未定位直接字面引用')+' |')
(O/'MODEL_EVIDENCE_DETAIL.md').write_text('\n'.join(rows)+'\n')
print('operator evidence:',len(records),'models:',len(manifest['executableModels']))
