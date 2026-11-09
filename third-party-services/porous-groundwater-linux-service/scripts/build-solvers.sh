#!/bin/sh
set -eu

if [ "$#" -ne 3 ]; then
  echo "usage: build-solvers.sh AUTHORIZATION_JSON SOURCE_ROOT OUTPUT_ROOT" >&2
  exit 64
fi

authorization=$1
source_root=$2
output_root=$3
script_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

python3 "$script_root/verify_authorized_source.py" \
  --authorization "$authorization" --source "$source_root"

mkdir -p "$output_root/bin"

# Exact make targets and source subdirectories must be filled from the authorized
# source inventory. This fail-closed check prevents an accidental partial image.
for solver in mf2005 mfnwt mt3dusgs pest; do
  makefile="$source_root/linux/$solver/Makefile"
  if [ ! -f "$makefile" ]; then
    echo "missing approved native build file: $makefile" >&2
    exit 65
  fi
  make -C "$(dirname -- "$makefile")" \
    FC=gfortran FFLAGS="-O2 -fno-fast-math -frecursive" clean all
  install -m 0555 "$(dirname -- "$makefile")/$solver" "$output_root/bin/$solver"
done

python3 "$script_root/write-solver-manifest.py" \
  "$authorization" "$source_root" "$output_root/bin" \
  > "$output_root/solver-manifest.json"
