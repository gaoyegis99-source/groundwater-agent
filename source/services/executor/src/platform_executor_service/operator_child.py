"""Set operator limits in the child before loading the operator runtime."""

from __future__ import annotations

import argparse
import os
import resource
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("timeout_seconds", type=int)
    parser.add_argument("memory_bytes", type=int)
    parser.add_argument("payload")
    parser.add_argument("result")
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CPU, (args.timeout_seconds, args.timeout_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (args.memory_bytes, args.memory_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_FSIZE, (args.memory_bytes, args.memory_bytes))
    os.execv(
        sys.executable,
        [sys.executable, "-m", "moperators.runtime.task", args.payload, args.result],
    )


if __name__ == "__main__":
    main()
