from contextlib import contextmanager
import fcntl


@contextmanager
def runtime_lock(root, exclusive=False):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".runtime.lock").open("a") as handle:
        try:
            fcntl.flock(
                handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
            )
        except BlockingIOError:
            raise RuntimeError("DATA_ROOT_IN_USE") from None
        yield
