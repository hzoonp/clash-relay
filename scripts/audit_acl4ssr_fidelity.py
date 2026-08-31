#!/usr/bin/env python3
"""Fail closed if the canonical ACL4SSR manifest drifts from its pinned Online profile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from clash_relay.acl4ssr_reference import validate_acl4ssr_fidelity
from clash_relay.errors import FetchError, GenerationError
from clash_relay.fetch import fetch_subscription


def _normalized(text: str) -> str:
    return text.replace("\r\n", "\n").rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((root / "rules/acl4ssr.yaml").read_text(encoding="utf-8"))
    contract = manifest.get("reference")
    if not isinstance(contract, dict):
        raise GenerationError("canonical ACL4SSR manifest has no reference contract")

    vendored_path = root / "rules/acl4ssr-online.reference.ini"
    vendored = vendored_path.read_text(encoding="utf-8")
    upstream_verified = False
    if not args.offline:
        repository = str(manifest["repository"])
        ref = str(manifest["ref"])
        path = str(contract["path"])
        url = f"https://raw.githubusercontent.com/{repository}/{ref}/{path}"
        try:
            upstream = fetch_subscription(
                url,
                timeout=20,
                max_bytes=int(manifest["max_source_bytes"]),
                allow_http=False,
                allow_file=False,
            )
        except FetchError as exc:
            raise GenerationError("pinned ACL4SSR Online reference could not be fetched") from exc
        if _normalized(upstream) != _normalized(vendored):
            raise GenerationError("vendored ACL4SSR Online reference differs from the pinned upstream bytes")
        upstream_verified = True

    report = validate_acl4ssr_fidelity(manifest, reference_text=vendored)
    report["upstream_reference_verified"] = upstream_verified
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
