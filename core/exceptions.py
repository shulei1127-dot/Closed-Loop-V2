class UnsupportedModuleError(ValueError):
    """Raised when an unknown module code is requested."""


class ResourceNotFoundError(LookupError):
    """Raised when a requested resource cannot be found."""


class OperationConflictError(RuntimeError):
    """Raised when an operation is blocked by a running lock or idempotency guard."""


class EnvironmentDependencyError(RuntimeError):
    """Raised when required runtime dependencies are unavailable or not ready."""

    def __init__(
        self,
        *,
        error_type: str,
        public_message: str,
        hint: str | None = None,
        details: dict | None = None,
        status_code: int = 503,
    ) -> None:
        super().__init__(public_message)
        self.error_type = error_type
        self.public_message = public_message
        self.hint = hint
        self.details = details or {}
        self.status_code = status_code
