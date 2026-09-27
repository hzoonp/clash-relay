"""Audit fake-IP compatibility exclusions for safe, narrow scope."""

from __future__ import annotations

from typing import Any

from .errors import ValidationError

_LOCAL_BASELINE = frozenset({"*.lan", "*.local", "localhost"})
_BROAD_PUBLIC_SUFFIXES = frozenset(
    {
        "com",
        "cn",
        "net",
        "org",
        "com.cn",
        "net.cn",
        "org.cn",
        "gov.cn",
        "co.jp",
        "ne.jp",
        "or.jp",
    }
)
_PROTECTED_AI_ROOTS = frozenset(
    {
        "openai.com",
        "chatgpt.com",
        "anthropic.com",
        "claude.ai",
        "google.com",
        "googleapis.com",
    }
)


def _overlaps_protected_ai(suffix: str) -> bool:
    return any(
        suffix == root or suffix.endswith(f".{root}") or root.endswith(f".{suffix}")
        for root in _PROTECTED_AI_ROOTS
    )


def audit_fake_ip_compatibility(candidate: dict[str, Any]) -> dict[str, Any]:
    """Ensure fake-IP compatibility exclusions stay narrow and non-AI."""

    dns = candidate.get("dns")
    if not isinstance(dns, dict) or dns.get("enable") is not True:
        return {"status": "not_applicable", "mode": "no_managed_dns"}
    if dns.get("enhanced-mode") != "fake-ip":
        return {"status": "not_applicable", "mode": "not_fake_ip"}

    entries = dns.get("fake-ip-filter", [])
    if not isinstance(entries, list) or not all(isinstance(item, str) for item in entries):
        raise ValidationError("fake-IP compatibility filter must be a string list")

    compatibility_entries = 0
    for raw in entries:
        entry = raw.strip().lower().rstrip(".")
        if not entry:
            raise ValidationError("fake-IP compatibility filter contains an empty entry")
        if entry in _LOCAL_BASELINE:
            continue

        if "*" in entry:
            if not entry.startswith("*.") or entry.count("*") != 1:
                raise ValidationError(
                    "fake-IP compatibility wildcard must use one leading '*.' label"
                )
            suffix = entry[2:]
        else:
            suffix = entry

        if "." not in suffix or suffix in _BROAD_PUBLIC_SUFFIXES:
            raise ValidationError(
                f"fake-IP compatibility exclusion {raw!r} is broader than an application domain"
            )
        if _overlaps_protected_ai(suffix):
            raise ValidationError(
                f"fake-IP compatibility exclusion {raw!r} overlaps protected AI domains"
            )
        compatibility_entries += 1

    return {
        "status": "passed",
        "mode": "fake_ip",
        "entries": len(entries),
        "compatibility_entries": compatibility_entries,
    }
