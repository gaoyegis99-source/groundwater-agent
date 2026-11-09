class KnowledgeProblemError(Exception):
    def __init__(self, code: str, status: int = 400, details=None):
        super().__init__(code)
        self.code, self.status, self.details = code, status, details
