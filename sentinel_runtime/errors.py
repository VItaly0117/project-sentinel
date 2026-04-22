class SentinelRuntimeError(Exception):
    """Base exception for the runtime."""


class ConfigError(SentinelRuntimeError):
    """Raised when configuration is invalid or incomplete."""


class ExchangeClientError(SentinelRuntimeError):
    """Raised when exchange access fails."""


class CircuitBreakerOpen(ExchangeClientError):
    """Raised when exchange access is paused due to repeated API failures."""


class ReconciliationError(SentinelRuntimeError):
    """Raised when startup reconciliation cannot safely continue."""


class PreflightError(SentinelRuntimeError):
    """Raised when local runtime readiness checks fail before launch."""
