#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: build-solvers.sh DELIVERY_ZIP EMPTY_PRIVATE_OUTPUT_ROOT" >&2
  exit 64
fi

script_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_root/build_native_solvers.py" "$@"
