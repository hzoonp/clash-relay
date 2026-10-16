"""Cloudflare Workers KV names derived from the client-facing production key.

The publisher stores every derived name — release config and manifest objects,
the release pointers, journal and transaction, and the auxiliary state keys —
under Cloudflare's 512-byte UTF-8 key limit. ``config.yaml`` only declares a
character limit for the base key, so the names are declared here once and
validated as a complete set by the configuration semantic check and before any
release I/O, instead of each caller rebuilding the suffixes privately.
"""

from __future__ import annotations

from .errors import PublicationError

KV_KEY_MAX_BYTES = 512
RELEASE_ID_LENGTH = 64

_RELEASE_OBJECT_INFIX = ".release-v1."
_RELEASE_CONFIG_SUFFIX = ".config"
_RELEASE_MANIFEST_SUFFIX = ".manifest"

CURRENT_POINTER_SUFFIX = ".current-release-v1"
PREVIOUS_POINTER_SUFFIX = ".previous-release-v1"
JOURNAL_SUFFIX = ".release-journal-v1"
TRANSACTION_SUFFIX = ".release-transaction-v1"

# Auxiliary state names written outside a release transaction.
PRODUCTION_METRICS_SUFFIX = ".production-metrics-v1"
SCHEDULER_EVIDENCE_SUFFIX = ".scheduler-evidence-v1"
OPERATIONAL_SLO_SUFFIX = ".operational-slo-v1"
AI_QUALIFICATION_CACHE_SUFFIX = ".ai-qualification-cache-v1"
SCHEDULER_STATE_SUFFIXES: tuple[str, ...] = (
    ".scheduler-state-v3",
    ".scheduler-state-v2",
    ".scheduler-state-v1",
)

AUXILIARY_KEY_SUFFIXES: tuple[str, ...] = (
    PRODUCTION_METRICS_SUFFIX,
    SCHEDULER_EVIDENCE_SUFFIX,
    OPERATIONAL_SLO_SUFFIX,
    AI_QUALIFICATION_CACHE_SUFFIX,
    *SCHEDULER_STATE_SUFFIXES,
)


def release_object_prefix(production_key: str) -> str:
    return f"{production_key}{_RELEASE_OBJECT_INFIX}"


def release_config_key(production_key: str, release_id: str) -> str:
    return f"{release_object_prefix(production_key)}{release_id}{_RELEASE_CONFIG_SUFFIX}"


def release_manifest_key(production_key: str, release_id: str) -> str:
    return f"{release_object_prefix(production_key)}{release_id}{_RELEASE_MANIFEST_SUFFIX}"


def auxiliary_state_key(production_key: str, suffix: str) -> str:
    return f"{production_key}{suffix}"


def derived_kv_key_names(production_key: str) -> dict[str, str]:
    """Return every KV name derived from ``production_key``, labeled for diagnostics."""
    release_id = "0" * RELEASE_ID_LENGTH
    names = {
        "release-config": release_config_key(production_key, release_id),
        "release-manifest": release_manifest_key(production_key, release_id),
        "current-pointer": auxiliary_state_key(production_key, CURRENT_POINTER_SUFFIX),
        "previous-pointer": auxiliary_state_key(production_key, PREVIOUS_POINTER_SUFFIX),
        "journal": auxiliary_state_key(production_key, JOURNAL_SUFFIX),
        "transaction": auxiliary_state_key(production_key, TRANSACTION_SUFFIX),
    }
    for suffix in AUXILIARY_KEY_SUFFIXES:
        names[f"auxiliary{suffix}"] = auxiliary_state_key(production_key, suffix)
    return names


def oversized_derived_kv_keys(production_key: str) -> dict[str, int]:
    """Label to UTF-8 byte length for every derived name beyond the KV limit."""
    oversized: dict[str, int] = {}
    for label, name in derived_kv_key_names(production_key).items():
        size = len(name.encode("utf-8"))
        if size > KV_KEY_MAX_BYTES:
            oversized[label] = size
    return oversized


def require_derived_kv_keys(production_key: str) -> None:
    """Refuse a production key whose derived names cannot be stored.

    The low-level publisher keeps its own 512-byte self-check; this gate exists so
    the rejection happens before the first read or write of a release, not after
    an immutable object has already been staged.
    """

    oversized = oversized_derived_kv_keys(production_key)
    if not oversized:
        return
    details = ", ".join(f"{label} ({size} bytes)" for label, size in sorted(oversized.items()))
    raise PublicationError(
        f"production key derives Cloudflare KV names beyond the "
        f"{KV_KEY_MAX_BYTES}-byte limit: {details}"
    )
