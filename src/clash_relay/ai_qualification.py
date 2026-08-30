"""Private build-time AI egress qualification for production profiles."""

from __future__ import annotations

import contextlib
import json
import os
import re
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
from typing import Any

from .errors import ValidationError
from .util import atomic_write, dump_yaml, load_yaml_file
from .validator import validate_generated_config

AI_PROVIDER_PREFIX = "cr_ai_"
AI_POLICY_GROUP = "人工智能"
DEFAULT_PROBE_NAMES = ("ai_openai", "ai_claude", "ai_gemini")
_PROBE_GROUP = "__CR_AI_PROBE"
_DEFAULT_WORKERS = 12
_SHARD_SIZE = 20
_STATUS_TOKEN_RE = re.compile(r"^(\d{3})(?:-(\d{3}))?$")
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


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


def _expected_status_ranges(value: Any) -> tuple[tuple[int, int], ...]:
    text = str(value).strip()
    if not text:
        raise ValidationError("AI qualification expected status must not be empty")
    ranges: list[tuple[int, int]] = []
    for token in text.split("/"):
        match = _STATUS_TOKEN_RE.fullmatch(token.strip())
        if match is None:
            raise ValidationError(f"invalid AI qualification expected status {text!r}")
        lower = int(match.group(1))
        upper = int(match.group(2) or match.group(1))
        if not 100 <= lower <= upper <= 599:
            raise ValidationError(f"invalid AI qualification expected status {text!r}")
        ranges.append((lower, upper))
    return tuple(ranges)


def _status_matches(value: Any, status: int) -> bool:
    return any(lower <= status <= upper for lower, upper in _expected_status_ranges(value))


def load_ai_probe_specs(
    policies_path: Path,
    *,
    names: tuple[str, ...] = DEFAULT_PROBE_NAMES,
) -> tuple[dict[str, Any], ...]:
    document = load_yaml_file(policies_path)
    if not isinstance(document, dict) or not isinstance(document.get("probes"), dict):
        raise ValidationError("AI qualification requires a valid policies probe mapping")
    probes = document["probes"]
    result: list[dict[str, Any]] = []
    for name in names:
        probe = probes.get(name)
        if not isinstance(probe, dict):
            raise ValidationError(f"AI qualification probe {name!r} is missing")
        url = probe.get("url")
        expected = probe.get("expected_status")
        timeout = probe.get("timeout")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValidationError(f"AI qualification probe {name!r} must use HTTPS")
        _expected_status_ranges(expected)
        if not isinstance(timeout, int) or timeout < 100:
            raise ValidationError(f"AI qualification probe {name!r} has an invalid timeout")
        result.append(
            {
                "name": name,
                "url": url,
                "expected_status": str(expected),
                "timeout": timeout,
            }
        )
    return tuple(result)


def _ai_provider_payloads(config: dict[str, Any]) -> dict[str, tuple[dict[str, Any], ...]]:
    providers = config.get("proxy-providers", {})
    if not isinstance(providers, dict):
        raise ValidationError("candidate proxy-providers must be a mapping")
    result: dict[str, tuple[dict[str, Any], ...]] = {}
    for provider_name in sorted(providers):
        if not provider_name.startswith(AI_PROVIDER_PREFIX):
            continue
        provider = providers[provider_name]
        if not isinstance(provider, dict) or not isinstance(provider.get("payload"), list):
            raise ValidationError("AI qualification provider payload is invalid")
        payload: list[dict[str, Any]] = []
        for proxy in provider["payload"]:
            if not isinstance(proxy, dict) or not isinstance(proxy.get("name"), str):
                raise ValidationError("AI qualification provider contains an unnamed proxy")
            payload.append(dict(proxy))
        if payload:
            result[provider_name] = tuple(payload)
    if not result:
        raise ValidationError("AI qualification found no candidate AI proxy nodes")
    return result


def _temporary_probe_config(
    base_config: dict[str, Any],
    *,
    provider_name: str,
    payload: tuple[dict[str, Any], ...],
    mixed_port: int,
    controller_port: int,
    secret: str,
) -> dict[str, Any]:
    original_providers = base_config.get("proxy-providers", {})
    original_provider = original_providers.get(provider_name)
    if not isinstance(original_provider, dict):
        raise ValidationError(f"AI qualification provider {provider_name!r} disappeared")
    provider = {
        key: value
        for key, value in original_provider.items()
        if key not in {"health-check", "payload"}
    }
    provider["type"] = "inline"
    provider["payload"] = [dict(proxy) for proxy in payload]

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
        "proxy-providers": {provider_name: provider},
        "proxy-groups": [
            {
                "name": _PROBE_GROUP,
                "type": "select",
                "use": [provider_name],
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
        raise ValidationError("Mihomo AI qualification API returned an invalid response")
    return payload


def _wait_for_controller(process: subprocess.Popen[bytes], port: int, secret: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValidationError("Mihomo exited before AI qualification could start")
        try:
            _controller_json(port, secret, "/version", timeout=0.5)
            return
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
    raise ValidationError("Mihomo controller did not become ready for AI qualification")


def _wait_for_selector_members(
    process: subprocess.Popen[bytes],
    port: int,
    secret: str,
    expected_names: set[str],
) -> None:
    encoded_group = urllib.parse.quote(_PROBE_GROUP, safe="")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValidationError("Mihomo exited while loading AI qualification provider")
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
    raise ValidationError("AI qualification provider did not populate its selector")


def _select_node(port: int, secret: str, node_name: str) -> bool:
    encoded_group = urllib.parse.quote(_PROBE_GROUP, safe="")
    url = f"http://127.0.0.1:{port}/proxies/{encoded_group}"
    body = json.dumps({"name": node_name}, ensure_ascii=False).encode("utf-8")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        request = urllib.request.Request(
            url,
            data=body,
            method="PUT",
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=1) as response:
                if response.status != 204:
                    return False
            group = _controller_json(port, secret, f"/proxies/{encoded_group}", timeout=1)
            if group.get("now") == node_name:
                return True
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.1)
    return False


def _request_status(mixed_port: int, probe: dict[str, Any]) -> int | None:
    proxy_url = f"http://127.0.0.1:{mixed_port}"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    )
    request = urllib.request.Request(
        str(probe["url"]),
        method="GET",
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Cache-Control": "no-cache",
        },
    )
    timeout = (int(probe["timeout"]) / 1000) + 1
    try:
        with opener.open(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return None


def _selected_node_passes(mixed_port: int, probes: tuple[dict[str, Any], ...]) -> bool:
    worker_count = max(1, min(3, len(probes)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_request_status, mixed_port, probe): probe for probe in probes}
        for future in as_completed(futures):
            status = future.result()
            probe = futures[future]
            if status is None or not _status_matches(probe["expected_status"], status):
                for pending in futures:
                    pending.cancel()
                return False
    return True


def _qualify_shard(
    binary: Path,
    base_config: dict[str, Any],
    provider_name: str,
    payload: tuple[dict[str, Any], ...],
    probes: tuple[dict[str, Any], ...],
) -> set[str]:
    with tempfile.TemporaryDirectory(prefix="clash-relay-ai-") as temp_name:
        workdir = Path(temp_name)
        mixed_port = _free_port()
        controller_port = _free_port()
        secret = "clash-relay-ai-qualification-only"
        config = _temporary_probe_config(
            base_config,
            provider_name=provider_name,
            payload=payload,
            mixed_port=mixed_port,
            controller_port=controller_port,
            secret=secret,
        )
        config_path = workdir / "probe.yaml"
        config_path.write_text(dump_yaml(config), encoding="utf-8")
        try:
            test = subprocess.run(
                [str(binary), "-t", "-d", str(workdir), "-f", str(config_path)],
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
                env={**os.environ, "TZ": "UTC"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValidationError("failed to execute Mihomo for AI qualification") from exc
        if test.returncode != 0:
            raise ValidationError("Mihomo rejected a temporary AI qualification configuration")

        try:
            process = subprocess.Popen(
                [str(binary), "-d", str(workdir), "-f", str(config_path)],
                cwd=workdir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                env={**os.environ, "TZ": "UTC"},
                start_new_session=True,
            )
        except OSError as exc:
            raise ValidationError("failed to start Mihomo for AI qualification") from exc

        try:
            _wait_for_controller(process, controller_port, secret)
            expected_names = {str(proxy["name"]) for proxy in payload}
            _wait_for_selector_members(process, controller_port, secret, expected_names)
            qualified: set[str] = set()
            for proxy in payload:
                name = str(proxy["name"])
                if process.poll() is not None:
                    raise ValidationError("Mihomo exited during AI qualification")
                if not _select_node(controller_port, secret, name):
                    continue
                if _selected_node_passes(mixed_port, probes):
                    qualified.add(name)
            return qualified
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


def probe_ai_nodes(
    binary: Path,
    config_path: Path,
    probes: tuple[dict[str, Any], ...],
    *,
    workers: int = _DEFAULT_WORKERS,
) -> set[str]:
    """Return runtime proxy names that pass every configured live AI service probe."""
    binary = binary.resolve()
    config_path = config_path.resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValidationError("AI qualification requires an executable Mihomo binary")
    config = load_yaml_file(config_path)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")
    provider_payloads = _ai_provider_payloads(config)
    if not probes:
        raise ValidationError("AI qualification requires at least one probe")
    for probe in probes:
        _expected_status_ranges(probe["expected_status"])

    shards: list[tuple[str, tuple[dict[str, Any], ...]]] = []
    for provider_name, payload in provider_payloads.items():
        for start in range(0, len(payload), _SHARD_SIZE):
            shards.append((provider_name, payload[start : start + _SHARD_SIZE]))

    qualified: set[str] = set()
    worker_count = max(1, min(int(workers), len(shards)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                _qualify_shard,
                binary,
                config,
                provider_name,
                payload,
                probes,
            ): (provider_name, index)
            for index, (provider_name, payload) in enumerate(shards)
        }
        for future in as_completed(futures):
            qualified.update(future.result())
    return qualified


def _provider_public_groups(
    groups: list[dict[str, Any]],
    provider_names: set[str],
) -> dict[str, str]:
    anchor_by_provider: dict[str, str] = {}
    for group in groups:
        uses = group.get("use", [])
        if not group.get("hidden", False) or not isinstance(uses, list):
            continue
        for provider_name in uses:
            if provider_name in provider_names:
                anchor_by_provider[str(provider_name)] = str(group["name"])

    public_by_provider: dict[str, str] = {}
    for provider_name, anchor_name in anchor_by_provider.items():
        for group in groups:
            if group.get("hidden", False):
                continue
            if group.get("proxies") == [anchor_name]:
                public_by_provider[provider_name] = str(group["name"])
                break
    return public_by_provider


def apply_ai_qualification(
    config: dict[str, Any],
    qualified_names: set[str],
) -> dict[str, Any]:
    """Prune AI country pools to the nodes that passed every live AI probe."""
    providers = config.get("proxy-providers")
    groups = config.get("proxy-groups")
    if not isinstance(providers, dict) or not isinstance(groups, list):
        raise ValidationError("candidate proxy provider/group structure is invalid")

    ai_provider_names = {name for name in providers if str(name).startswith(AI_PROVIDER_PREFIX)}
    if not ai_provider_names:
        raise ValidationError("candidate contains no AI country providers")
    public_by_provider = _provider_public_groups(groups, ai_provider_names)
    tested = 0
    qualified = 0
    removed_providers: set[str] = set()
    country_counts: dict[str, int] = {}

    for provider_name in sorted(ai_provider_names):
        provider = providers[provider_name]
        payload = provider.get("payload") if isinstance(provider, dict) else None
        if not isinstance(payload, list):
            raise ValidationError("AI provider payload is invalid")
        tested += len(payload)
        kept = [
            proxy
            for proxy in payload
            if isinstance(proxy, dict) and str(proxy.get("name", "")) in qualified_names
        ]
        qualified += len(kept)
        label = public_by_provider.get(provider_name, provider_name)
        country_counts[label] = len(kept)
        if kept:
            provider["payload"] = kept
        else:
            removed_providers.add(provider_name)

    for provider_name in removed_providers:
        providers.pop(provider_name, None)

    remove_group_names: set[str] = set()
    for group in groups:
        uses = group.get("use", [])
        if isinstance(uses, list) and removed_providers.intersection(str(item) for item in uses):
            remove_group_names.add(str(group["name"]))

    ai_policy = next(
        (
            group
            for group in groups
            if not group.get("hidden", False) and group.get("name") == AI_POLICY_GROUP
        ),
        None,
    )
    if not isinstance(ai_policy, dict) or not isinstance(ai_policy.get("proxies"), list):
        raise ValidationError("AI policy group is missing from the generated candidate")

    ai_members = [str(item) for item in ai_policy["proxies"] if str(item) != "DIRECT"]
    by_name = {
        str(group["name"]): group
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("name"), str)
    }
    removed_public: set[str] = set()
    for public_name in ai_members:
        public = by_name.get(public_name)
        if not isinstance(public, dict):
            continue
        references = public.get("proxies", [])
        if not isinstance(references, list) or len(references) != 1:
            continue
        anchor_name = str(references[0])
        anchor = by_name.get(anchor_name)
        if anchor_name in remove_group_names or (
            isinstance(anchor, dict) and anchor.get("proxies") == ["REJECT"]
        ):
            removed_public.add(public_name)
            remove_group_names.add(anchor_name)

    remove_group_names.update(removed_public)
    config["proxy-groups"] = [
        group for group in groups if str(group.get("name")) not in remove_group_names
    ]
    ai_policy["proxies"] = [
        item for item in ai_policy["proxies"] if str(item) not in removed_public
    ]
    live_country_groups = [item for item in ai_policy["proxies"] if str(item) != "DIRECT"]
    if not live_country_groups or qualified == 0:
        raise ValidationError(
            "no nodes passed all AI qualification probes; refusing to replace the published profile"
        )

    return {
        "tested_nodes": tested,
        "qualified_nodes": qualified,
        "country_groups": country_counts,
        "removed_country_groups": sorted(removed_public),
    }


def rewrite_ai_qualified_candidate(
    candidate_path: Path,
    qualified_names: set[str],
) -> dict[str, Any]:
    try:
        original = candidate_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError("failed to read candidate for AI qualification") from exc
    config = load_yaml_file(candidate_path)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")
    report = apply_ai_qualification(config, qualified_names)
    validate_generated_config(config)
    atomic_write(candidate_path, _comment_header(original) + dump_yaml(config))
    return report
