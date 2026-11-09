"""Linux OS boundary for query children; fail closed when unavailable."""

import ctypes
import ctypes.util
import os
from pathlib import Path
import resource
import sys
import sysconfig


class Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def restrict_query(objects: Path, work: Path, settings):
    libc = ctypes.CDLL(None, use_errno=True)
    seccomp = ctypes.CDLL(
        ctypes.util.find_library("seccomp") or "libseccomp.so.2", use_errno=True
    )
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise RuntimeError("NO_NEW_PRIVS_UNAVAILABLE")
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 1:
        raise RuntimeError("LANDLOCK_UNAVAILABLE")
    # Handle every filesystem action supported by this ABI (incl. truncate on ABI >= 3).
    bits = 15 if abi >= 3 else 14 if abi >= 2 else 13
    handled = (1 << bits) - 1
    ruleset = Ruleset(handled)
    fd = libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0)
    if fd < 0:
        raise RuntimeError("LANDLOCK_CREATE_FAILED")
    read = (1 << 2) | (1 << 3)
    try:
        for path, access in (
            (objects, read),
            (work, handled & ~1),
            (Path(sys.prefix), read),
            # A virtualenv keeps its standard library under the base interpreter.
            (Path(sysconfig.get_path("stdlib")), read),
        ):
            parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule(access, parent)
                if libc.syscall(445, fd, 1, ctypes.byref(rule), 0) != 0:
                    raise RuntimeError("LANDLOCK_RULE_FAILED")
            finally:
                os.close(parent)
        if libc.syscall(446, fd, 0) != 0:
            raise RuntimeError("LANDLOCK_RESTRICT_FAILED")
    finally:
        os.close(fd)
    ctx = seccomp.seccomp_init(0x7FFF0000)
    if not ctx:
        raise RuntimeError("SECCOMP_UNAVAILABLE")
    try:
        for name in (b"socket", b"connect", b"execve", b"execveat", b"ptrace"):
            number = seccomp.seccomp_syscall_resolve_name(name)
            if (
                number >= 0
                and seccomp.seccomp_rule_add(ctx, 0x00050000 | 1, number, 0) != 0
            ):
                raise RuntimeError("SECCOMP_RULE_FAILED")
        if seccomp.seccomp_load(ctx) != 0:
            raise RuntimeError("SECCOMP_LOAD_FAILED")
    finally:
        seccomp.seccomp_release(ctx)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (settings.temp_mb * 1024**2,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (settings.query_seconds + 2,) * 2)


class ArgumentComparison(ctypes.Structure):
    _fields_ = [
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_int),
        ("datum_a", ctypes.c_uint64),
        ("datum_b", ctypes.c_uint64),
    ]


def install_container_profile(path):
    """Install the pinned default-deny profile using container libseccomp.

    Some host Docker/libseccomp combinations synthesize ENOSYS for Landlock even
    with an explicit OCI allow rule. Installing before Worker startup avoids that
    host resolver while preserving a default-deny filter inherited by children.
    Worker has zero Linux capabilities; capability-gated allow rules never apply.
    """
    import json
    import platform

    import hashlib

    raw = Path(path).read_bytes()
    if (
        hashlib.sha256(raw).hexdigest()
        != "9c1025c88ccaa517b648da571961838744ea2137f176bfe6a48b21294cae9c76"
    ):
        raise RuntimeError("CONTAINER_SECCOMP_PROFILE_MISMATCH")
    profile = json.loads(raw)
    if profile["defaultAction"] != "SCMP_ACT_ERRNO":
        raise RuntimeError("DEFAULT_DENY_PROFILE_REQUIRED")
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    if architecture is None:
        raise RuntimeError("UNSUPPORTED_SANDBOX_ARCHITECTURE")
    library = ctypes.CDLL(ctypes.util.find_library("seccomp") or "libseccomp.so.2")
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.POINTER(ArgumentComparison),
    ]
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    for name in (
        b"landlock_create_ruleset",
        b"landlock_add_rule",
        b"landlock_restrict_self",
    ):
        if library.seccomp_syscall_resolve_name(name) < 0:
            raise RuntimeError("CONTAINER_LIBSECCOMP_TOO_OLD")
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    if ctypes.CDLL(None).prctl(38, 1, 0, 0, 0) != 0:
        raise RuntimeError("NO_NEW_PRIVS_UNAVAILABLE")
    ctx = library.seccomp_init(0x00050000 | profile.get("defaultErrnoRet", 1))
    if not ctx:
        raise RuntimeError("SECCOMP_UNAVAILABLE")
    try:
        for rule in profile["syscalls"]:
            includes, excludes = rule.get("includes", {}), rule.get("excludes", {})
            if includes.get("caps") or includes.get("minKernel"):
                continue
            if includes.get("arches") and architecture not in includes["arches"]:
                continue
            if architecture in excludes.get("arches", []):
                continue
            action = (
                0x7FFF0000
                if rule["action"] == "SCMP_ACT_ALLOW"
                else 0x00050000 | rule.get("errnoRet", 1)
            )
            arguments = rule.get("args", [])
            comparisons = (ArgumentComparison * len(arguments))(
                *[
                    ArgumentComparison(
                        a["index"],
                        {"SCMP_CMP_NE": 1, "SCMP_CMP_EQ": 4, "SCMP_CMP_MASKED_EQ": 7}[
                            a["op"]
                        ],
                        a["value"],
                        a.get("valueTwo", 0),
                    )
                    for a in arguments
                ]
            )
            for name in rule["names"]:
                number = library.seccomp_syscall_resolve_name(name.encode())
                if (
                    number >= 0
                    and library.seccomp_rule_add_array(
                        ctx, action, number, len(arguments), comparisons
                    )
                    != 0
                ):
                    raise RuntimeError("CONTAINER_SECCOMP_RULE_FAILED")
        if library.seccomp_load(ctx) != 0:
            raise RuntimeError("CONTAINER_SECCOMP_LOAD_FAILED")
    finally:
        library.seccomp_release(ctx)
