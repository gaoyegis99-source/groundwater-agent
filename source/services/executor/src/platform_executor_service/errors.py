class JobNotFoundError(RuntimeError):
    pass


class ExecutionServiceError(RuntimeError):
    pass


class ExecutorUnavailableError(RuntimeError):
    pass
