"""Audit the canonical manual client/DNS verification contract."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "tools/client-dns-verification.json"
DOC = ROOT / "docs/client-dns-verification.md"

_REQUIRED_CLIENTS = {"flclash_windows", "flclash_android"}
_REQUIRED_MODES = {"system_proxy", "tun"}
_REQUIRED_NETWORKS = {
    "wifi",
    "mobile_hotspot",
    "china_telecom",
    "china_unicom",
    "china_mobile",
}
_REQUIRED_CHECKS = {
    "profile_refresh",
    "tun_dns_hijack",
    "fake_ip_compatibility",
    "direct_dns",
    "proxy_server_dns",
    "cn_three_net",
    "six_public_groups",
    "uk_region",
    "ai_services",
    "web_browsing",
    "streaming",
    "messaging",
    "download",
    "ipv4_ipv6",
    "network_transition",
    "carrier_auth",
    "banking_auth",
}


def _string_set(value: object, label: str) -> set[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise SystemExit(f"client/DNS verification contract: {label} must be a string list")
    return set(value)


def main() -> int:
    try:
        data = json.loads(MATRIX.read_text(encoding="utf-8"))
        documentation = DOC.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("client/DNS verification contract: failed to load canonical files") from exc

    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise SystemExit("client/DNS verification contract: schema_version must be 1")
    if data.get("strategy") != "cartesian_product":
        raise SystemExit("client/DNS verification contract: strategy must be cartesian_product")

    dimensions = data.get("dimensions")
    if not isinstance(dimensions, dict):
        raise SystemExit("client/DNS verification contract: dimensions must be an object")
    if _string_set(dimensions.get("clients"), "clients") != _REQUIRED_CLIENTS:
        raise SystemExit("client/DNS verification contract: client matrix drift")
    if _string_set(dimensions.get("modes"), "modes") != _REQUIRED_MODES:
        raise SystemExit("client/DNS verification contract: mode matrix drift")
    if _string_set(dimensions.get("networks"), "networks") != _REQUIRED_NETWORKS:
        raise SystemExit("client/DNS verification contract: network matrix drift")

    checks = data.get("checks")
    if not isinstance(checks, list) or not all(isinstance(row, dict) for row in checks):
        raise SystemExit("client/DNS verification contract: checks must be an object list")
    ids = [row.get("id") for row in checks]
    if not all(isinstance(item, str) and item for item in ids) or len(ids) != len(set(ids)):
        raise SystemExit("client/DNS verification contract: check IDs must be unique strings")
    if set(ids) != _REQUIRED_CHECKS:
        raise SystemExit("client/DNS verification contract: required check set drift")

    for row in checks:
        modes = _string_set(row.get("modes"), f"{row.get('id')} modes")
        if not modes or not modes <= _REQUIRED_MODES:
            raise SystemExit("client/DNS verification contract: invalid check mode")
        if row.get("evidence") != "client_runtime":
            raise SystemExit("client/DNS verification contract: checks require client_runtime evidence")
        if f"`{row['id']}`" not in documentation:
            raise SystemExit(
                f"client/DNS verification contract: documentation is missing {row['id']}"
            )

    if "20 base client/mode/network combinations" not in documentation:
        raise SystemExit("client/DNS verification contract: documentation lost matrix cardinality")
    print("client/DNS verification contract: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
