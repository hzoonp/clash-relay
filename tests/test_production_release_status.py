from __future__ import annotations

import pytest

from clash_relay.errors import ValidationError
from clash_relay.production_lifecycle_result import (
    ProductionLifecycleResult,
    ProductionReleaseStatus,
)


def _result(**extra: object) -> ProductionLifecycleResult:
    raw: dict[str, object] = {
        "status": "passed",
        "publication_status": "dry-run",
        "release_phase": "verified",
        "warnings": [],
    }
    raw.update(extra)
    return ProductionLifecycleResult.from_mapping(raw)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("published", ProductionReleaseStatus.PUBLISHED),
        ("unchanged", ProductionReleaseStatus.UNCHANGED),
        ("dry-run", ProductionReleaseStatus.DRY_RUN),
    ],
)
def test_release_status_is_typed(value: str, expected: ProductionReleaseStatus) -> None:
    assert _result(release_status=value).release_status is expected


def test_release_status_is_optional_for_legacy_safe_result_shape() -> None:
    assert _result().release_status is None


@pytest.mark.parametrize("value", [1, False, [], "unknown"])
def test_release_status_rejects_invalid_evidence(value: object) -> None:
    with pytest.raises(ValidationError, match="invalid release status"):
        _ = _result(release_status=value).release_status
