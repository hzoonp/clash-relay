"""Safe fork-readiness preflight for public and private production inputs."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config_loader import load_project
from .errors import FetchError, PublicationError, ValidationError
from .fetch import fetch_subscription
from .mihomo_matrix import load_mihomo_tags
from .publishers.cloudflare_kv import CloudflareKVPublisher
from .scheduler_policy import load_scheduler_policy
from .secrets import resolve_subscription_urls

_CLOUDFLARE_NAMES = (
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_KV_NAMESPACE_TITLE",
)


def _enabled(project) -> list:
    return [spec for spec in project.subscriptions if spec.enabled]


def _cloudflare_values(environment: Mapping[str, str]) -> tuple[str, str, str]:
    missing = [name for name in _CLOUDFLARE_NAMES if not environment.get(name, "").strip()]
    if missing:
        raise ValidationError(
            "doctor requires Cloudflare setting(s): " + ", ".join(sorted(missing))
        )
    return (
        environment["CLOUDFLARE_API_TOKEN"].strip(),
        environment["CLOUDFLARE_ACCOUNT_ID"].strip(),
        environment["CLOUDFLARE_KV_NAMESPACE_TITLE"].strip(),
    )


def run_doctor(
    *,
    config_path: Path = Path("config.yaml"),
    subscriptions_path: Path = Path("subscriptions.yaml"),
    services_path: Path = Path("services.yaml"),
    policies_path: Path = Path("policies.yaml"),
    secret_file: Path | None = None,
    mihomo_manifest: Path = Path("tools/mihomo-versions.json"),
    public_only: bool = False,
    check_subscriptions: bool = False,
    check_cloudflare: bool = False,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate fork readiness without publishing or exposing private values."""
    if public_only and (check_subscriptions or check_cloudflare):
        raise ValidationError("--public-only cannot be combined with private connectivity checks")

    project = load_project(
        config_path=config_path,
        subscriptions_path=subscriptions_path,
        services_path=services_path,
        policies_path=policies_path,
    )
    scheduler = load_scheduler_policy(policies_path)
    stable_tags = load_mihomo_tags(mihomo_manifest, "stable")
    enabled = _enabled(project)

    report: dict[str, Any] = {
        "status": "passed",
        "public": {
            "status": "ready",
            "enabled_subscriptions": len(enabled),
            "services": len(project.services["services"]),
            "pools": len(project.policies["pools"]),
            "chains": len(project.policies["chains"]),
            "stable_mihomo_cores": len(stable_tags),
            "scheduler_policy_declared": scheduler.declared,
        },
        "subscriptions": {"status": "skipped", "enabled": len(enabled)},
        "cloudflare": {"status": "skipped"},
    }
    if public_only:
        return report

    environment = os.environ if env is None else env
    resolved, _ = resolve_subscription_urls(
        list(project.subscriptions),
        secret_file=secret_file,
        env=environment,
    )
    subscription_report: dict[str, Any] = {
        "status": "ready",
        "enabled": len(enabled),
        "resolved": len(resolved),
    }
    if check_subscriptions:
        generation = project.config["generation"]
        reachable = 0
        for spec in enabled:
            try:
                fetch_subscription(
                    resolved[spec.id],
                    timeout=int(generation["fetch_timeout_seconds"]),
                    max_bytes=int(generation["max_subscription_bytes"]),
                    allow_http=bool(generation["allow_http_subscription_urls"]),
                    allow_file=bool(generation["allow_file_subscription_urls"]),
                )
            except FetchError as exc:
                raise ValidationError(
                    f"subscription connectivity check failed for {spec.id}"
                ) from exc
            reachable += 1
        subscription_report["reachable"] = reachable
    report["subscriptions"] = subscription_report

    if check_cloudflare:
        token, account_id, namespace_title = _cloudflare_values(environment)
        production_key = str(project.config["publishing"]["cloudflare_kv"]["key"])
        try:
            current = CloudflareKVPublisher(
                token=token,
                account_id=account_id,
                namespace_title=namespace_title,
                key_name=production_key,
            ).read()
        except PublicationError as exc:
            raise ValidationError("Cloudflare KV read readiness check failed") from exc
        report["cloudflare"] = {
            "status": "ready",
            "production_key_present": current is not None,
        }
    return report
