class DataProblemError(Exception):
    def __init__(self, code: str, status: int = 422, **details):
        super().__init__(code)
        self.code, self.status, self.details = code, status, details
