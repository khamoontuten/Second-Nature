"""Domain errors with user-readable messages."""


class SecondNatureError(Exception):
    status_code = 400
    code = "error"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationFailure(SecondNatureError):
    status_code = 422
    code = "validation_failed"


class NotFound(SecondNatureError):
    status_code = 404
    code = "not_found"


class ProviderError(SecondNatureError):
    """Raised by a climate/grid/water provider. ``provider`` names the source."""

    status_code = 502
    code = "provider_error"

    def __init__(self, provider: str, message: str, *, retryable: bool = False, details: dict | None = None):
        super().__init__(f"[{provider}] {message}", details=details)
        self.provider = provider
        self.retryable = retryable


class InsufficientData(SecondNatureError):
    """A model cannot produce a defensible result from the available inputs."""

    status_code = 422
    code = "insufficient_data"


class JobCancelled(SecondNatureError):
    status_code = 409
    code = "cancelled"
