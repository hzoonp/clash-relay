"""Compile declarative routing metadata into an auditable scenario model.

Routing Model V2 deliberately separates four concepts that used to be encoded
implicitly in proxy-group names:

- scenario: the user/workload intent (direct, general, browsing, media, download, ai, final)
- service: an optional application/service inside a scenario
- source_use: the subscription permission boundary used for reachability audits
- route target: the Mihomo policy group that implements the decision

The compiler is intentionally side-effect free.  It is used by build reports,
production audits, shadow comparison, and tests; Mihomo materialization remains
owned by the normal generator and policy post-processors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ConfigurationError

SCENARIOS = frozenset({"direct", "general", "browsing", "media", "download", "ai", "final"})


@dataclass(frozen=True, slots=True)
class RouteBinding:
    source_id: str
    target: str
    scenario: str
    service: str | None
    source_use: str | None
    priority: int

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source_id": self.source_id,
            "target": self.target,
            "scenario": self.scenario,
            "priority": self.priority,
        }
        if self.service is not None:
            result["service"] = self.service
        if self.source_use is not None:
            result["source_use"] = self.source_use
        return result


@dataclass(frozen=True, slots=True)
class RouteTarget:
    id: str
    display_name: str
    scenario: str
    service: str | None
    deterministic: bool

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "display_name": self.display_name,
            "scenario": self.scenario,
            "deterministic": self.deterministic,
        }
        if self.service is not None:
            result["service"] = self.service
        return result


def _scenario(value: Any, *, context: str) -> str:
    scenario = str(value)
    if scenario not in SCENARIOS:
        raise ConfigurationError(f"{context} uses unknown routing scenario {scenario!r}")
    return scenario


def compile_routing_model(manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the normalized Routing Model V2 view of one ACL4SSR manifest."""

    if manifest is None:
        return None

    targets: dict[str, RouteTarget] = {}
    for row in manifest.get("groups", []):
        route = row.get("route")
        if not isinstance(route, dict):
            continue
        display_name = str(row["display_name"])
        targets[display_name] = RouteTarget(
            id=str(row["id"]),
            display_name=display_name,
            scenario=_scenario(route["scenario"], context=f"routing group {row['id']!r}"),
            service=str(route["service"]) if route.get("service") is not None else None,
            deterministic=bool(route.get("deterministic", False)),
        )

    bindings: list[RouteBinding] = []
    for row in [*manifest.get("sources", []), *manifest.get("inline_rules", [])]:
        target = str(row["target"])
        target_meta = targets.get(target)
        raw_scenario = row.get("scenario")
        if raw_scenario is None and target_meta is not None:
            raw_scenario = target_meta.scenario
        if raw_scenario is None:
            raise ConfigurationError(
                f"routing source {row['id']!r} has no scenario and target {target!r} "
                "does not declare one"
            )
        raw_service = row.get("service")
        service = (
            str(raw_service)
            if raw_service is not None
            else target_meta.service if target_meta is not None else None
        )
        bindings.append(
            RouteBinding(
                source_id=str(row["id"]),
                target=target,
                scenario=_scenario(raw_scenario, context=f"routing source {row['id']!r}"),
                service=service,
                source_use=str(row["source_use"]) if row.get("source_use") is not None else None,
                priority=int(row["priority"]),
            )
        )

    final_target = manifest.get("final_target")
    if final_target is not None:
        target_name = str(final_target)
        target_meta = targets.get(target_name)
        final_scenario = str(manifest.get("final_scenario", "final"))
        bindings.append(
            RouteBinding(
                source_id="__final__",
                target=target_name,
                scenario=_scenario(final_scenario, context="final route"),
                service=None,
                source_use=(
                    str(manifest["final_source_use"])
                    if manifest.get("final_source_use") is not None
                    else None
                ),
                priority=10001,
            )
        )
        if target_meta is not None and target_meta.scenario not in {"final", "general"}:
            raise ConfigurationError("final route target must belong to final/general scenario")

    scenario_counts: dict[str, int] = {name: 0 for name in sorted(SCENARIOS)}
    for binding in bindings:
        scenario_counts[binding.scenario] += 1

    return {
        "version": 2,
        "targets": [targets[name].as_dict() for name in sorted(targets)],
        "bindings": [binding.as_dict() for binding in sorted(bindings, key=lambda item: (item.priority, item.source_id))],
        "scenario_counts": scenario_counts,
    }
