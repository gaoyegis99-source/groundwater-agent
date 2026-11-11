"""Pinned transport method selection and per-step conservation evidence."""
import csv
import math
from pathlib import Path

METHOD = 'conservative-afc.v1'


def configure_transport(transport: dict, vendor: Path) -> str:
    mt3d = transport['mt3d']
    method = mt3d.setdefault('numerics', METHOD)
    if method not in (METHOD, 'legacy'):
        raise ValueError('TRANSPORT_NUMERICAL_METHOD_INVALID')
    no_cross = mt3d.get('dsp', {}).get('noCross', False)
    if type(no_cross) is not bool:
        raise ValueError('TRANSPORT_NOCROSS_MUST_BE_BOOLEAN')
    if method == METHOD and transport.get('enabled'):
        if no_cross:
            raise ValueError('AFC_REQUIRES_FULL_CROSS_DISPERSION')
        if mt3d.get('adv', {}).get('mixelm', -1) != -1:
            raise ValueError('AFC_REQUIRES_TVD_ADVECTION')
        rct = mt3d.get('rct', {})
        if rct.get('isothm', 0) or rct.get('ireact', 0):
            raise ValueError('AFC_REACTIVE_TRANSPORT_NOT_VALIDATED')
        if transport.get('initialConcentration', 0) < 0:
            raise ValueError('AFC_NONNEGATIVE_INITIAL_CONCENTRATION_REQUIRED')
        for source in transport.get('mt3dSources', transport.get('sources', [])):
            if source.get('concentration', 0) < 0 or source.get('massRate', 0) < 0:
                raise ValueError('AFC_NONNEGATIVE_SOURCE_REQUIRED')
        gcg = mt3d.setdefault('gcg', {})
        gcg['ncrs'] = 1
        gcg['mxiter'] = max(10, gcg.get('mxiter', 1))
    solver = 'mt3dusgs-afc' if method == METHOD else 'mt3dusgs'
    transport['executable'] = str(vendor / 'bin' / solver)
    mt3d['executable'] = transport['executable']
    return method


def read_audit(path: Path, final_time: float) -> dict:
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError('AFC_AUDIT_MISSING_STEPS')
    values = [{name: float(value) for name, value in row.items()} for row in rows]
    previous = -math.inf
    for row in values:
        if (not all(math.isfinite(v) for v in row.values())
            or row['time'] <= previous or row['minimum_corrected'] < 0
            or abs(row['pair_mass_residual']) > 1e-10 * max(1, row['pair_mass_scale'])
            or row['linear_residual'] > 1e-10):
            raise ValueError('AFC_CONSERVATION_OR_POSITIVITY_CHECK_FAILED')
        previous = row['time']
    if not math.isclose(previous, final_time, rel_tol=1e-6, abs_tol=1e-7):
        raise ValueError('AFC_AUDIT_TIME_INCOMPLETE')
    return {
        'method': METHOD, 'crossDispersionRetained': True,
        'correctionDuringTimeStepping': True, 'postprocessingClipping': False,
        'stepCount': len(rows), 'minimumAcrossAllSteps': min(r['minimum_corrected'] for r in values),
        'maxPairMassResidual': max(abs(r['pair_mass_residual']) for r in values),
        'maxRelativePairMassResidual': max(abs(r['pair_mass_residual']) / max(1, r['pair_mass_scale']) for r in values),
        'maxLinearResidual': max(r['linear_residual'] for r in values),
        'roundoffMassTransfer': math.fsum(r['roundoff_mass_transfer'] for r in values),
        'boundaryMassCorrection': math.fsum(r['boundary_mass_correction'] for r in values),
        'auditFile': 'raw/transport.afc.csv',
        'scope': 'single nonreactive species, TVD advection; full-tensor implicit transport',
    }
