"""Read-only aggregate diagnostics for generated Mihomo candidates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .dns_compatibility import audit_fake_ip_compatibility
from .errors import ValidationError
from .mihomo import load_candidate, validate_with_mihomo
from .validator import validate_generated_config

_PUBLIC_SCENARIO_GROUPS = (
    "代理选择",
    "网页浏览",
    "人工智能",
    "流媒体",
    "消息通讯",
    "下载流量",
)


def _result(*, status: str, tested: int, passed: int, failed: int, skipped: int = 0) -> dict[str, Any]:
    return {
        "status": status,
        "tested": tested,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
    }


def _check_static(candidate: dict[str, Any]) -> bool:
    try:
        validate_generated_config(candidate)
    except ValidationError:
        return False
    return True


def _check_dns_compatibility(candidate: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    try:
        report = audit_fake_ip_compatibility(candidate)
    except ValidationError:
        return False, {}
    safe = {
        key: report[key]
        for key in ("status", "mode", "entries", "compatibility_entries")
        if key in report
    }
    return True, safe


def _public_group_count(candidate: dict[str, Any]) -> tuple[bool, int]:
    groups = candidate.get("proxy-groups")
    if not isinstance(groups, list):
        return False, 0
    visible = {
        str(group.get("name"))
        for group in groups
        if isinstance(group, dict) and group.get("hidden") is not True and group.get("name")
    }
    present = sum(name in visible for name in _PUBLIC_SCENARIO_GROUPS)
    return present == len(_PUBLIC_SCENARIO_GROUPS), present


def diagnose_candidate(
    candidate_path: Path,
    *,
    mihomo_bin: Path | None = None,
    startup_seconds: float = 1.5,
) -> dict[str, Any]:
    """Run read-only diagnostics without exposing node, server, or credential data."""

    candidate = load_candidate(candidate_path)
    checks: dict[str, dict[str, Any]] = {}
    tested = passed = failed = skipped = 0

    tested += 1
    static_ok = _check_static(candidate)
    checks["static_validation"] = {"status": "passed" if static_ok else "failed"}
    passed += int(static_ok)
    failed += int(not static_ok)

    tested += 1
    dns_ok, dns_report = _check_dns_compatibility(candidate)
    checks["dns_compatibility"] = {
        "status": "passed" if dns_ok else "failed",
        **dns_report,
    }
    passed += int(dns_ok)
    failed += int(not dns_ok)

    tested += 1
    groups_ok, group_count = _public_group_count(candidate)
    checks["public_scenario_groups"] = {
        "status": "passed" if groups_ok else "failed",
        "expected": len(_PUBLIC_SCENARIO_GROUPS),
        "present": group_count,
    }
    passed += int(groups_ok)
    failed += int(not groups_ok)

    if mihomo_bin is None:
        skipped += 1
        checks["mihomo_runtime"] = {"status": "skipped"}
    else:
        tested += 1
        try:
            runtime = validate_with_mihomo(
                mihomo_bin,
                candidate_path,
                startup_seconds=startup_seconds,
            )
        except ValidationError:
            runtime_ok = False
            runtime = {}
        else:
            runtime_ok = True
        checks["mihomo_runtime"] = {
            "status": "passed" if runtime_ok else "failed",
            **{
                key: runtime[key]
                for key in ("binary", "version", "config_test", "startup_smoke", "startup_tun_disabled")
                if key in runtime
            },
        }
        passed += int(runtime_ok)
        failed += int(not runtime_ok)

    status = "passed" if failed == 0 else "failed"
    return {
        "status": status,
        "summary": _result(
            status=status,
            tested=tested,
            passed=passed,
            failed=failed,
            skipped=skipped,
        ),
        "checks": checks,
    }
