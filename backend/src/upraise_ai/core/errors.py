class IngestionError(Exception):
    def __init__(self, code: str, *, retryable: bool = False, status: int = 422):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.status = status


class LeaseLost(Exception):
    pass
