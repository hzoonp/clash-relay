"""Stable privacy-safe categories for failed production attempts.

Diagnostics intentionally classify typed failures without returning exception
messages, node identities, subscription URLs, probe endpoints, or credentials.
The categories are observability only: they never alter retry or fail-closed
behavior.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from .errors import (
    CandidateValidationStageError,
    CommitUnknownError,
    ConfigurationError,
    FetchError,
    GenerationError,
    PublicationError,
    SecretError,
    SubscriptionError,
    UnsafeSubscriptionError,
    ValidationError,
)
from .qualification_reliability import QualificationStageRejected


class ProductionFailureCategory(StrEnum):
    CONFIGURATION = "configuration"
    SUBSCRIPTION_FETCH = "subscription_fetch"
    SUBSCRIPTION_ADMISSION = "subscription_admission"
    SUBSCRIPTION_PARSE = "subscription_parse"
    GENERATION = "generation"
    BROWSING_QUALIFICATION = "browsing_qualification"
    AI_QUALIFICATION = "ai_qualification"
    QUALIFICATION = "qualification"
    CLOUDFLARE_PUBLICATION = "cloudflare_publication"
    CLOUDFLARE_COMMIT_UNKNOWN = "cloudflare_commit_unknown"
    CANDIDATE_VALIDATION = "candidate_validation"
    IO_FAILURE = "io_failure"
    UNKNOWN = "unknown"


_SAFE_RUNTIME_SOURCE_ID = re.compile(r"^subscription_[0-9]+$")
_SAFE_CORE_REJECTION_ISOLATIONS = frozenset({"isolated", "combined", "unavailable"})
_SAFE_PROXY_TYPES = frozenset(
    {
        "ss",
        "ssr",
        "vmess",
        "vless",
        "trojan",
        "http",
        "socks5",
        "snell",
        "hysteria",
        "hysteria2",
        "tuic",
        "anytls",
        "wireguard",
        "ssh",
        "mieru",
        "masque",
    }
)

_SAFE_QUALIFICATION_STAGES = frozenset(
    {
        "setup",
        "browsing",
        "history",
        "browsing_rewrite",
        "transport",
        "ai",
        "ai_service",
        "service",
    }
)
_SAFE_SOURCE_FAILURE_CATEGORIES = frozenset(
    {
        "subscription_fetch",
        "subscription_admission",
        "subscription_parse",
        "io_failure",
    }
)
_SAFE_EMPTY_PAYLOAD_SHAPES = frozenset(
    {
        "empty_text",
        "yaml_empty_list",
        "yaml_empty_proxies",
        "remote_provider_only",
        "yaml_empty_inventory",
    }
)
_SAFE_SOURCE_FAILURE_REASONS = frozenset(
    {
        "http_error",
        "tls_error",
        "timeout",
        "dns_error",
        "invalid_url",
        "destination_rejected",
        "size_limit",
        "payload_encoding",
        "io_error",
        "transport_error",
        "unsafe_payload",
        "no_usable_proxies",
        "parse_error",
        "invalid_value",
        "empty_subscription",
        "mixed_invalid_proxies",
        "all_invalid_entries",
        "all_invalid_fields",
        "all_invalid_names",
        "all_missing_types",
        "all_unsupported_types",
        "all_invalid_servers",
        "all_invalid_ports",
        "all_private_hosts",
        "all_missing_required_fields",
        "all_malformed_options",
        "all_unsupported_values",
        "all_other_invalid",
    }
)
_SAFE_CANDIDATE_VALIDATION_STAGES = frozenset(
    {
        "production_pre_audit",
        "qualification_pipeline",
        "production_post_audit",
        "generation_validation",
        "derived_state_load",
        "mihomo_download",
        "publication_validation",
        "ai_setup",
        "ai_cache_fingerprints",
        "ai_service_probe",
        "ai_service_rewrite",
        "ai_service_inputs",
        "ai_service_routes",
        "ai_service_union_prune",
        "ai_service_country_order",
        "ai_service_group_build",
        "ai_service_rules",
        "ai_service_validate",
        "ai_route_postprocess",
        "release_baseline",
        "promotion_guard",
        "mihomo_matrix",
    }
)


def _chain(error: BaseException) -> tuple[BaseException, ...]:
    current: BaseException | None = error
    seen: set[int] = set()
    values: list[BaseException] = []
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        values.append(current)
        current = current.__cause__ or current.__context__
    return tuple(values)


def _qualification_category(error: QualificationStageRejected) -> ProductionFailureCategory:
    stage = error.stage.casefold()
    if "brows" in stage or "transport" in stage:
        return ProductionFailureCategory.BROWSING_QUALIFICATION
    if "ai" in stage or "service" in stage:
        return ProductionFailureCategory.AI_QUALIFICATION
    return ProductionFailureCategory.QUALIFICATION


def _safe_qualification_stage(stage: str) -> str:
    normalized = stage.strip().casefold()
    return normalized if normalized in _SAFE_QUALIFICATION_STAGES else "other"


def _safe_candidate_validation_stage(stage: str) -> str:
    normalized = stage.strip().casefold()
    return normalized if normalized in _SAFE_CANDIDATE_VALIDATION_STAGES else "other"


def _validation_stage(error: BaseException) -> str | None:
    if isinstance(error, CandidateValidationStageError):
        return error.stage
    if isinstance(error, ValidationError):
        stage = getattr(error, "validation_stage", None)
        if isinstance(stage, str):
            return stage
    return None


def _ai_service_rewrite_substage(chain: tuple[BaseException, ...]) -> str | None:
    """Map only known static ValidationError contracts to safe rewrite substages."""

    messages = [str(item) for item in chain if type(item) is ValidationError]
    for message in messages:
        if message == "AI qualification could not resolve every country provider route":
            return "ai_service_routes"
        if message.startswith("AI routing uses unknown preferred region"):
            return "ai_service_country_order"
        if message in {
            "AI service qualification is missing required probe results",
            "candidate proxy provider/group structure is invalid",
            "candidate contains no AI country providers",
            "AI provider payload is invalid",
            "AI service qualification returned unknown candidate nodes",
        }:
            return "ai_service_inputs"
        if message in {
            "AI policy group is missing after qualification",
            "no nodes passed all AI qualification probes; refusing to replace the published profile",
        }:
            return "ai_service_union_prune"
        if message in {
            "AI service filter cannot be empty",
            "AI service routing requires a url-test country anchor",
            "AI service routing cannot compose an existing country filter",
            "AI service fallback requires a country probe template",
            "AI qualification references a missing country group",
        } or message.startswith("AI service fallback template is missing"):
            return "ai_service_group_build"
        if message.startswith("AI service routing requires exactly one ") or message in {
            "AI service routing requires generated ACL4SSR rule providers",
            "AI service routing requires the pinned ACL4SSR AI provider",
            "AI service routing requires the pinned ACL4SSR OpenAI provider",
            "pinned ACL4SSR AI rules changed; service routing requires review",
            "generic ACL4SSR AI rule no longer targets the AI policy group",
        }:
            return "ai_service_rules"
        if message.startswith("generated configuration is invalid:"):
            return "ai_service_validate"
    return None


def _safe_qualification_diagnostics(
    error: QualificationStageRejected,
) -> dict[str, Any] | None:
    diagnostics = error.diagnostics
    if not isinstance(diagnostics, Mapping):
        return None
    result: dict[str, Any] = {}
    isolation = diagnostics.get("core_rejection_isolation")
    if isolation in _SAFE_CORE_REJECTION_ISOLATIONS:
        result["core_rejection_isolation"] = isolation

    sources = diagnostics.get("core_rejection_sources")
    if isinstance(sources, list):
        safe_sources = sorted(
            {
                value
                for value in sources
                if isinstance(value, str) and _SAFE_RUNTIME_SOURCE_ID.fullmatch(value)
            }
        )
        if safe_sources:
            result["core_rejection_sources"] = safe_sources

    proxy_types = diagnostics.get("core_rejection_proxy_types")
    if isinstance(proxy_types, list):
        safe_types = sorted(
            {
                value
                for value in proxy_types
                if isinstance(value, str) and value in _SAFE_PROXY_TYPES
            }
        )
        if safe_types:
            result["core_rejection_proxy_types"] = safe_types
    return result or None


def _safe_promotion_guard_report(error: BaseException) -> dict[str, Any] | None:
    report = getattr(error, "promotion_guard_report", None)
    if not isinstance(report, Mapping):
        return None
    keys = ("status", "reason", "candidate", "baseline", "ratios", "thresholds", "violations")
    return {key: report.get(key) for key in keys}


def sanitize_source_admission_report(report: Mapping[str, Any]) -> dict[str, Any] | None:
    subscriptions = report.get("subscriptions")
    if not isinstance(subscriptions, list):
        return None
    safe_rows: list[dict[str, Any]] = []
    allowed = {
        "id",
        "status",
        "input_nodes",
        "parsed_valid_nodes",
        "skipped_invalid_nodes",
        "post_name_filter_nodes",
        "filtered_by_name",
        "post_multiplier_filter_nodes",
        "filtered_over_multiplier",
        "post_dedup_nodes",
        "nodes",
        "max_node_multiplier",
    }
    for row in subscriptions:
        if not isinstance(row, Mapping):
            continue
        safe_row = {str(key): row.get(key) for key in allowed if key in row}
        category = row.get("failure_category")
        if category in _SAFE_SOURCE_FAILURE_CATEGORIES:
            safe_row["failure_category"] = category
        reason = row.get("failure_reason")
        if reason in _SAFE_SOURCE_FAILURE_REASONS:
            safe_row["failure_reason"] = reason
        payload_shape = row.get("empty_payload_shape")
        if payload_shape in _SAFE_EMPTY_PAYLOAD_SHAPES:
            safe_row["empty_payload_shape"] = payload_shape
        safe_rows.append(safe_row)
    return {
        "successful_subscriptions": report.get("successful_subscriptions"),
        "parsed_nodes": report.get("parsed_nodes"),
        "usable_nodes": report.get("usable_nodes"),
        "name_filtered_nodes": report.get("name_filtered_nodes"),
        "multiplier_filtered_nodes": report.get("multiplier_filtered_nodes"),
        "subscriptions": safe_rows,
    }


def _safe_source_admission_report(error: BaseException) -> dict[str, Any] | None:
    report = getattr(error, "source_admission_report", None)
    if not isinstance(report, Mapping):
        return None
    return sanitize_source_admission_report(report)


def safe_failure_diagnostic(error: BaseException) -> dict[str, Any]:
    """Classify one failure without copying any exception text into output."""

    chain = _chain(error)
    qualification = next(
        (item for item in chain if isinstance(item, QualificationStageRejected)),
        None,
    )
    if isinstance(qualification, QualificationStageRejected):
        qualification_diagnostic: dict[str, Any] = {
            "status": "failed",
            "category": _qualification_category(qualification).value,
            "qualification_stage": _safe_qualification_stage(qualification.stage),
            "qualification_failure_category": qualification.category.value,
            "retryable": qualification.retryable,
        }
        qualification_details = _safe_qualification_diagnostics(qualification)
        if qualification_details is not None:
            qualification_diagnostic["qualification_diagnostics"] = qualification_details
        source_admission = _safe_source_admission_report(error)
        if source_admission is not None:
            qualification_diagnostic["source_admission"] = source_admission
        return qualification_diagnostic

    commit_unknown = next(
        (item for item in chain if isinstance(item, CommitUnknownError)),
        None,
    )
    if isinstance(commit_unknown, CommitUnknownError):
        return {
            "status": "failed",
            "category": ProductionFailureCategory.CLOUDFLARE_COMMIT_UNKNOWN.value,
            "production_changed": commit_unknown.production_changed,
        }

    for item in chain:
        stage = _validation_stage(item)
        if stage is not None:
            if stage == "ai_service_rewrite":
                stage = _ai_service_rewrite_substage(chain) or stage
            diagnostic: dict[str, Any] = {
                "status": "failed",
                "category": ProductionFailureCategory.CANDIDATE_VALIDATION.value,
                "validation_stage": _safe_candidate_validation_stage(stage),
            }
            if stage == "promotion_guard":
                report = _safe_promotion_guard_report(item)
                if report is not None:
                    diagnostic["promotion_guard"] = report
            source_admission = _safe_source_admission_report(item)
            if source_admission is not None:
                diagnostic["source_admission"] = source_admission
            return diagnostic

    category = ProductionFailureCategory.UNKNOWN
    if any(isinstance(item, SecretError | ConfigurationError) for item in chain):
        category = ProductionFailureCategory.CONFIGURATION
    elif any(isinstance(item, FetchError) for item in chain):
        category = ProductionFailureCategory.SUBSCRIPTION_FETCH
    elif any(isinstance(item, UnsafeSubscriptionError) for item in chain):
        category = ProductionFailureCategory.SUBSCRIPTION_ADMISSION
    elif any(isinstance(item, SubscriptionError) for item in chain):
        category = ProductionFailureCategory.SUBSCRIPTION_PARSE
    elif any(isinstance(item, GenerationError) for item in chain):
        category = ProductionFailureCategory.GENERATION
    elif any(isinstance(item, PublicationError) for item in chain):
        category = ProductionFailureCategory.CLOUDFLARE_PUBLICATION
    elif any(isinstance(item, ValidationError) for item in chain):
        category = ProductionFailureCategory.CANDIDATE_VALIDATION
    elif any(isinstance(item, OSError) for item in chain):
        category = ProductionFailureCategory.IO_FAILURE

    return {"status": "failed", "category": category.value}
