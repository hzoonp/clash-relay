"""Compile declarative project and external routing rules into Mihomo rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import GenerationError
from .schema import load_and_validate
from .util import unique

_BUILTINS = frozenset({"DIRECT", "REJECT", "PASS", "COMPATIBLE"})


@dataclass(frozen=True, slots=True)
class RuleCompilation:
    rule_providers: dict[str, Any]
    rules: list[str]


class RuleCompiler:
    """Own rule loading, ordering, external binding, and final-target validation."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _render_rule(rule: dict[str, Any], target: str) -> str:
        parts = [str(rule["type"]), str(rule["value"]), target]
        parts.extend(str(option) for option in rule.get("options", []))
        return ",".join(parts)

    def _load_rules(self, relative: str) -> list[dict[str, Any]]:
        document = load_and_validate(self.root / relative, "rules.schema.json")
        return list(document["rules"])

    def compile(
        self,
        *,
        modules: dict[str, bool],
        policies: dict[str, Any],
        groups: list[dict[str, Any]],
        external_rule_providers: dict[str, Any] | None = None,
        external_rules: list[dict[str, Any]] | None = None,
        final_target: str | None = None,
    ) -> RuleCompilation:
        available_targets = _BUILTINS | {str(group["name"]) for group in groups}
        rule_rows: list[tuple[int, str, int, str]] = []
        for pool in policies["pools"]:
            if modules.get(pool["module"], False) and pool["rules"]:
                for order, rule in enumerate(self._load_rules(pool["rules"])):
                    rule_rows.append(
                        (
                            pool["rule_priority"],
                            f"pool:{pool['id']}",
                            order,
                            self._render_rule(rule, pool["display_name"]),
                        )
                    )

        rule_providers = dict(external_rule_providers or {})
        for item in external_rules or []:
            target = str(item["target"])
            if target not in available_targets:
                raise GenerationError(
                    f"external rule source {item['source_id']!r} targets unavailable group "
                    f"{target!r}"
                )
            if "provider" in item:
                provider_name = str(item["provider"])
                if provider_name not in rule_providers:
                    raise GenerationError(
                        f"external rule source {item['source_id']!r} references missing rule "
                        "provider"
                    )
                rendered = f"RULE-SET,{provider_name},{target}"
            else:
                rendered = self._render_rule(item["rule"], target)
            rule_rows.append(
                (
                    int(item["priority"]),
                    f"acl4ssr:{item['source_id']}",
                    int(item["order"]),
                    rendered,
                )
            )

        rendered_rules = [
            self._render_rule(rule, "DIRECT")
            for rule in self._load_rules("rules/direct.yaml")
        ]
        rendered_rules.extend(
            value for _, _, _, value in sorted(rule_rows, key=lambda item: item[:3])
        )
        resolved_final_target = final_target or (
            "Proxy" if modules.get("general", False) else "DIRECT"
        )
        if resolved_final_target not in available_targets:
            raise GenerationError(f"final routing target is unavailable: {resolved_final_target!r}")
        rendered_rules.append(f"MATCH,{resolved_final_target}")

        return RuleCompilation(
            rule_providers=rule_providers,
            rules=unique(rendered_rules),
        )
