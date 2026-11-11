"""Build the private, full-tensor conservative AFC variant from the locked ZIP."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
from zipfile import ZipFile

from build_native_solvers import ARCHIVE_SHA256, BASE_FLAGS, compile_solver, sha256, unpack_sources

SERVICE = Path(__file__).resolve().parents[1]


def patch_source(source: Path) -> list[dict]:
    patches = {
        'gcg1.f': [
            ('      READ(INGCG,*) MXITER,ITER1,ISOLVE,NCRS',
             '      READ(INGCG,*) MXITER,ITER1,ISOLVE,NCRS\n      MXITER=MAX(MXITER,10)'),
            ('      IF(NCRS.GT.0) THEN', '      NCRS=1\n      IF(NCRS.GT.0) THEN', 1),
        ],
        'dsp1.f': [
            ('IF(ICBUND(INDEX,ICOMP).GT.0) THEN', 'IF(ICBUND(INDEX,ICOMP).NE.0) THEN'),
            ('IF(NCRS.GT.0.AND.ICBUND(INDEX,ICOMP).GT.0) THEN',
             'IF(NCRS.GT.0.AND.ICBUND(INDEX,ICOMP).NE.0) THEN'),
        ],
        'mt3d-usgs.f': [
            ('  110         CONTINUE', "  110         CONTINUE\n"
             "              IF(ICNVG.NE.1) ERROR STOP 'AFC_PREDICTOR_NOT_CONVERGED'\n"
             "              CALL AFC_APPLY(ICOMP,DTRANS,TIME2)"),
            ('     &         CALL DSP1BD(ICOMP,DTRANS)',
             '     &         CALL DSP1BD(ICOMP,DTRANS)\n              CALL AFC_BUDGET(ICOMP)'),
        ],
    }
    records = []
    for name, edits in patches.items():
        path=source/name;before=sha256(path);text=path.read_text()
        for edit in edits:
            old,new,*limit=edit
            if (not limit and text.count(old)!=1) or (limit and text.count(old)<1):
                raise ValueError(f'AFC_SOURCE_PATCH_MISMATCH:{name}')
            text=text.replace(old,new,limit[0] if limit else -1)
        path.write_text(text)
        records.append({'path':name,'upstreamSha256':'sha256:'+before,'patchedSha256':'sha256:'+sha256(path)})
    shutil.copyfile(SERVICE/'native/afc.f90',source/'afc.f90')
    records.append({'path':'afc.f90','patchedSha256':'sha256:'+sha256(source/'afc.f90')})
    return records


def build(archive: Path, output: Path, compiler: str) -> None:
    if sha256(archive)!=ARCHIVE_SHA256:
        raise ValueError('DELIVERY_ARCHIVE_SHA256_MISMATCH')
    if output.exists():
        raise ValueError('AFC_BUILD_DIRECTORY_MUST_NOT_EXIST')
    output.mkdir(mode=0o700,parents=True);source=output/'src';source.mkdir();(output/'bin').mkdir()
    with ZipFile(archive) as delivery:
        [root]={item.filename.split('/',1)[0] for item in delivery.infolist()}
        unpack_sources(delivery,root,source,'mt3dusgs')
    patches=patch_source(source)
    flags=[*BASE_FLAGS,'-ffree-line-length-none','-static-libgfortran']
    major=int(subprocess.check_output([compiler,'-dumpversion'],text=True).split('.')[0])
    if major>=10:flags.append('-fallow-argument-mismatch')
    record=compile_solver('mt3dusgs',source,output/'bin/mt3dusgs-afc',compiler,flags)
    record.update(numericalMethod='conservative-afc.v1',patches=patches,flags=flags,
                  compiler=subprocess.check_output([compiler,'--version'],text=True).splitlines()[0],
                  buildRecipeSha256='sha256:'+sha256(Path(__file__)))
    (output/'afc-manifest.json').write_text(json.dumps(record,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--compiler',default='gfortran')
    args=parser.parse_args();build(args.archive.resolve(strict=True),args.output.absolute(),args.compiler)
