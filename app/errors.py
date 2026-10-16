class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str, *, details=None, headers=None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}
        super().__init__(message)
