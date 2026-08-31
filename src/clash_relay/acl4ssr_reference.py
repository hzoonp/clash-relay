"""Parse the pinned ACL4SSR Online profile and enforce declared fidelity.

The tracked ACL4SSR manifest is allowed to adapt policy-group names to the
clash-relay public surface, but the upstream Online profile remains the source
of truth for baseline ruleset ordering and compatibility-selector defaults.
Every intentional deviation must therefore be declared in the manifest rather
than silently introduced by Routing V2.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .errors import GenerationError

_RAW_HOST = "raw.githubusercontent.com"
_BUILTINS = {"DIRECT", "REJECT", "PASS", "COMPATIBLE"}


def _reference_source_path(source: str, *, repository: str) -> str:
    parsed = urlparse(source)
    if parsed.scheme != "https" or parsed.netloc != _RAW_HOST:
        raise GenerationError("ACL4SSR Online reference uses a non-canonical ruleset URL")
    owner, name = repository.split("/", 1)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[:3] != [owner, name, "master"]:
        raise GenerationError("ACL4SSR Online reference points outside the declared repository")
    return "/".join(parts[3:])


def parse_acl4ssr_online(text: str, *, repository: str) -> dict[str, Any]:
    """Return a normalized view of one ACL4SSR ``ACL4SSR_Online.ini`` file."""

    rulesets: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith((";", "#", "[")):
            continue
        if line.startswith("ruleset="):
            body = line.removeprefix("ruleset=")
            if "," not in body:
                raise GenerationError(
                    f"ACL4SSR Online reference has an invalid ruleset at line {line_number}"
                )
            target, source = (part.strip() for part in body.split(",", 1))
            if source == "[]FINAL":
                rulesets.append({"kind": "final", "target": target})
                continue
            if source.startswith("[]GEOIP,"):
                value = source.removeprefix("[]GEOIP,").strip()
                if not value:
                    raise GenerationError(
                        f"ACL4SSR Online reference has an empty GEOIP value at line {line_number}"
                    )
                rulesets.append(
                    {
                        "kind": "inline",
                        "type": "GEOIP",
                        "value": value,
                        "target": target,
                    }
                )
                continue
            if source.startswith("[]"):
                raise GenerationError(
                    f"ACL4SSR Online reference uses an unsupported inline rule at line {line_number}"
                )
            rulesets.append(
                {
                    "kind": "source",
                    "path": _reference_source_path(source, repository=repository),
                    "target": target,
                }
            )
            continue

        if line.startswith("custom_proxy_group="):
            parts = line.removeprefix("custom_proxy_group=").split("`")
            if len(parts) < 2 or not parts[0] or not parts[1]:
                raise GenerationError(
                    f"ACL4SSR Online reference has an invalid proxy group at line {line_number}"
                )
            name = parts[0]
            group_type = parts[1]
            row: dict[str, Any] = {
                "name": name,
                "type": group_type,
                "members": [part[2:] for part in parts[2:] if part.startswith("[]")],
                "node_wildcard": any(part == ".*" for part in parts[2:]),
            }
            if group_type == "url-test":
                if len(parts) < 5:
                    raise GenerationError(
                        f"ACL4SSR Online url-test group {name!r} is incomplete"
                    )
                row["filter"] = parts[2]
                row["url"] = parts[3]
                try:
                    row["interval"] = int(parts[4])
                    tolerance = next(
                        (int(part) for part in reversed(parts[5:]) if part.strip()),
                        None,
                    )
                except ValueError as exc:
                    raise GenerationError(
                        f"ACL4SSR Online url-test group {name!r} has invalid timing values"
                    ) from exc
                if tolerance is not None:
                    row["tolerance"] = tolerance
            groups.append(row)

    final_rules = [row for row in rulesets if row["kind"] == "final"]
    if len(final_rules) != 1:
        raise GenerationError("ACL4SSR Online reference must contain exactly one FINAL rule")
    if not groups:
        raise GenerationError("ACL4SSR Online reference contains no proxy groups")
    return {"rulesets": rulesets, "groups": groups}


def _manifest_member_name(member: dict[str, Any]) -> str:
    if "builtin" in member:
        return str(member["builtin"])
    if "group" in member:
        return str(member["group"])
    if "auto_pool" in member:
        return f"auto_pool:{member['auto_pool']}"
    raise GenerationError("ACL4SSR manifest contains an invalid group member")


def validate_acl4ssr_fidelity(
    manifest: dict[str, Any],
    *,
    reference_text: str,
) -> dict[str, Any]:
    """Validate the manifest against its pinned ACL4SSR Online reference contract."""

    contract = manifest.get("reference")
    if not isinstance(contract, dict):
        raise GenerationError("canonical ACL4SSR manifest is missing its reference contract")

    parsed = parse_acl4ssr_online(reference_text, repository=str(manifest["repository"]))
    disabled_paths = {str(path) for path in contract.get("disabled_paths", [])}
    disabled_groups = {
        str(reference_name): str(internal_name)
        for reference_name, internal_name in contract.get("disabled_groups", {}).items()
    }
    target_map = {
        str(reference_name): str(internal_name)
        for reference_name, internal_name in contract.get("target_map", {}).items()
    }
    group_map = {
        str(reference_name): str(internal_name)
        for reference_name, internal_name in contract.get("group_map", {}).items()
    }
    adapted_groups = {
        str(reference_name): str(internal_name)
        for reference_name, internal_name in contract.get("adapted_groups", {}).items()
    }
    member_map = {
        str(reference_name): str(internal_name)
        for reference_name, internal_name in contract.get("member_map", {}).items()
    }

    ordered_sources = sorted(
        manifest["sources"], key=lambda row: (row["priority"], row["id"])
    )
    by_path: dict[str, dict[str, Any]] = {}
    for source in ordered_sources:
        path = str(source["path"])
        if path in by_path:
            raise GenerationError(f"ACL4SSR manifest declares duplicate source path {path!r}")
        by_path[path] = source

    baseline_paths: list[str] = []
    baseline_positions: list[int] = []
    source_position = {
        str(row["id"]): index for index, row in enumerate(ordered_sources)
    }
    for reference in parsed["rulesets"]:
        kind = str(reference["kind"])
        reference_target = str(reference["target"])
        expected_target = target_map.get(reference_target)
        if expected_target is None:
            raise GenerationError(
                f"ACL4SSR reference target {reference_target!r} has no declared internal mapping"
            )
        if kind == "source":
            path = str(reference["path"])
            if path in disabled_paths:
                continue
            source = by_path.get(path)
            if source is None:
                raise GenerationError(
                    f"ACL4SSR baseline source {path!r} is missing from the canonical manifest"
                )
            if str(source["target"]) != expected_target:
                raise GenerationError(
                    f"ACL4SSR baseline source {path!r} targets {source['target']!r}, "
                    f"expected {expected_target!r}"
                )
            baseline_paths.append(path)
            baseline_positions.append(source_position[str(source["id"])])
        elif kind == "inline":
            matches = [
                row
                for row in manifest.get("inline_rules", [])
                if str(row["type"]) == str(reference["type"])
                and str(row["value"]) == str(reference["value"])
            ]
            if len(matches) != 1 or str(matches[0]["target"]) != expected_target:
                raise GenerationError("ACL4SSR baseline GEOIP rule does not match the reference")
        elif kind == "final":
            if str(manifest.get("final_target")) != expected_target:
                raise GenerationError("ACL4SSR final target does not match the Online reference")

    if baseline_positions != sorted(baseline_positions):
        raise GenerationError(
            "ACL4SSR baseline source ordering drifted from ACL4SSR_Online.ini"
        )

    extensions = list(contract.get("extensions", []))
    extension_ids = {str(row["source_id"]) for row in extensions}
    baseline_ids = {str(by_path[path]["id"]) for path in baseline_paths}
    unexpected = (
        {str(row["id"]) for row in ordered_sources} - baseline_ids - extension_ids
    )
    if unexpected:
        raise GenerationError(
            "ACL4SSR manifest contains undeclared source extensions: "
            + ", ".join(sorted(unexpected))
        )
    for extension in extensions:
        source_id = str(extension["source_id"])
        extension_source = next(
            (row for row in ordered_sources if str(row["id"]) == source_id),
            None,
        )
        if extension_source is None:
            raise GenerationError(f"declared ACL4SSR extension {source_id!r} is missing")
        before_path = str(extension["before_path"])
        anchor = by_path.get(before_path)
        if anchor is None:
            raise GenerationError(
                f"ACL4SSR extension {source_id!r} references unknown anchor {before_path!r}"
            )
        if int(extension_source["priority"]) >= int(anchor["priority"]):
            raise GenerationError(
                f"ACL4SSR extension {source_id!r} must run before {before_path!r}"
            )

    reference_groups = {str(row["name"]): row for row in parsed["groups"]}
    manifest_groups = {
        str(row["display_name"]): row for row in manifest.get("groups", [])
    }
    for reference_name, internal_name in disabled_groups.items():
        if reference_name not in reference_groups:
            raise GenerationError(
                f"disabled ACL4SSR group {reference_name!r} is absent from the pinned reference"
            )
        if internal_name in manifest_groups:
            raise GenerationError(
                f"disabled ACL4SSR group {internal_name!r} was reintroduced into the manifest"
            )

    for reference_name, internal_name in adapted_groups.items():
        if reference_name not in reference_groups:
            raise GenerationError(
                f"adapted ACL4SSR group {reference_name!r} is absent from the pinned reference"
            )
        if internal_name not in manifest_groups:
            raise GenerationError(
                f"adapted ACL4SSR group target {internal_name!r} is missing from the manifest"
            )

    groups_checked = 0
    for reference_name, internal_name in group_map.items():
        reference = reference_groups.get(reference_name)
        group = manifest_groups.get(internal_name)
        if reference is None or group is None:
            raise GenerationError(
                f"ACL4SSR compatibility group mapping {reference_name!r} -> "
                f"{internal_name!r} is invalid"
            )
        if str(reference["type"]) != str(group.get("type", "select")):
            raise GenerationError(
                f"ACL4SSR compatibility group {internal_name!r} changed runtime type"
            )
        if reference["type"] == "select":
            expected_members = [
                member_map.get(str(member), str(member))
                for member in reference["members"]
            ]
            actual_members = [
                _manifest_member_name(member) for member in group["members"]
            ]
            if actual_members != expected_members:
                raise GenerationError(
                    f"ACL4SSR compatibility group {internal_name!r} changed selector defaults"
                )
        elif reference["type"] == "url-test":
            for key in ("url", "interval", "tolerance"):
                if key in reference and group.get(key) != reference[key]:
                    raise GenerationError(
                        f"ACL4SSR compatibility group {internal_name!r} changed {key}"
                    )
        else:
            raise GenerationError(
                f"ACL4SSR Online reference uses unsupported group type {reference['type']!r}"
            )
        groups_checked += 1

    disabled_reference_paths = {
        str(row["path"])
        for row in parsed["rulesets"]
        if row["kind"] == "source" and str(row["path"]) in disabled_paths
    }
    if disabled_reference_paths != disabled_paths:
        missing = disabled_paths - disabled_reference_paths
        raise GenerationError(
            "declared disabled ACL4SSR paths are absent from the pinned reference: "
            + ", ".join(sorted(missing))
        )

    return {
        "status": "matched_with_declared_deviations",
        "reference_path": str(contract["path"]),
        "baseline_sources": len(baseline_paths),
        "compatibility_groups_checked": groups_checked,
        "adapted_groups": len(adapted_groups),
        "disabled_sources": len(disabled_paths),
        "extensions": sorted(extension_ids),
        "node_wildcards_omitted_for_source_isolation": True,
    }
