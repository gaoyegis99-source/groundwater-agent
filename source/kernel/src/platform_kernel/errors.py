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


class AuthenticationRequiredError(ValueError):
    pass


class AuthorizationDeniedError(ValueError):
    pass


class CapacityLimitError(RuntimeError):
    pass
