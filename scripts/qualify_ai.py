from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.ai_qualification import load_ai_probe_specs, probe_ai_nodes
from clash_relay.ai_service_qualification import rewrite_ai_service_qualified_candidate
from clash_relay.errors import ClashRelayError, ValidationError


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Privately qualify generated AI nodes before production publication."
    )
    parser.add_argument("--candidate", type=_path, required=True)
    parser.add_argument("--policies", type=_path, required=True)
    parser.add_argument("--mihomo-bin", type=_path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    return parser


def _service_diagnostics() -> dict[str, object]:
    return {
        "qualification_mode": "per-service",
        "tested_nodes": 0,
        "selector_failures": 0,
        "probes": {},
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    diagnostics = _service_diagnostics()
    try:
        probes = load_ai_probe_specs(args.policies)
        probe_specs_by_name = {str(probe["name"]): probe for probe in probes}
        qualified_by_probe: dict[str, set[str]] = {}
        expected_tested_nodes: int | None = None
        for probe in probes:
            probe_diagnostics: dict[str, object] = {}
            qualified = probe_ai_nodes(
                args.mihomo_bin,
                args.candidate,
                (probe,),
                workers=args.workers,
                diagnostics=probe_diagnostics,
            )
            name = str(probe["name"])
            tested_nodes = int(probe_diagnostics["tested_nodes"])
            if expected_tested_nodes is None:
                expected_tested_nodes = tested_nodes
            elif tested_nodes != expected_tested_nodes:
                raise ValidationError("AI service probes tested inconsistent node inventories")
            qualified_by_probe[name] = qualified
            diagnostics["tested_nodes"] = tested_nodes
            diagnostics["selector_failures"] = int(diagnostics["selector_failures"]) + int(
                probe_diagnostics["selector_failures"]
            )
            probe_summary = dict(probe_diagnostics["probes"][name])
            probe_summary["qualified_nodes"] = len(qualified)
            diagnostics["probes"][name] = probe_summary

        report = rewrite_ai_service_qualified_candidate(
            args.candidate,
            qualified_by_probe,
            probe_specs_by_name,
        )
        print(
            json.dumps(
                {"status": "qualified", "diagnostics": diagnostics, **report},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except ClashRelayError as exc:
        if diagnostics["probes"]:
            print(
                json.dumps(
                    {"status": "rejected", "diagnostics": diagnostics},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
