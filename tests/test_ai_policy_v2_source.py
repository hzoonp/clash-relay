from __future__ import annotations

from pathlib import Path

import pytest

import clash_relay.ai_application as ai_application
from clash_relay.ai_qualification import load_ai_probe_specs
from clash_relay.errors import ConfigurationError
from clash_relay.policy_document import policy_fragment_path


class _ProbeSourceCaptured(RuntimeError):
    pass


def test_ai_application_resolves_probes_from_v2_scheduling_fragment(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[Path] = []

    def capture(source: Path):
        observed.append(source.resolve())
        raise _ProbeSourceCaptured

    monkeypatch.setattr(ai_application, "load_ai_probe_specs", capture)

    with pytest.raises(_ProbeSourceCaptured):
        ai_application.run_ai_qualification(
            candidate=tmp_path / "candidate.yaml",
            policies=repo_root / "policies.yaml",
            mihomo_bin=tmp_path / "mihomo",
        )

    assert observed == [(repo_root / "policies/scheduling.yaml").resolve()]


def test_canonical_v2_scheduling_fragment_contains_registered_ai_probes(repo_root: Path) -> None:
    source = policy_fragment_path(repo_root / "policies.yaml", "scheduling")
    probes = load_ai_probe_specs(source)

    assert tuple(str(probe["name"]) for probe in probes) == (
        "ai_openai",
        "ai_claude",
        "ai_gemini",
    )


def test_policy_fragment_resolution_does_not_accept_flat_legacy_policy(tmp_path: Path) -> None:
    legacy = tmp_path / "policies.yaml"
    legacy.write_text(
        "probes:\n  ai_openai:\n    url: https://chatgpt.com/\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Policy Model v2 manifest is required"):
        policy_fragment_path(legacy, "scheduling")
