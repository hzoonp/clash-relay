#!/usr/bin/env python3
"""Fail CI when high-risk production modules regress below their coverage floor."""

from __future__ import annotations

import argparse
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
    "src/clash_relay/release_bundle.py": 85.0,
    "src/clash_relay/publishers/cloudflare_kv.py": 80.0,
    "src/clash_relay/qualification_reliability.py": 75.0,
    "src/clash_relay/service_qualification.py": 75.0,
    "src/clash_relay/production_lifecycle.py": 70.0,
    "src/clash_relay/browsing_application.py": 65.0,
    "src/clash_relay/ai_application.py": 45.0,
    "src/clash_relay/transport_qualification.py": 50.0,
    "src/clash_relay/browsing_qualification.py": 50.0,
    "src/clash_relay/ai_qualification.py": 48.0,
    "src/clash_relay/production_application.py": 55.0,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_xml", type=Path)
    return parser


def _normalized(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


def _coverage_by_filename(path: Path) -> dict[str, float]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"failed to read coverage XML {path}") from exc

    coverage: dict[str, float] = {}
    for node in root.findall(".//class"):
        filename = node.get("filename")
        line_rate = node.get("line-rate")
        if filename is None or line_rate is None:
            continue
        try:
            percent = float(line_rate) * 100.0
        except ValueError as exc:
            raise ValueError(f"invalid line-rate for {filename!r}") from exc
        coverage[_normalized(filename)] = percent
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
