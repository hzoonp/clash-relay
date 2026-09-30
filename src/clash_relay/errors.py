"""Project-specific errors with safe, human-readable messages."""


class ClashRelayError(Exception):
    """Base class for expected project failures."""


class ConfigurationError(ClashRelayError):
    """The public declaration is invalid or semantically inconsistent."""


class SecretError(ClashRelayError):
    """Secret injection is missing or malformed."""


class FetchError(ClashRelayError):
    """A subscription could not be fetched safely."""


class SubscriptionError(ClashRelayError):
    """A subscription payload could not be parsed safely."""


class UnsafeSubscriptionError(SubscriptionError):
    """An untrusted subscription contains a construct that must never be retried."""


class GenerationError(ClashRelayError):
    """A deterministic candidate could not be generated."""


class ValidationError(ClashRelayError):
    """A candidate failed static or real-core validation."""


class AIProbeRuntimeError(ValidationError):
    """A transient Mihomo probe-runtime failure that may be retried once."""


class CandidateValidationStageError(ValidationError):
    """A validation failure annotated with privacy-safe static diagnostics."""

    def __init__(
        self,
        stage: str,
        *,
        reason: str | None = None,
        service: str | None = None,
        retryable: bool | None = None,
        attempts: int | None = None,
    ) -> None:
        self.stage = stage
        self.reason = reason
        self.service = service
        self.retryable = retryable
        self.attempts = attempts
        super().__init__("candidate validation failed in a classified stage")


class PublicationError(ClashRelayError):
    """A publication safety gate rejected the operation."""


class CommitUnknownError(PublicationError):
    """A remote write may have committed, so automated recovery must stop."""

    def __init__(
        self,
        message: str,
        *,
        production_changed: bool | str = "unknown",
    ) -> None:
        super().__init__(message)
        self.production_changed = production_changed
