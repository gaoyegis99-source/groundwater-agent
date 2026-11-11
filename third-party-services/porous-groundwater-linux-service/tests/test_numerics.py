import csv
from pathlib import Path
import pytest
from porous_remote.numerics import METHOD, configure_transport, read_audit


def transport():
    return {'enabled':True,'initialConcentration':0,'mt3d':{'gcg':{'mxiter':2,'ncrs':0}}}


def test_default_is_full_tensor_and_records_effective_solver_controls():
    value=transport()
    assert configure_transport(value,Path('/vendor'))==METHOD
    assert value['mt3d']['gcg']=={'mxiter':10,'ncrs':1}
    assert value['executable']=='/vendor/bin/mt3dusgs-afc'


@pytest.mark.parametrize('extra,code',[
    ({'dsp':{'noCross':True}},'AFC_REQUIRES_FULL_CROSS_DISPERSION'),
    ({'adv':{'mixelm':0}},'AFC_REQUIRES_TVD_ADVECTION'),
    ({'rct':{'isothm':1}},'AFC_REACTIVE_TRANSPORT_NOT_VALIDATED'),
    ({'numerics':'arbitrary'},'TRANSPORT_NUMERICAL_METHOD_INVALID'),
])
def test_unsupported_modes_fail_explicitly(extra,code):
    value=transport();value['mt3d'].update(extra)
    with pytest.raises(ValueError,match=code):configure_transport(value,Path('/vendor'))


def test_legacy_requires_explicit_selection():
    value=transport();value['mt3d']['numerics']='legacy'
    assert configure_transport(value,Path('/vendor'))=='legacy'
    assert value['executable']=='/vendor/bin/mt3dusgs'
    assert value['mt3d']['gcg']['ncrs']==0


@pytest.mark.parametrize('extra,code',[
    ({'initialConcentration':-1},'AFC_NONNEGATIVE_INITIAL_CONCENTRATION_REQUIRED'),
    ({'mt3dSources':[{'concentration':-1}]},'AFC_NONNEGATIVE_SOURCE_REQUIRED'),
    ({'mt3dSources':[{'massRate':-1}]},'AFC_NONNEGATIVE_SOURCE_REQUIRED'),
])
def test_negative_input_mass_is_rejected(extra,code):
    value=transport();value.update(extra)
    with pytest.raises(ValueError,match=code):configure_transport(value,Path('/vendor'))


def test_audit_preserves_boundary_budget_and_requires_complete_time(tmp_path):
    path=tmp_path/'audit.csv'
    path.write_text('time,minimum_corrected,pair_mass_residual,pair_mass_scale,linear_residual,roundoff_mass_transfer,boundary_mass_correction\n'
                    '10,0,1e-15,100,1e-14,2e-17,0.2\n'
                    '20,0,2e-15,100,2e-14,3e-17,-0.1\n')
    result=read_audit(path,20)
    assert result['boundaryMassCorrection']==pytest.approx(.1)
    assert result['roundoffMassTransfer']==pytest.approx(5e-17,abs=1e-30)
    assert result['stepCount']==2 and not result['postprocessingClipping']
    with pytest.raises(ValueError,match='AFC_AUDIT_TIME_INCOMPLETE'):read_audit(path,30)
    path.write_text(path.read_text().replace('20,0,','10,0,'))
    with pytest.raises(ValueError,match='AFC_CONSERVATION_OR_POSITIVITY_CHECK_FAILED'):read_audit(path,10)


@pytest.mark.parametrize('changed',[
    {'minimum_corrected':-.0001},{'pair_mass_residual':.1},{'linear_residual':.1},{'time':float('nan')},
])
def test_audit_rejects_invalid_science(changed,tmp_path):
    row=dict(time=10,minimum_corrected=0,pair_mass_residual=1e-15,pair_mass_scale=100,
             linear_residual=1e-15,roundoff_mass_transfer=0,boundary_mass_correction=0)
    row.update(changed);path=tmp_path/'audit.csv'
    with path.open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=row);writer.writeheader();writer.writerow(row)
    with pytest.raises(ValueError,match='AFC_CONSERVATION_OR_POSITIVITY_CHECK_FAILED'):read_audit(path,10)
