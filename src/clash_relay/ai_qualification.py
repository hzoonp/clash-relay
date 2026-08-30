"""Private build-time AI egress qualification for production profiles."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .util import atomic_write, dump_yaml, load_yaml_file
from .validator import validate_generated_config

AI_PROVIDER_PREFIX = "cr_ai_"
AI_POLICY_GROUP = "人工智能"
DEFAULT_PROBE_NAMES = ("ai_openai", "ai_claude", "ai_gemini")
_PROBE_INTERVAL_SECONDS = 86400


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
        if not isinstance(expected, str) or not expected:
            raise ValidationError(f"AI qualification probe {name!r} has no expected status")
        if not isinstance(timeout, int) or timeout < 100:
            raise ValidationError(f"AI qualification probe {name!r} has an invalid timeout")
        result.append(
            {
                "name": name,
                "url": url,
                "expected_status": expected,
                "timeout": timeout,
            }
        )
    return tuple(result)


def _ai_provider_nodes(config: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    providers = config.get("proxy-providers", {})
    if not isinstance(providers, dict):
        raise ValidationError("candidate proxy-providers must be a mapping")
    result: dict[str, tuple[str, ...]] = {}
    for provider_name in sorted(providers):
        if not provider_name.startswith(AI_PROVIDER_PREFIX):
            continue
        provider = providers[provider_name]
        if not isinstance(provider, dict) or not isinstance(provider.get("payload"), list):
            raise ValidationError("AI qualification provider payload is invalid")
        names: list[str] = []
        for proxy in provider["payload"]:
            if not isinstance(proxy, dict) or not isinstance(proxy.get("name"), str):
                raise ValidationError("AI qualification provider contains an unnamed proxy")
            names.append(proxy["name"])
        if names:
            result[provider_name] = tuple(names)
    if not result:
        raise ValidationError("AI qualification found no candidate AI proxy nodes")
    return result


def _probe_copy(
    config_path: Path,
    workdir: Path,
    probe: dict[str, Any],
) -> tuple[Path, int, str]:
    config = load_yaml_file(config_path)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")
    providers = config.get("proxy-providers")
    if not isinstance(providers, dict):
        raise ValidationError("candidate proxy-providers must be a mapping")

    for provider_name, provider in providers.items():
        if not isinstance(provider, dict):
            raise ValidationError("candidate contains an invalid proxy provider")
        if str(provider_name).startswith(AI_PROVIDER_PREFIX):
            provider["health-check"] = {
                "enable": True,
                "url": str(probe["url"]),
                "interval": _PROBE_INTERVAL_SECONDS,
                "timeout": int(probe["timeout"]),
                "lazy": False,
                "expected-status": str(probe["expected_status"]),
            }
        else:
            # The qualification process is private and short-lived. Avoid unrelated
            # provider traffic while the AI candidates are being measured.
            provider.pop("health-check", None)

    controller_port = _free_port()
    secret = "clash-relay-ai-qualification-only"
    config["mixed-port"] = _free_port()
    config["external-controller"] = f"127.0.0.1:{controller_port}"
    config["secret"] = secret
    dns = dict(config.get("dns", {}))
    if dns.get("enable"):
        dns["listen"] = f"127.0.0.1:{_free_port()}"
    config["dns"] = dns
    target = workdir / "ai-probe.yaml"
    target.write_text(dump_yaml(config), encoding="utf-8")
    return target, controller_port, secret


def _controller_json(
    controller_port: int,
    secret: str,
    path: str,
    *,
    timeout: float = 2.0,
) -> dict[str, Any]:
    url = f"http://127.0.0.1:{controller_port}{path}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {secret}"})
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


def _provider_result(
    controller_port: int,
    secret: str,
    provider_name: str,
    expected_names: tuple[str, ...],
) -> tuple[set[str], set[str]]:
    encoded_provider = urllib.parse.quote(provider_name, safe="")
    payload = _controller_json(
        controller_port,
        secret,
        f"/providers/proxies/{encoded_provider}",
        timeout=1.5,
    )
    proxies = payload.get("proxies")
    if not isinstance(proxies, list):
        raise ValidationError("Mihomo provider API returned an invalid proxy list")
    expected = set(expected_names)
    completed: set[str] = set()
    alive: set[str] = set()
    for proxy in proxies:
        if not isinstance(proxy, dict):
            continue
        name = proxy.get("name")
        if not isinstance(name, str) or name not in expected:
            continue
        history = proxy.get("history")
        if not isinstance(history, list) or not history:
            continue
        completed.add(name)
        if proxy.get("alive") is True:
            alive.add(name)
    return completed, alive


def _wait_for_probe_round(
    process: subprocess.Popen[bytes],
    controller_port: int,
    secret: str,
    provider_nodes: dict[str, tuple[str, ...]],
    *,
    probe_timeout_ms: int,
) -> set[str]:
    expected_total = sum(len(names) for names in provider_nodes.values())
    deadline = time.monotonic() + max(20.0, (probe_timeout_ms / 1000) * 3 + 10)
    latest_alive: set[str] = set()
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValidationError("Mihomo exited during AI qualification")
        completed_total = 0
        alive: set[str] = set()
        api_ready = True
        for provider_name, expected_names in provider_nodes.items():
            try:
                completed, provider_alive = _provider_result(
                    controller_port,
                    secret,
                    provider_name,
                    expected_names,
                )
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError):
                api_ready = False
                break
            completed_total += len(completed)
            alive.update(provider_alive)
        if api_ready:
            latest_alive = alive
            if completed_total == expected_total:
                return alive
        time.sleep(0.2)

    # A node that did not produce fresh history within the bounded probe round is
    # treated as failed. This is fail-closed without aborting useful survivors.
    return latest_alive


def _run_probe_round(
    binary: Path,
    config_path: Path,
    provider_nodes: dict[str, tuple[str, ...]],
    probe: dict[str, Any],
) -> set[str]:
    with tempfile.TemporaryDirectory(prefix="clash-relay-ai-") as temp_name:
        workdir = Path(temp_name)
        probe_path, controller_port, secret = _probe_copy(config_path, workdir, probe)
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
            raise ValidationError("failed to execute Mihomo for AI qualification") from exc
        if test.returncode != 0:
            raise ValidationError("Mihomo rejected the temporary AI qualification configuration")

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
            raise ValidationError("failed to start Mihomo for AI qualification") from exc

        try:
            _wait_for_controller(process, controller_port, secret)
            return _wait_for_probe_round(
                process,
                controller_port,
                secret,
                provider_nodes,
                probe_timeout_ms=int(probe["timeout"]),
            )
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
) -> set[str]:
    """Return runtime proxy names that pass every configured AI probe."""
    binary = binary.resolve()
    config_path = config_path.resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValidationError("AI qualification requires an executable Mihomo binary")
    config = load_yaml_file(config_path)
    if not isinstance(config, dict):
        raise ValidationError("candidate is not a YAML mapping")
    provider_nodes = _ai_provider_nodes(config)
    if not probes:
        raise ValidationError("AI qualification requires at least one probe")

    qualified = {name for names in provider_nodes.values() for name in names}
    for probe in probes:
        qualified.intersection_update(_run_probe_round(binary, config_path, provider_nodes, probe))
        if not qualified:
            break
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
