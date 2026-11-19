"""Validate assessment deliverables and the unchanged tracked source baseline."""
import ast,collections,hashlib,json,pathlib,re,subprocess
R=pathlib.Path(__file__).resolve().parents[3];O=R/'plans/repository-assessment';E=O/'evidence';errors=[]
required=['README.md','00_PRODUCT_POSITIONING.md','01_REPOSITORY_INVENTORY.md','02_CORE_BUSINESS_CAPABILITY.md','03_DATA_ASSESSMENT.md','04_KNOWLEDGE_ASSESSMENT.md','05_AGENT_ASSESSMENT.md','06_DOMAIN_WORKFLOW_ASSESSMENT.md','07_MODULE_SIMPLIFICATION.md','08_REDUNDANCY_DECISION_MATRIX.md','09_TARGET_ARCHITECTURE.md','10_REFACTORING_ROADMAP.md','11_IMPLEMENTATION_ROADMAP.md','evidence/SOURCE_INDEX.md','evidence/TEST_EVIDENCE.md','progress/PROGRESS.md','progress/DECISIONS.md','progress/RISKS.md']
for p in required:
 if not (O/p).is_file() or not (O/p).stat().st_size:errors.append('missing deliverable: '+p)
files=json.loads((E/'files.json').read_text());changed=[]
for item in files:
 p=R/item['path']
 if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:changed.append(item['path'])
if changed:errors.append('baseline files changed: '+repr(changed))
revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=R).decode().strip()
inv=json.loads((E/'inventory.json').read_text())
if revision!=inv['revision']:errors.append('HEAD changed')
status=subprocess.check_output(['git','status','--short','--untracked-files=all'],cwd=R).decode().splitlines()
outside=[s for s in status if not s[3:].startswith('plans/repository-assessment/')]
if outside:errors.append('changes outside assessment: '+repr(outside))
ops=json.loads((E/'operators.json').read_text());models=json.loads((E/'models.json').read_text());assets=json.loads((E/'knowledge_assets.json').read_text());tasks=json.loads((E/'implementation_tasks.json').read_text());wf=json.loads((E/'workflows.json').read_text())
expected={'operators':113,'operator_ids':111,'models':18,'workflows':18,'knowledge_assets':9,'documents':121,'units':321,'tasks':24}
actual={'operators':len(ops),'operator_ids':len({o['id'].split('@')[0] for o in ops}),'models':len(models),'workflows':len(wf),'knowledge_assets':len(assets),'documents':sum(len(a['documents']) for a in assets),'units':sum(len(d['chunks']) for a in assets for d in a['documents']),'tasks':len(tasks)}
if actual!=expected:errors.append('inventory counts mismatch')
fields={'id','priority','title','problem','scope','root_cause','impact','action','dependencies','risk','tests','acceptance','contract_change'}
for t in tasks:
 if set(t)!=fields or not all(t.values()):errors.append('task fields: '+t['id'])
 if t['priority'] not in {'P0','P1','P2','P3'}:errors.append('task priority: '+t['id'])
ids={t['id'] for t in tasks}
if len(ids)!=len(tasks):errors.append('duplicate task id')
for t in tasks:
 for dep in re.findall(r'\bA\d\d\b',t['dependencies']):
  if dep not in ids:errors.append('unknown dependency: '+dep)
markdown=list(O.rglob('*.md'));local_links=0;external_links=0
for p in markdown:
 s=p.read_text()
 for raw in re.findall(r'\[[^\]\n]*\]\(([^\n)]+)\)',s):
  target=raw.strip('<>')
  if target.startswith(('https://','http://','mailto:')):external_links+=1;continue
  if target.startswith('#'):continue
  target=target.split('#',1)[0];m=re.search(r':(\d+)$',target);line=int(m.group(1)) if m else None
  if m:target=target[:m.start()]
  q=pathlib.Path(target)
  if not q.is_absolute():errors.append(f'nonabsolute local link {p.name}: {target}');q=p.parent/q
  if not q.exists():errors.append(f'broken link {p.name}: {q}')
  elif line and q.is_file() and line>len(q.read_text().splitlines()):errors.append(f'line beyond end: {q}:{line}')
  local_links+=1
 if s.count('```')%2:errors.append('unclosed fence: '+str(p))
source_ids=set(re.findall(r'^## (E\d\d) ',(E/'SOURCE_INDEX.md').read_text(),re.M))
for p in markdown:
 for ref in set(re.findall(r'\bE\d\d\b',p.read_text())):
  if ref not in source_ids:errors.append(f'unknown source reference {p.name}: {ref}')
for p in E.glob('*.py'):
 try:ast.parse(p.read_text())
 except SyntaxError as exc:errors.append(f'script syntax {p.name}: {exc}')
# Check the saved evidence, without rerunning product checks.
logs={name:(E/name).read_text() for name in ['collect.log','contracts.log','retry.log','operators.log']}
for name,text in [('collect.log','2287 tests collected'),('contracts.log','2 failed, 493 passed'),('retry.log','2 passed'),('operators.log','45 passed')]:
 if text not in logs[name]:errors.append('test count mismatch: '+name)
probe=json.loads((E/'preparation-boundary-probe.json').read_text())
if probe['preparation_status']!='READY' or probe['accepted_actions']!=1:errors.append('probe mismatch')
for name,count,pattern in [('OPERATOR_MATRIX.md',113,r'^\| `'),('OPERATOR_TEST_INDEX.md',113,r'^\| `'),('MODEL_WORKFLOW_MATRIX.md',18,r'^\| `'),('MODEL_EVIDENCE_DETAIL.md',18,r'^\| \[groundwater\.')]:
 found=len(re.findall(pattern,(E/name).read_text(),re.M))
 if found!=count:errors.append(f'{name} row count {found} != {count}')
check=subprocess.run(['git','diff','--check'],cwd=R,capture_output=True,text=True)
if check.returncode:errors.append('git diff --check: '+check.stdout+check.stderr)
result={'status':'PASS' if not errors else 'FAIL','source_revision':revision,'required_deliverables':len(required),'markdown_files':len(markdown),'baseline_files_hashed':len(files),'baseline_files_changed':changed,'git_changes_outside_assessment':outside,'local_links_checked':local_links,'external_links_listed_not_all_fetched':external_links,'source_evidence_groups':len(source_ids),'inventory':actual,'operator_categories':dict(collections.Counter(o['category'] for o in ops)),'task_priorities':dict(collections.Counter(t['priority'] for t in tasks)),'selected_unique_cases_eventually_passed':540,'production_test_rerun_in_report_validation':False,'errors':errors}
(E/'REPORT_VALIDATION.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False,indent=2))
raise SystemExit(bool(errors))
