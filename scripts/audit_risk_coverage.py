#!/usr/bin/env python3
"""Fail CI when high-risk production modules regress below their coverage floor."""

from __future__ import annotations

import argparse
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Floors intentionally start at or below the measured main-branch baseline.
# Raise individual values as failure-path coverage improves; never lower them to
# make CI green. The global pytest-cov floor remains a separate broad signal.
RISK_COVERAGE_FLOORS: dict[str, float] = {
    "src/clash_relay/publication_decision.py": 95.0,
    "src/clash_relay/production_event_audit.py": 95.0,
    "src/clash_relay/production_lifecycle_result.py": 95.0,
    "src/clash_relay/production_release_stage.py": 95.0,
    "src/clash_relay/production_diagnostics.py": 95.0,
    "src/clash_relay/promotion_guard.py": 90.0,
    "src/clash_relay/production_failure_metrics.py": 90.0,
    "src/clash_relay/service_qualification_result.py": 90.0,
    "src/clash_relay/qualification_pipeline.py": 85.0,
    "src/clash_relay/production_pipeline.py": 85.0,
    "src/clash_relay/release_bundle.py": 95.0,
    "src/clash_relay/release_reconciliation.py": 90.0,
    "src/clash_relay/publishers/cloudflare_kv.py": 80.0,
    "src/clash_relay/qualification_reliability.py": 75.0,
    "src/clash_relay/service_qualification.py": 75.0,
    "src/clash_relay/production_lifecycle.py": 70.0,
    "src/clash_relay/browsing_application.py": 65.0,
    "src/clash_relay/ai_application.py": 85.0,
    "src/clash_relay/ai_probe_environment.py": 95.0,
    "src/clash_relay/transport_qualification.py": 50.0,
    "src/clash_relay/browsing_qualification.py": 50.0,
    "src/clash_relay/ai_qualification.py": 55.0,
    "src/clash_relay/production_application.py": 75.0,
    "src/clash_relay/runtime_identity.py": 95.0,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_xml", type=Path)
    return parser


def _normalized(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


def _ratio(raw: str, label: str) -> float:
    """Return a coverage ratio, refusing values no report should contain.

    ``float("NaN")`` parses without error and loses every threshold comparison,
    so a corrupt report would pass the ratchet silently. Non-finite and
    out-of-range values are therefore rejected before any comparison happens.
    """
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"invalid line-rate for {label!r}") from exc
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"line-rate for {label!r} is not a ratio in [0, 1]: {raw!r}")
    return value


def _coverage_by_filename(path: Path) -> dict[str, float]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"failed to read coverage XML {path}") from exc

    for node in root.iter():
        reported = node.get("line-rate")
        if reported is not None:
            _ratio(reported, node.get("filename") or node.get("name") or node.tag)

    coverage: dict[str, float] = {}
    for node in root.findall(".//class"):
        filename = node.get("filename")
        line_rate = node.get("line-rate")
        if filename is None or line_rate is None:
            continue
        coverage[_normalized(filename)] = _ratio(line_rate, filename) * 100.0
    return coverage


def _lookup(coverage: dict[str, float], expected: str) -> float | None:
    normalized_expected = _normalized(expected)
    exact = coverage.get(normalized_expected)
    if exact is not None:
        return exact
    suffix = "/" + normalized_expected
    matches = [value for name, value in coverage.items() if name.endswith(suffix)]
    if len(matches) == 1:
        return matches[0]
    return None


def audit(path: Path) -> list[str]:
    coverage = _coverage_by_filename(path)
    failures: list[str] = []
    for filename, floor in RISK_COVERAGE_FLOORS.items():
        actual = _lookup(coverage, filename)
        if actual is None:
            failures.append(f"{filename}: missing from coverage report")
            continue
        print(f"risk coverage: {filename} {actual:.2f}% (floor {floor:.2f}%)")
        if actual + 1e-9 < floor:
            failures.append(f"{filename}: {actual:.2f}% < required {floor:.2f}%")
    return failures


def main() -> int:
    args = _parser().parse_args()
    try:
        failures = audit(args.coverage_xml)
    except ValueError as exc:
        print(f"risk coverage audit: {exc}", file=sys.stderr)
        return 2
    if failures:
        for failure in failures:
            print(f"risk coverage regression: {failure}", file=sys.stderr)
        return 1
    print("risk-based coverage ratchet: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
