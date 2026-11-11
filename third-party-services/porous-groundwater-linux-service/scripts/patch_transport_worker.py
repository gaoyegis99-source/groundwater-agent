"""Apply a checked, optional MT3D NOCROSS input patch to the private worker."""

import hashlib
from pathlib import Path


def patch_worker(path: Path) -> dict:
    original = path.read_bytes()
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    anchor = b'        longitudinal = float(transport.get("longitudinalDispersivity", 10.0))'
    if original.count(anchor) != 1:
        raise ValueError("UPSTREAM_TRANSPORT_PATCH_ANCHOR_MISMATCH")
    insertion = newline.join([
        b'        if dsp.get("noCross", False):',
        b'            handle.write("$ NOCROSS\\n")', b'',
    ])
    patched = original.replace(anchor, insertion + anchor)
    path.write_bytes(patched)
    digest = lambda value: "sha256:" + hashlib.sha256(value).hexdigest()
    return {"id": "explicit-mt3d-nocross.v1", "originalWorkerSha256": digest(original),
            "patchedWorkerSha256": digest(patched),
            "patchScriptSha256": digest(Path(__file__).read_bytes())}
