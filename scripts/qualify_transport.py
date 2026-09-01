from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clash_relay.errors import ClashRelayError
from clash_relay.transport_qualification import (
    probe_transport_nodes,
    rewrite_transport_qualified_candidate,
)


def _path(value: str) -> Path:
    return Path(value).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Privately qualify general automatic TCP and UDP/QUIC transport before publication."
    )
    parser.add_argument("--candidate", type=_path, required=True)
    parser.add_argument("--mihomo-bin", type=_path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    diagnostics: dict[str, object] = {}
    try:
        tcp_qualified, udp_qualified, quic_path = probe_transport_nodes(
            args.mihomo_bin,
            args.candidate,
            diagnostics,
        )
        report = rewrite_transport_qualified_candidate(
            args.candidate,
            tcp_qualified,
            udp_qualified,
        )
        print(
            json.dumps(
                {
                    "status": "qualified",
                    "diagnostics": diagnostics,
                    "quic_path_nodes": len(quic_path),
                    **report,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except ClashRelayError as exc:
        if diagnostics:
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
