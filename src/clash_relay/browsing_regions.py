"""Canonical browsing-region identifiers and runtime group names."""

from __future__ import annotations

import re

BROWSING_PROVIDER_PREFIX = "cr_browsing_"
DEFAULT_BROWSING_REGIONS = ("US", "SG", "JP", "TW", "KR", "HK", "OTHER")
REGION_LABELS = {
    "US": "美国",
    "SG": "新加坡",
    "JP": "日本",
    "TW": "台湾",
    "KR": "韩国",
    "HK": "香港",
    "OTHER": "其他地区",
}
_REGION_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_RUNTIME_SCOPE_RE = re.compile(r"^\[BROWSING:([A-Z][A-Z0-9_]*)\]\s")


def normalize_region(region: str) -> str:
    value = str(region).strip().upper()
    if not _REGION_RE.fullmatch(value):
        raise ValueError(f"invalid browsing region {region!r}")
    return value


def provider_region(provider_name: str) -> str | None:
    name = str(provider_name)
    if not name.startswith(BROWSING_PROVIDER_PREFIX):
        return None
    suffix = name[len(BROWSING_PROVIDER_PREFIX) :]
    if not suffix:
        return None
    region = suffix.upper()
    return region if _REGION_RE.fullmatch(region) else None


def runtime_name_region(runtime_name: str) -> str | None:
    match = _RUNTIME_SCOPE_RE.match(str(runtime_name))
    return match.group(1) if match else None


def region_display_name(region: str) -> str:
    code = normalize_region(region)
    return f"网页 · {REGION_LABELS.get(code, code)}"


def region_stable_group(region: str) -> str:
    return f"__CR_BROWSING_{normalize_region(region)}_STABLE_AUTO"


def region_reserve_group(region: str) -> str:
    return f"__CR_BROWSING_{normalize_region(region)}_RESERVE_AUTO"


def region_from_display_name(name: str) -> str | None:
    for region, label in REGION_LABELS.items():
        if name == f"网页 · {label}":
            return region
    if name.startswith("网页 · "):
        candidate = name.removeprefix("网页 · ").strip().upper()
        return candidate if _REGION_RE.fullmatch(candidate) else None
    return None
