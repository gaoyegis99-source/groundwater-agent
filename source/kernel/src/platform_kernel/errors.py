from collections.abc import Iterator
from contextlib import contextmanager
import sqlite3


class ResourceNotFoundError(LookupError):
    pass


class ConfirmationConflictError(ValueError):
    pass


class UpstreamServiceError(RuntimeError):
    def __init__(self, code: str, agent_run_id: str | None = None, *,
                 diagnostics: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.agent_run_id = agent_run_id
        self.diagnostics = diagnostics


class AgentRunError(RuntimeError):
    def __init__(self, code: str, agent_run_id: str | None = None, *,
                 diagnostics: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.agent_run_id = agent_run_id
        self.diagnostics = diagnostics


class WorkflowDefinitionUnavailableError(RuntimeError):
    pass


class KernelUnavailableError(RuntimeError):
    pass


class WriteLockUnavailableError(RuntimeError):
    """A queued operation could not begin its transaction; no write ran."""


class SchedulerStoreReadonlyError(RuntimeError):
    """A local scheduler transaction rolled back because SQLite was read-only."""


@contextmanager
def scheduler_write() -> Iterator[None]:
    """Classify a rolled-back, database-only scheduler operation."""
    try:
        yield
    except sqlite3.OperationalError as error:
        code = getattr(error, "sqlite_errorcode", None)
        if code is not None and code & 0xFF == sqlite3.SQLITE_READONLY:
            raise SchedulerStoreReadonlyError("KERNEL_SCHEDULER_STORE_READONLY") from error
        raise


class AuthenticationRequiredError(ValueError):
    pass


class AuthorizationDeniedError(ValueError):
    pass


class CapacityLimitError(RuntimeError):
    pass
