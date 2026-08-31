"""Private pre-publish qualification for web-browsing egress nodes."""

from __future__ import annotations

import contextlib
import json
import math
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import median
from typing import Any

from .errors import ValidationError
from .util import atomic_write, dump_yaml, load_yaml_file
from .validator import validate_generated_config

BROWSING_PROVIDER_PREFIX = "cr_browsing_"
BROWSING_POOL_ID = "browsing"
_PROBE_GROUP = "__CR_BROWSING_QUALIFICATION"
_DEFAULT_ATTEMPTS = 3
_DEFAULT_REQUIRED_SUCCESSES = 2
_DEFAULT_WORKERS = 12


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _comment_header(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def load_browsing_probe_spec(policies_path: Path) -> dict[str, Any]:
    """Load the probe referenced by the canonical browsing pool."""
    document = load_yaml_file(policies_path)
    if not isinstance(document, dict):
        raise ValidationError("browsing qualification requires a valid policies mapping")
    pools = document.get("pools")
    probes = document.get("probes")
    if not isinstance(pools, list) or not isinstance(probes, dict):
        raise ValidationError("browsing qualification requires policy pools and probes")

    pool = next(
        (item for item in pools if isinstance(item, dict) and item.get("id") == BROWSING_POOL_ID),
        None,
    )
    if not isinstance(pool, dict):
        raise ValidationError("browsing qualification requires the canonical browsing pool")
    probe_name = pool.get("probe")
    probe = probes.get(probe_name) if isinstance(probe_name, str) else None
    if not isinstance(probe, dict):
        raise ValidationError("browsing qualification probe is missing")

    url = probe.get("url")
    method = probe.get("method")
    expected_status = probe.get("expected_status")
    timeout = probe.get("timeout")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValidationError("browsing qualification probe must use HTTPS")
    if method != "HEAD":
        raise ValidationError("browsing qualification probe must use HEAD")
    if not isinstance(expected_status, str) or not expected_status.strip():
        raise ValidationError("browsing qualification expected status must not be empty")
    if not isinstance(timeout, int) or timeout < 100:
        raise ValidationError("browsing qualification probe has an invalid timeout")
    return {
        "name": probe_name,
        "url": url,
        "expected_status": expected_status,
        "timeout": timeout,
    }


def _browsing_provider_payloads(
    config: dict[str, Any],
) -> dict[str, tuple[dict[str, Any], ...]]:
    providers = config.get("proxy-providers", {})
    if not isinstance(providers, dict):
        raise ValidationError("candidate proxy-providers must be a mapping")
    result: dict[str, tuple[dict[str, Any], ...]] = {}
    for provider_name in sorted(providers):
        if not provider_name.startswith(BROWSING_PROVIDER_PREFIX):
            continue
        provider = providers[provider_name]
        if not isinstance(provider, dict) or not isinstance(provider.get("payload"), list):
            raise ValidationError("browsing qualification provider payload is invalid")
        payload: list[dict[str, Any]] = []
        for proxy in provider["payload"]:
            if not isinstance(proxy, dict) or not isinstance(proxy.get("name"), str):
                raise ValidationError("browsing qualification provider contains an unnamed proxy")
            payload.append(dict(proxy))
        if not payload:
            raise ValidationError("browsing qualification provider must not be empty")
        result[provider_name] = tuple(payload)
    if not result:
        raise ValidationError("browsing qualification found no candidate browsing proxy nodes")
    return result


def _temporary_probe_config(
    base_config: dict[str, Any],
    provider_payloads: dict[str, tuple[dict[str, Any], ...]],
    *,
    mixed_port: int,
    controller_port: int,
    secret: str,
) -> dict[str, Any]:
    original_providers = base_config.get("proxy-providers", {})
    if not isinstance(original_providers, dict):
        raise ValidationError("candidate proxy-providers must be a mapping")

    providers: dict[str, Any] = {}
    for provider_name, payload in provider_payloads.items():
        original_provider = original_providers.get(provider_name)
        if not isinstance(original_provider, dict):
            raise ValidationError(f"browsing provider {provider_name!r} disappeared")
        provider = {
            key: value
            for key, value in original_provider.items()
            if key not in {"health-check", "payload"}
        }
        provider["type"] = "inline"
        provider["payload"] = [dict(proxy) for proxy in payload]
        providers[provider_name] = provider

    config: dict[str, Any] = {
        "mixed-port": mixed_port,
        "allow-lan": False,
        "bind-address": "127.0.0.1",
        "mode": "rule",
        "log-level": "warning",
        "ipv6": bool(base_config.get("ipv6", False)),
        "unified-delay": True,
        "tcp-concurrent": True,
        "external-controller": f"127.0.0.1:{controller_port}",
        "secret": secret,
        "proxy-providers": providers,
        "proxy-groups": [
            {
                "name": _PROBE_GROUP,
                "type": "select",
                "use": sorted(providers),
            }
        ],
        "rules": [f"MATCH,{_PROBE_GROUP}"],
    }
    dns = base_config.get("dns")
    if isinstance(dns, dict):
        probe_dns = dict(dns)
        if probe_dns.get("enable"):
            probe_dns["listen"] = f"127.0.0.1:{_free_port()}"
        config["dns"] = probe_dns
    hosts = base_config.get("hosts")
    if isinstance(hosts, dict):
        config["hosts"] = dict(hosts)
    return config


def _controller_json(
    controller_port: int,
    secret: str,
    path: str,
    *,
    timeout: float = 2.0,
) -> dict[str, Any]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{controller_port}{path}",
        headers={"Authorization": f"Bearer {secret}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValidationError("Mihomo browsing qualification API returned an invalid response")
    return payload


def _wait_for_controller(process: subprocess.Popen[bytes], port: int, secret: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValidationError("Mihomo exited before browsing qualification could start")
        try:
            _controller_json(port, secret, "/version", timeout=0.5)
            return
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
    raise ValidationError("Mihomo controller did not become ready for browsing qualification")


def _wait_for_members(
    process: subprocess.Popen[bytes],
    port: int,
    secret: str,
    expected_names: set[str],
) -> None:
    encoded_group = urllib.parse.quote(_PROBE_GROUP, safe="")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValidationError("Mihomo exited while loading browsing qualification providers")
        try:
            group = _controller_json(port, secret, f"/proxies/{encoded_group}", timeout=0.5)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
            continue
        members = group.get("all")
        if isinstance(members, list) and expected_names.issubset(
            {str(member) for member in members}
        ):
            return
        time.sleep(0.1)
    raise ValidationError("browsing qualification providers did not populate their selector")


def _delay_probe(
    controller_port: int,
    secret: str,
    node_name: str,
    probe: dict[str, Any],
) -> tuple[int | None, str]:
    encoded_name = urllib.parse.quote(node_name, safe="")
    query = urllib.parse.urlencode(
        {
            "url": str(probe["url"]),
            "timeout": int(probe["timeout"]),
            "expected": str(probe["expected_status"]),
        }
    )
    path = f"/proxies/{encoded_name}/delay?{query}"
    timeout = (int(probe["timeout"]) / 1000) + 2
    try:
        response = _controller_json(controller_port, secret, path, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return None, f"controller_http_{exc.code}"
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return None, "probe_error"

    delay = response.get("delay")
    if not isinstance(delay, int) or delay <= 0:
        return None, "invalid_delay"
    return delay, "success"


def _probe_node(
    controller_port: int,
    secret: str,
    node_name: str,
    probe: dict[str, Any],
    *,
    attempts: int,
    required_successes: int,
) -> tuple[bool, tuple[int, ...], dict[str, int]]:
    delays: list[int] = []
    outcomes: dict[str, int] = {}
    for _ in range(attempts):
        delay, outcome = _delay_probe(controller_port, secret, node_name, probe)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if delay is not None:
            delays.append(delay)
    return len(delays) >= required_successes, tuple(delays), outcomes


def _latency_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"min": None, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "min": round(ordered[0], 1),
        "p50": round(float(median(ordered)), 1),
        "p95": round(ordered[p95_index], 1),
        "max": round(ordered[-1], 1),
    }


def probe_browsing_nodes(
    binary: Path,
    config_path: Path,
    probe: dict[str, Any],
    *,
    workers: int = _DEFAULT_WORKERS,
    attempts: int = _DEFAULT_ATTEMPTS,
    required_successes: int = _DEFAULT_REQUIRED_SUCCESSES,
    diagnostics: dict[str, Any] | None = None,
) -> set[str]:
    """Return browsing runtime names that pass a repeatable live HTTPS probe."""
    if attempts < 1:
        raise ValidationError("browsing qualification attempts must be positive")
    if required_successes < 1 or required_successes > attempts:
        raise ValidationError("browsing qualification required successes are invalid")
    if workers < 1:
        raise ValidationError("browsing qualification workers must be positive")

    binary = binary.resolve()
    config_path = config_path.resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValidationError("browsing qualification requires an executable Mihomo binary")
    config = load_yaml_file(config_path)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")
    provider_payloads = _browsing_provider_payloads(config)
    node_names = [
        str(proxy["name"])
        for payload in provider_payloads.values()
        for proxy in payload
    ]
    if len(set(node_names)) != len(node_names):
        raise ValidationError("browsing qualification requires unique runtime proxy names")

    qualified: set[str] = set()
    successful_samples = 0
    failed_samples = 0
    qualified_medians: list[float] = []
    outcomes: dict[str, int] = {}

    with tempfile.TemporaryDirectory(prefix="clash-relay-browsing-") as temp_name:
        workdir = Path(temp_name)
        mixed_port = _free_port()
        controller_port = _free_port()
        secret = "clash-relay-browsing-qualification-only"
        temporary = _temporary_probe_config(
            config,
            provider_payloads,
            mixed_port=mixed_port,
            controller_port=controller_port,
            secret=secret,
        )
        probe_path = workdir / "probe.yaml"
        probe_path.write_text(dump_yaml(temporary), encoding="utf-8")
        try:
            test = subprocess.run(
                [str(binary), "-t", "-d", str(workdir), "-f", str(probe_path)],
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
                env={**os.environ, "TZ": "UTC"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValidationError("failed to execute Mihomo for browsing qualification") from exc
        if test.returncode != 0:
            raise ValidationError("Mihomo rejected the browsing qualification configuration")

        try:
            process = subprocess.Popen(
                [str(binary), "-d", str(workdir), "-f", str(probe_path)],
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                env={**os.environ, "TZ": "UTC"},
                start_new_session=True,
            )
        except OSError as exc:
            raise ValidationError("failed to start Mihomo for browsing qualification") from exc

        try:
            _wait_for_controller(process, controller_port, secret)
            _wait_for_members(process, controller_port, secret, set(node_names))
            worker_count = max(1, min(int(workers), len(node_names)))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(
                        _probe_node,
                        controller_port,
                        secret,
                        node_name,
                        probe,
                        attempts=attempts,
                        required_successes=required_successes,
                    ): node_name
                    for node_name in node_names
                }
                for future in as_completed(futures):
                    node_name = futures[future]
                    passed, delays, node_outcomes = future.result()
                    successful_samples += len(delays)
                    failed_samples += attempts - len(delays)
                    for outcome, count in node_outcomes.items():
                        outcomes[outcome] = outcomes.get(outcome, 0) + count
                    if passed:
                        qualified.add(node_name)
                        qualified_medians.append(float(median(delays)))
        finally:
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)

    summary = {
        "qualification_mode": "pre_publish_browsing",
        "probe": {
            "name": str(probe["name"]),
            "expected_status": str(probe["expected_status"]),
            "timeout_ms": int(probe["timeout"]),
        },
        "attempts_per_node": attempts,
        "required_successes": required_successes,
        "tested_nodes": len(node_names),
        "qualified_nodes": len(qualified),
        "failed_nodes": len(node_names) - len(qualified),
        "successful_samples": successful_samples,
        "failed_samples": failed_samples,
        "qualified_latency_ms": _latency_summary(qualified_medians),
        "outcomes": dict(sorted(outcomes.items())),
    }
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update(summary)
    return qualified


def apply_browsing_qualification(
    config: dict[str, Any],
    qualified_names: set[str],
) -> dict[str, Any]:
    """Prune browsing providers to nodes that passed the pre-publish probe."""
    providers = config.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("candidate proxy provider structure is invalid")

    tested = 0
    qualified = 0
    provider_counts: dict[str, dict[str, int]] = {}
    found = False
    for provider_name in sorted(providers):
        if not provider_name.startswith(BROWSING_PROVIDER_PREFIX):
            continue
        found = True
        provider = providers[provider_name]
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            raise ValidationError("browsing provider payload is invalid")
        kept = [
            proxy
            for proxy in payload
            if isinstance(proxy, dict) and str(proxy.get("name", "")) in qualified_names
        ]
        if not kept:
            raise ValidationError(
                f"browsing qualification left provider {provider_name!r} empty; "
                "refusing to replace the published profile"
            )
        provider["payload"] = kept
        tested += len(payload)
        qualified += len(kept)
        provider_counts[provider_name] = {"tested": len(payload), "qualified": len(kept)}

    if not found:
        raise ValidationError("candidate contains no browsing providers")
    if qualified == 0:
        raise ValidationError("no nodes passed browsing qualification")
    return {
        "tested_nodes": tested,
        "qualified_nodes": qualified,
        "failed_nodes": tested - qualified,
        "providers": provider_counts,
    }


def rewrite_browsing_qualified_candidate(
    candidate_path: Path,
    qualified_names: set[str],
) -> dict[str, Any]:
    try:
        original = candidate_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("failed to read candidate for browsing qualification") from exc
    config = load_yaml_file(candidate_path)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")
    report = apply_browsing_qualification(config, qualified_names)
    validate_generated_config(config)
    atomic_write(candidate_path, _comment_header(original) + dump_yaml(config))
    return report
