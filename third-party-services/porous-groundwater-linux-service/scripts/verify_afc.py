"""Independent finite-volume conservation and rotated Gaussian convergence checks."""
import argparse
import csv
import json
from pathlib import Path
import struct
import subprocess

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve


def matrix(nx, ny, dx, dt, *, cross=0.8, closed=False, variable_volume=False):
    n = nx * ny
    mass = np.full(n, dx * dx, dtype=float)
    if variable_volume:
        mass *= 1 + .4 * np.sin(np.arange(n) * .13)
    rows, cols, values = [], [], []
    diagonal = mass / dt
    # Full symmetric tensor [[1,cross],[cross,1]], including both mixed stencils.
    for row in range(ny):
        for col in range(nx):
            i = row * nx + col
            for dc, dr, value in [(1, 0, -1), (0, 1, -1), (1, 1, -cross / 2), (-1, 1, cross / 2)]:
                c, r = col + dc, row + dr
                if not (0 <= c < nx and 0 <= r < ny):
                    continue
                j = r * nx + c
                rows.extend((i, j)); cols.extend((j, i)); values.extend((value, value))
                diagonal[i] -= value; diagonal[j] -= value
    flags = np.ones(n, dtype=np.int32)
    if not closed:
        flags.reshape(ny, nx)[[0, -1], :] = -1
        flags.reshape(ny, nx)[:, [0, -1]] = -1
    h = sparse.coo_matrix((values, (rows, cols)), shape=(n, n)).tocsr() + sparse.diags(diagonal)
    h = h.tolil()
    for i in np.where(flags < 0)[0]:
        h.rows[i] = [int(i)]; h.data[i] = [1.0]
    h = h.tocsr().astype(np.float32).astype(np.float64)
    offsets = [0, -n, n, -nx, nx, -1, 1, -nx-n, -1-n, 1-n, nx-n,
               -nx+n, -1+n, 1+n, nx+n, -1-nx, 1-nx, -1+nx, 1+nx]
    diagonal_format = np.zeros((n, 19), dtype='<f4')
    coo = h.tocoo()
    for i, j, value in zip(coo.row, coo.col, coo.data):
        diagonal_format[i, offsets.index(int(j-i))] = -value
    return h, diagonal_format, mass, flags


def step(driver, directory, nx, ny, dt, time, h, diagonal, mass, flags, previous):
    b = (mass * previous / dt).astype('<f4')
    b[flags < 0] = previous[flags < 0]
    predictor = spsolve(h, b).astype('<f4')
    with (directory / 'input.bin').open('wb') as out:
        out.write(struct.pack('<iiiff', nx, ny, 1, dt, time))
        for array in (diagonal.ravel(order='F'), -b, predictor, flags):
            out.write(array.tobytes())
    process = subprocess.run([str(driver)], cwd=directory, capture_output=True, timeout=60)
    if process.returncode:
        raise AssertionError(process.stderr.decode() + process.stdout.decode())
    corrected = np.fromfile(directory / 'output.bin', dtype='<f4').astype(float)
    assert corrected.size == nx*ny and np.isfinite(corrected).all() and corrected.min() >= 0
    return corrected, predictor


def gaussian(nx, dx, elapsed, cross=.8):
    coordinate = (np.arange(nx) - (nx-1)/2) * dx
    x, y = np.meshgrid(coordinate, coordinate)
    determinant = 1-cross*cross
    return (np.exp(-(x*x-2*cross*x*y+y*y)/(4*elapsed*determinant)) /
            (4*np.pi*elapsed*np.sqrt(determinant))).ravel()


def run(driver, output):
    output.mkdir(parents=True, exist_ok=False)
    reports = {}
    for name, constant in [('closed-variable-volume-pulse', False), ('closed-variable-volume-constant', True)]:
        directory = output / name; directory.mkdir()
        nx, ny, dt = 24, 20, .1
        h, diag, mass, flags = matrix(nx, ny, 1, dt, closed=True, variable_volume=True)
        c = np.full(nx*ny, .75) if constant else np.zeros(nx*ny)
        if not constant: c[(ny//2)*nx+nx//2] = 1
        initial = float(mass@c); extrema=[]
        for i in range(20):
            c, predictor = step(driver,directory,nx,ny,dt,(i+1)*dt,h,diag,mass,flags,c)
            extrema.append(float(predictor.min()))
        relative = abs(float(mass@c)-initial)/initial
        assert relative < 2e-6, relative
        if constant: assert np.max(np.abs(c-.75)) < 2e-6
        reports[name] = dict(relativeMassError=relative, minimum=float(c.min()), predictorMinimum=min(extrema),steps=20)
    # Independent reservoir budget: total active mass change must equal the
    # original full-tensor boundary flux plus the audited AFC boundary exchange.
    directory=output/'fixed-reservoir-budget';directory.mkdir()
    nx,ny,dt=24,20,.1
    h,diag,mass,flags=matrix(nx,ny,1,dt,variable_volume=True)
    c=np.zeros(nx*ny);c.reshape(ny,nx)[:,0]=1
    active=flags>0;errors=[];boundary_exchange=0.
    for i in range(20):
        previous=c.copy()
        c,_=step(driver,directory,nx,ny,dt,(i+1)*dt,h,diag,mass,flags,c)
        with (directory/'afc-audit.csv').open() as stream:
            audit=list(csv.DictReader(stream))[-1]
        extra=float(audit['boundary_mass_correction'])
        original_flux=-dt*float(np.sum((h@c-mass*c/dt)[active]))
        change=float(mass[active]@(c-previous)[active])
        errors.append(abs(change-original_flux-extra)/max(1.,abs(change)))
        boundary_exchange+=extra
    assert max(errors)<2e-6,errors
    assert abs(boundary_exchange)>1e-6,boundary_exchange
    reports['fixed-reservoir-budget']=dict(maxRelativeBudgetError=max(errors),
        boundaryCorrection=boundary_exchange,minimum=float(c.min()),steps=20)
    convergence=[]
    for nx in (41,81,161):
        directory=output/f'gaussian-{nx}';directory.mkdir()
        dx=8/(nx-1);dt=.04*(dx/.2)**2;steps=round(.08/dt)
        h,diag,mass,flags=matrix(nx,nx,dx,dt)
        c=gaussian(nx,dx,.2);c[flags<0]=0
        for i in range(steps):c,_=step(driver,directory,nx,nx,dt,(i+1)*dt,h,diag,mass,flags,c)
        expected=gaussian(nx,dx,.28);without_cross=gaussian(nx,dx,.28,cross=0)
        relative_l1=float(np.sum(abs(c-expected))*dx*dx)
        convergence.append(dict(grid=nx,dx=dx,dt=dt,steps=steps,L1Error=relative_l1,
                                minimum=float(c.min()), mass=float(mass@c),
                                crossMoment=float(np.sum(c*np.outer((np.arange(nx)-(nx-1)/2)*dx,(np.arange(nx)-(nx-1)/2)*dx).ravel())*dx*dx),
                                noCrossL1Distance=float(np.sum(abs(c-without_cross))*dx*dx)))
    assert convergence[2]['L1Error'] < convergence[1]['L1Error'] < convergence[0]['L1Error'], convergence
    assert convergence[2]['L1Error'] < .025, convergence
    assert abs(convergence[2]['crossMoment']-2*.8*.28) < .01, convergence
    reports['rotatedGaussian']=convergence
    reports['method']='full-tensor implicit AFC with conservative donor-RHS flux limit'
    (output/'verification.json').write_text(json.dumps(reports,indent=2)+'\n')
    print(json.dumps(reports,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--driver',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.driver.resolve(),args.output)
