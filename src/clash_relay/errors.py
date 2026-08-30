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


class PublicationError(ClashRelayError):
    """A publication safety gate rejected the operation."""
