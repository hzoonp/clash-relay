#!/usr/bin/env python3
"""Probe AI endpoint reachability through one subscription without logging proxy secrets."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

import yaml

GEO_MARKERS = (
    "unsupported_country",
    "unsupported country",
    "not available in your country",
    "not available in your region",
    "not supported in your country",
    "not supported in your region",
    "location is not supported",
    "country is not supported",
    "region is not supported",
    "service is not available in your country",
    "territory is not supported",
)

SERVICES = {
    "openai": {
        "url": "https://api.openai.com/v1/models",
        "headers": {"Accept": "application/json"},
    },
    "anthropic": {
        "url": "https://api.anthropic.com/v1/models",
        "headers": {
            "Accept": "application/json",
            "anthropic-version": "2023-06-01",
        },
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta/models",
        "headers": {"Accept": "application/json"},
    },
}


def controller_request(
    base: str,
    secret: str,
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    timeout: float = 5.0,
) -> tuple[int, bytes]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            return int(response.status), response.read(65536)
    except HTTPError as exc:
        return int(exc.code), exc.read(65536)


def wait_for_controller(base: str, secret: str, *, deadline: float = 20.0) -> None:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            status, _ = controller_request(base, secret, "GET", "/version", timeout=1.0)
            if status == 200:
                return
        except (OSError, URLError):
            pass
        time.sleep(0.25)
    raise RuntimeError("Mihomo controller did not become ready")


def select_proxy(base: str, secret: str, group: str, member: str) -> None:
    status, _ = controller_request(
        base,
        secret,
        "PUT",
        f"/proxies/{quote(group, safe='')}",
        {"name": member},
    )
    if status not in {200, 204}:
        raise RuntimeError("Mihomo rejected a private proxy selection")


def fetch_through_proxy(
    proxy_url: str,
    url: str,
    headers: dict[str, str],
    *,
    timeout: float,
) -> tuple[int | None, str | None]:
    opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}))
    request = Request(
        url,
        method="GET",
        headers={"User-Agent": "clash-relay-ai-probe/1.0", **headers},
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(32768).decode("utf-8", errors="replace")
            return int(response.status), body
    except HTTPError as exc:
        body = exc.read(32768).decode("utf-8", errors="replace")
        return int(exc.code), body
    except (OSError, URLError, TimeoutError):
        return None, None


def classify(service: str, status: int | None, body: str | None) -> str:
    if status is None or body is None:
        return "network_error"

    lowered = body.lower()
    if any(marker in lowered for marker in GEO_MARKERS):
        return "geo_blocked"

    if service == "openai":
        if status in {200, 401, 429}:
            return "reachable"
        if status == 403:
            return "uncertain"
        return "uncertain"

    if service == "anthropic":
        if status in {200, 400, 401, 429}:
            return "reachable"
        if status == 403:
            return "uncertain"
        return "uncertain"

    if service == "gemini":
        auth_markers = (
            "api key",
            "api_key_invalid",
            "permission_denied",
            "key not valid",
        )
        if status in {200, 429}:
            return "reachable"
        if status in {400, 401, 403} and any(marker in lowered for marker in auth_markers):
            return "reachable"
        return "uncertain"

    raise ValueError(f"unknown service: {service}")


def subscription_node_names(candidate: Path, source_id: str) -> list[str]:
    document = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    providers = document.get("proxy-providers", {})
    provider = providers.get("cr_general_any")
    if not isinstance(provider, dict):
        raise RuntimeError("general provider is missing")
    payload = provider.get("payload", [])
    prefix = f"[GENERAL:ANY] {source_id}/"
    names = [
        str(proxy["name"])
        for proxy in payload
        if isinstance(proxy, dict)
        and isinstance(proxy.get("name"), str)
        and str(proxy["name"]).startswith(prefix)
    ]
    if not names:
        raise RuntimeError("the requested subscription has no generated nodes")
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--controller", default="http://127.0.0.1:19090")
    parser.add_argument("--controller-secret", default="local-probe-only")
    parser.add_argument("--proxy", default="http://127.0.0.1:17890")
    parser.add_argument("--timeout", type=float, default=4.0)
    args = parser.parse_args()

    nodes = subscription_node_names(args.candidate, args.source_id)
    wait_for_controller(args.controller, args.controller_secret)
    select_proxy(args.controller, args.controller_secret, "人工智能", "节点选择")

    counters = {service: Counter() for service in SERVICES}
    all_three_reachable = 0

    print(f"source={args.source_id}")
    print(f"nodes_tested={len(nodes)}")

    for index, node_name in enumerate(nodes, start=1):
        select_proxy(args.controller, args.controller_secret, "节点选择", node_name)
        time.sleep(0.1)
        per_node: dict[str, str] = {}
        for service, spec in SERVICES.items():
            status, body = fetch_through_proxy(
                args.proxy,
                str(spec["url"]),
                dict(spec["headers"]),
                timeout=args.timeout,
            )
            result = classify(service, status, body)
            counters[service][result] += 1
            per_node[service] = result
        if all(result == "reachable" for result in per_node.values()):
            all_three_reachable += 1
        if index % 10 == 0 or index == len(nodes):
            print(f"progress={index}/{len(nodes)}")

    for service in SERVICES:
        counts = counters[service]
        print(
            f"{service}: reachable={counts['reachable']} "
            f"geo_blocked={counts['geo_blocked']} "
            f"network_error={counts['network_error']} "
            f"uncertain={counts['uncertain']}"
        )
    print(f"all_three_reachable={all_three_reachable}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - diagnostic wrapper must not print private values
        print(f"probe_failed={type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2) from None
