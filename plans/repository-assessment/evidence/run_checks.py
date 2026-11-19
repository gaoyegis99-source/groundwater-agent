"""Run isolated assessment checks, never starting services or using active databases."""
import datetime, json, os, pathlib, subprocess, tempfile, sys
ROOT=pathlib.Path(__file__).resolve().parents[3]; OUT=pathlib.Path(__file__).resolve().parent
PY='/home/mtd/.cache/groundwater-v6/toolchains/v6-d2879b157dedc56a/bin/python'
mode=sys.argv[1]; temp=pathlib.Path(tempfile.mkdtemp(prefix='groundwater-assessment-'))
env=dict(os.environ); env.update(PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PLATFORM_GENERATED_ROOT=str(temp/'assembly'))
for k in list(env):
 if k.startswith('TEST_') and ('DATABASE' in k or 'URL' in k):env.pop(k)
base=[PY,'-m','pytest','-p','no:cacheprovider','--basetemp',str(temp/'pytest')]
if mode=='collect': args=['--collect-only','-q'];cwd=ROOT/'source'
elif mode=='contracts':
 args=['-q','tests/contracts','tests/assembler','services/executor/tests/test_remote_http.py','services/executor/tests/test_schema_contract.py','kernel/tests/test_internal_modules.py','kernel/tests/test_settings.py','services/knowledge-next/tests/test_retrieval_budget.py'];cwd=ROOT/'source'
elif mode=='retry':
 args=['-q','tests/contracts/test_agent_evaluator.py::test_compose_compiles_temporary_sources_and_cleans_up','tests/contracts/test_groundwater_resource_wiring.py::test_sdk_configuration_error_terminalizes_run_session_and_trace'];cwd=ROOT/'source'
elif mode=='operators':
 base=['/home/mtd/miniconda3/bin/python3','-m','pytest','-p','no:cacheprovider','--basetemp',str(temp/'pytest')]
 args=['-q','-c',str(temp/'pytest.ini'),'source/packages/operator-pack/tests/test_flow_field_interpretation.py','source/packages/operator-pack/tests/test_kriging.py','source/packages/operator-pack/tests/test_monitoring_trend_expansion.py','source/packages/operator-pack/tests/test_drastic.py','source/packages/operator-pack/tests/test_table_classify.py','source/packages/operator-pack/tests/test_transfer_learning_audit_semantics.py','source/packages/operator-pack/tests/test_summary_scientific_semantics.py','source/packages/operator-pack/tests/test_render_map_scientific_expression.py'];cwd=ROOT
 (temp/'pytest.ini').write_text('[pytest]\naddopts = --import-mode=importlib\n')
 env['PYTHONPATH']=str(ROOT/'source/packages/operator-pack/src')+':'+str(ROOT/'source/packages/protocol-contracts/src')
else: raise SystemExit('unknown mode')
# Preserve the original evidence logs; future runs get separate names.
if mode in ('contracts','retry'):
 env.pop('OPENAI_AGENTS_DISABLE_TRACING',None)
 build_parent=pathlib.Path('/home/mtd/.cache/groundwater-v6/build')
 build_parent.mkdir(parents=True,exist_ok=True)
 isolated_build=pathlib.Path(tempfile.mkdtemp(prefix='repository-assessment-',dir=build_parent))
 env['PLATFORM_GENERATED_ROOT']=str(isolated_build/'assembly')
cmd=base+args;start=datetime.datetime.now(datetime.timezone.utc).isoformat()
log_stem='recheck-'+mode+'-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
with (OUT/(log_stem+'.log')).open('w') as log:
 log.write('Command: '+repr(cmd)+'\nIsolated temporary root: '+str(temp)+'\n');log.flush()
 result=subprocess.run(cmd,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600)
meta={'check':mode,'start_utc':start,'end_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'command':cmd,'cwd':str(cwd),'exit_code':result.returncode,'temporary_root':str(temp),'database_services_started':False,'provider_calls_requested':False,'note':'operators uses available Python 3.13 host scientific environment; not locked Native release gate' if mode=='operators' else 'Python 3.12 project environment; selected checks, not release acceptance'}
(OUT/(log_stem+'-check.json')).write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n');print(json.dumps(meta,ensure_ascii=False));print('\n'.join((OUT/(log_stem+'.log')).read_text().splitlines()[-30:]))
