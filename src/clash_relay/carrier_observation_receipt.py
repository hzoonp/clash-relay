"""Privacy-safe commit boundary between a successful preflight and history.

Phase A (prepare, read-only): the canonical production lifecycle validates the
carrier aggregate, binds it to the exact generated candidate, runs the full
qualification and production-relative gates, and only then issues a local
observation receipt. The receipt is aggregate-only: it carries no node,
server, IP, hostname, source, credential, raw sample, or HMAC key material.

Phase B (commit): ``commit_carrier_observation_receipt`` (and the
``scripts/persist_carrier_observation.py`` entrypoint) accept ONLY such a
receipt — never a bare carrier JSON payload — verify every bound digest, and
require the receipt's ``validated_sha`` to match the commit's expected SHA
before any Cloudflare KV write happens. Digest or SHA mismatches fail closed.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Mapping
from typing import Any

from .carrier_history_application import persist_carrier_observation
from .carrier_qualification import run_carrier_qualification
from .config_loader import ProjectDefinition
from .errors import ValidationError
from .util import stable_json

RECEIPT_SCHEMA_VERSION = 1
RECEIPT_KIND = "clash-relay.carrier-observation-receipt"
RECEIPT_FILENAME = "carrier-observation-receipt.json"
RECEIPT_KEYS = frozenset(
    {
        "receipt_schema_version",
        "kind",
        "aggregate_digest",
        "binding_digest",
        "receipt_mac",
        "collected_epoch",
        "validated_sha",
        "aggregate",
    }
)
_BINDING_FIELDS = ("sample_set_id", "inventory_set_id", "probe_plan_id", "sampler_version")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _aggregate_digest(aggregate: Mapping[str, Any]) -> str:
    return hashlib.sha256(stable_json(aggregate).encode("utf-8")).hexdigest()


def _receipt_mac(receipt: Mapping[str, Any], *, env: Mapping[str, str]) -> str:
    """Authenticate a preflight receipt with the repository-bound carrier key."""
    key = env.get("CLASH_RELAY_CARRIER_HMAC_KEY", "").encode("utf-8")
    repository = env.get("GITHUB_REPOSITORY", "")
    if len(key) < 32 or not repository:
        raise ValidationError("carrier observation receipt requires repository-bound HMAC key")
    unsigned = {name: value for name, value in receipt.items() if name != "receipt_mac"}
    message = (
        b"clash-relay/carrier-observation-receipt/v1\0"
        + repository.encode("utf-8")
        + b"\0"
        + stable_json(unsigned).encode("utf-8")
    )
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def carrier_binding_digest(aggregate: Mapping[str, Any]) -> str:
    """Digest the exact plan metadata the preflight verified against the candidate.

    The four fields are repository-keyed HMAC values recomputed and compared by
    ``verify_carrier_candidate_binding`` during a successful preflight, so this
    digest identifies the bound candidate without exposing any identity.
    """
    values: list[str | int] = []
    for name in _BINDING_FIELDS:
        value = aggregate.get(name)
        if isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool)):
            values.append(value)
        else:
            raise ValidationError("carrier observation receipt lacks complete binding metadata")
    return hashlib.sha256(stable_json(values).encode("utf-8")).hexdigest()


def build_carrier_observation_receipt(
    *,
    aggregate: Mapping[str, Any],
    validated_sha: str,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Issue the Phase-A receipt for one fully validated bound aggregate."""
    if not isinstance(validated_sha, str) or _SHA_PATTERN.fullmatch(validated_sha) is None:
        raise ValidationError("carrier observation receipt requires a valid validated SHA")
    # Fail closed before issuing: the receipt may only describe an aggregate
    # that still validates as a current, well-formed carrier campaign.
    report = run_carrier_qualification(aggregate)
    if report.get("coverage") not in {"partial", "full"}:
        raise ValidationError("carrier observation receipt requires configured carrier coverage")
    collected = aggregate.get("collected_at_epoch")
    if not isinstance(collected, int) or isinstance(collected, bool):
        raise ValidationError("carrier observation receipt requires a collected epoch")
    receipt = {
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "kind": RECEIPT_KIND,
        "aggregate_digest": _aggregate_digest(aggregate),
        "binding_digest": carrier_binding_digest(aggregate),
        "collected_epoch": collected,
        "validated_sha": validated_sha,
        "aggregate": dict(aggregate),
    }
    receipt["receipt_mac"] = _receipt_mac(receipt, env=os.environ if env is None else env)
    return receipt


def parse_carrier_observation_receipt(
    receipt: Mapping[str, Any],
    *,
    now_epoch: int | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate receipt shape and every bound digest; return the aggregate."""
    if not isinstance(receipt, Mapping) or set(receipt) != set(RECEIPT_KEYS):
        raise ValidationError("carrier observation receipt has an unexpected schema")
    if receipt["receipt_schema_version"] != RECEIPT_SCHEMA_VERSION:
        raise ValidationError("carrier observation receipt schema version is unsupported")
    if receipt["kind"] != RECEIPT_KIND:
        raise ValidationError("carrier observation receipt kind is unsupported")
    for name in ("aggregate_digest", "binding_digest"):
        value = receipt[name]
        if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
            raise ValidationError(f"carrier observation receipt {name} is invalid")
    mac = receipt["receipt_mac"]
    if not isinstance(mac, str) or _DIGEST_PATTERN.fullmatch(mac) is None:
        raise ValidationError("carrier observation receipt authentication is invalid")
    expected_mac = _receipt_mac(receipt, env=os.environ if env is None else env)
    if not hmac.compare_digest(mac, expected_mac):
        raise ValidationError("carrier observation receipt authentication mismatch")
    validated_sha = receipt["validated_sha"]
    if not isinstance(validated_sha, str) or (
        validated_sha and _SHA_PATTERN.fullmatch(validated_sha) is None
    ):
        raise ValidationError("carrier observation receipt has an invalid validated SHA")
    if not validated_sha:
        raise ValidationError("carrier observation receipt must record the SHA that validated it")
    aggregate = receipt["aggregate"]
    if not isinstance(aggregate, Mapping):
        raise ValidationError("carrier observation receipt aggregate is invalid")
    if not hmac.compare_digest(receipt["aggregate_digest"], _aggregate_digest(aggregate)):
        raise ValidationError("carrier observation receipt aggregate digest mismatch")
    if not hmac.compare_digest(receipt["binding_digest"], carrier_binding_digest(aggregate)):
        raise ValidationError("carrier observation receipt binding digest mismatch")
    collected = aggregate.get("collected_at_epoch")
    if (
        not isinstance(collected, int)
        or isinstance(collected, bool)
        or collected != receipt["collected_epoch"]
    ):
        raise ValidationError("carrier observation receipt collected epoch mismatch")
    if now_epoch is not None and (collected < 0 or collected > now_epoch):
        raise ValidationError("carrier observation receipt collected epoch is out of range")
    return {
        "aggregate": dict(aggregate),
        "collected_epoch": collected,
        "validated_sha": validated_sha,
    }


def _expected_validated_sha(*, expect_validated_sha: str | None, env: Mapping[str, str]) -> str:
    if expect_validated_sha is not None:
        expectation = expect_validated_sha.strip()
        if not expectation:
            raise ValidationError("carrier observation commit requires an expected validated SHA")
        return expectation
    expectation = env.get("GITHUB_SHA", "").strip()
    if not expectation:
        raise ValidationError(
            "carrier observation commit requires GITHUB_SHA or an explicit expected SHA"
        )
    return expectation


def commit_carrier_observation_receipt(
    *,
    project: ProjectDefinition,
    receipt: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
    expect_validated_sha: str | None = None,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    """Phase B: persist history from a validated receipt or fail closed."""
    environment = os.environ if env is None else env
    validated = parse_carrier_observation_receipt(receipt, now_epoch=now_epoch, env=environment)
    expectation = _expected_validated_sha(
        expect_validated_sha=expect_validated_sha, env=environment
    )
    if not hmac.compare_digest(validated["validated_sha"], expectation):
        raise ValidationError("carrier observation receipt validated SHA mismatch")
    report = run_carrier_qualification(validated["aggregate"], now_epoch=now_epoch)
    return persist_carrier_observation(project=project, report=report, env=environment)
