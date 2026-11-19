#!/usr/bin/env python3
"""Read tracked repository files; write assessment evidence only. No service startup."""
import ast, collections, hashlib, json, pathlib, re, subprocess
ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT = pathlib.Path(__file__).resolve().parent
files = [p for p in subprocess.check_output(['git','ls-files'],cwd=ROOT,text=True).splitlines() if not p.startswith('plans/repository-assessment/') and (ROOT/p).is_file()]
records=[]; symbols=[]; tests=[]
def module(p):
 a=p.split('/')
 if a[0]=='source':
  if a[1] in ('services','packages','domains','products'): return '/'.join(a[:3])
  return '/'.join(a[:2])
 if a[0]=='third-party-services': return '/'.join(a[:2])
 return a[0]
for name in files:
 p=ROOT/name; raw=p.read_bytes(); ext=p.suffix; lines=0
 try: content=raw.decode(); lines=len(content.splitlines())
 except UnicodeDecodeError: content=''
 record={'path':name,'module':module(name),'bytes':len(raw),'lines':lines,'suffix':ext,'sha256':hashlib.sha256(raw).hexdigest()};records.append(record)
 if ext=='.py':
  try: tree=ast.parse(content)
  except (SyntaxError,ValueError): continue
  for n in ast.walk(tree):
   if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
    symbols.append((name,n.lineno,n.name,type(n).__name__))
    if n.name.startswith('test_') and isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
     tests.append((name,n.lineno,n.name))
mods=[]
for m in sorted({r['module'] for r in records}):
 rs=[r for r in records if r['module']==m]; mods.append({'module':m,'tracked_files':len(rs),'text_lines':sum(r['lines'] for r in rs),'python_files':sum(r['suffix']=='.py' for r in rs),'python_test_functions':sum(module(t[0])==m for t in tests)})
domain=ROOT/'source/domains/groundwater/6.2.0';manifest=json.loads((domain/'domain-manifest.json').read_text()); cat=ROOT/'source/packages/operator-pack/catalog'
index=json.loads((cat/'index.json').read_text()); refs=collections.defaultdict(list); flows=[]
for relative in manifest['workflowDefinitions']:
 p=domain/relative; d=json.loads(p.read_text()); nodes=d.get('nodes',[])
 if isinstance(nodes,dict): nodes=list(nodes.values())
 for n in nodes:
  op=n.get('operator',n.get('operatorRef',{'id':n.get('operatorId',''),'version':n.get('operatorVersion','')}))
  if isinstance(op,dict): refs[op.get('id',op.get('operatorId',''))+'@'+op.get('version','')].append(d.get('workflowId',p.stem))
 flows.append({'path':str(p.relative_to(ROOT)),'id':d.get('workflowId'),'version':d.get('version'),'nodes':len(nodes),'keys':list(d),'operators':sorted(set([n.get('operatorId','')+'@'+n.get('operatorVersion','') for n in nodes]))})
ops=[]
for ent in index['operators']:
 p=cat/'operators'/ent['id']/ent['version']/'manifest.json';d=json.loads(p.read_text()); ident=ent['id']+'@'+ent['version']; folder=p.parent
 ops.append({'id':ident,'runtime':ent['runtime'],'path':str(p.relative_to(ROOT)),'referenced_by':sorted(set(refs.get(ident,[]))),'inputs':[x.get('name') for x in d.get('inputs',[])],'outputs':[x.get('name') for x in d.get('outputs',[])],'smoke_files':[str(x.relative_to(ROOT)) for x in folder.rglob('*') if x.is_file() and 'smoke' in str(x)],'description':d.get('description','')})
knowledge=[]; contents=collections.defaultdict(list)
for rel in manifest['knowledgeDocuments']:
 p=domain/rel; d=json.loads(p.read_text()); docs=d.get('documents',[])
 for doc in docs:
  chunks=doc.get('chunks',[])
  for i,ch in enumerate(chunks):
   s=ch.get('content','') if isinstance(ch,dict) else str(ch)
   contents[hashlib.sha256(s.encode()).hexdigest()].append([rel,doc.get('documentId',doc.get('id',doc.get('title',''))),i])
 knowledge.append({'path':str(p.relative_to(ROOT)),'keys':list(d),'sources':d.get('sources',[]),'documents':docs})
summary={'revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'tracked_files':len(files),'python_test_functions':len(tests),'python_test_files':len({t[0] for t in tests}),'js_test_files':len([p for p in files if re.search(r'\.(test|spec)\.[cm]?[jt]sx?$',p)]),'domain':{'version':manifest['version'],'models':len(manifest['executableModels']),'workflows':len(flows),'knowledge_assets':len(knowledge),'execution_cases':len(manifest['executionCases']),'observation_tools':len(manifest['observationTools'])},'catalog':{'entries':len(ops),'unique_ids':len({o['id'].rsplit('@',1)[0] for o in ops}),'runtime':dict(collections.Counter(o['runtime'] for o in ops)),'workflow_referenced_entries':len([o for o in ops if o['referenced_by']]),'manifest_files':len(list((cat/'operators').glob('*/*/manifest.json')))},'modules':mods,'knowledge_exact_duplicates':[v for v in contents.values() if len(v)>1]}
for name,value in [('inventory.json',summary),('files.json',records),('workflows.json',flows),('operators.json',ops),('knowledge_assets.json',knowledge)]: (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
for name,header,rows in [('SYMBOLS.tsv',['path','line','symbol','kind'],symbols),('TEST_FUNCTIONS.tsv',['path','line','function'],tests)]:
 (OUT/name).write_text('\t'.join(header)+'\n'+'\n'.join('\t'.join(map(str,r)) for r in rows)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k!='modules'},ensure_ascii=False,indent=2));print(json.dumps(mods,ensure_ascii=False,indent=2))
