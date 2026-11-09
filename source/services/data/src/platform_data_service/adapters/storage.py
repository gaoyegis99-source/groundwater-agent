from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
from ..errors import DataProblemError
from .database import identity


class LocalStore:
    def __init__(self, root: Path, *, allow_incomplete_restore: bool = False):
        if not allow_incomplete_restore and any(
            (root / name).exists() or (root / name).is_symlink()
            for name in (".restore-stage", ".restore-copying")
        ):
            raise RuntimeError("DATA_RESTORE_INCOMPLETE")
        self.root = root
        self.objects = root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)
        (root / "work").mkdir(exist_ok=True)

    def path(self, obj):
        key = obj["id"]
        if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
            raise RuntimeError("INVALID_INTERNAL_OBJECT_ID")
        return self.objects / key

    def put(self, source, limit):
        obj = {"id": identity()}
        path = self.path(obj)
        sha = hashlib.sha256()
        size = 0
        try:
            with path.open("xb") as out:
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > limit:
                        raise DataProblemError("OBJECT_TOO_LARGE", 413)
                    sha.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            fd = os.open(self.objects, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {**obj, "size": size, "checksum": "sha256:" + sha.hexdigest()}

    @contextmanager
    def open(self, obj):
        try:
            with self.path(obj).open("rb") as stream:
                sha = hashlib.file_digest(stream, "sha256").hexdigest()
                if (
                    os.fstat(stream.fileno()).st_size != obj["size"]
                    or "sha256:" + sha != obj["checksum"]
                ):
                    raise DataProblemError("OBJECT_CORRUPT", 409, object_id=obj["id"])
                stream.seek(0)
                yield stream
        except FileNotFoundError:
            raise DataProblemError("OBJECT_MISSING", 409, object_id=obj["id"]) from None

    def verify(self, obj):
        with self.open(obj):
            pass

    def delete(self, obj):
        self.path(obj).unlink(missing_ok=True)
