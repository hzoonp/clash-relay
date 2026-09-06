from __future__ import annotations

from pathlib import Path

import pytest

import clash_relay.ai_application as ai_application
from clash_relay.ai_application import (
    _cache_inputs,
    _empty_probe_summary,
    _filtered_candidate,
    _probe_names,
)
from clash_relay.errors import ValidationError
from clash_relay.util import dump_yaml, load_yaml_file


def test_cache_inputs_require_complete_path_triple(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="requires cache, cache_key, and next_cache"):
        _cache_inputs(
            cache=tmp_path / "cache.json",
            cache_key=None,
            next_cache=tmp_path / "next.json",
        )

    assert _cache_inputs(cache=None, cache_key=None, next_cache=None) is None


def test_cache_inputs_empty_key_disables_cache(tmp_path: Path) -> None:
    key = tmp_path / "cache.key"
    key.write_text("\n", encoding="ascii")

    assert (
        _cache_inputs(
            cache=tmp_path / "cache.json",
            cache_key=key,
            next_cache=tmp_path / "next.json",
        )
        is None
    )


def test_cache_inputs_reject_invalid_private_key(tmp_path: Path) -> None:
    key = tmp_path / "cache.key"
    key.write_text("invalid-hex\n", encoding="ascii")

    with pytest.raises(ValidationError, match="fingerprint key is invalid"):
        _cache_inputs(
            cache=tmp_path / "cache.json",
            cache_key=key,
            next_cache=tmp_path / "next.json",
        )


def test_cache_inputs_missing_state_degrades_to_empty_cache(tmp_path: Path) -> None:
    key = tmp_path / "cache.key"
    key.write_text("11" * 32 + "\n", encoding="ascii")

    result = _cache_inputs(
        cache=tmp_path / "missing-cache.json",
        cache_key=key,
        next_cache=tmp_path / "next.json",
    )

    assert result is not None
    document, fingerprint_key, status = result
    assert status == "missing"
    assert document["nodes"] == {}
    assert fingerprint_key == bytes.fromhex("11" * 32)


def test_filtered_candidate_keeps_only_requested_ai_nodes(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(
        dump_yaml(
            {
                "proxy-providers": {
                    "cr_ai_us": {
                        "type": "inline",
                        "payload": [
                            {"name": "keep", "server": "198.51.100.10"},
                            {"name": "drop", "server": "198.51.100.11"},
                        ],
                    },
                    "cr_general_any": {
                        "type": "inline",
                        "payload": [{"name": "general", "server": "198.51.100.20"}],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    filtered = _filtered_candidate(candidate, {"keep"})
    try:
        document = load_yaml_file(filtered)
        assert document["proxy-providers"]["cr_ai_us"]["payload"] == [
            {"name": "keep", "server": "198.51.100.10"}
        ]
        assert document["proxy-providers"]["cr_general_any"]["payload"] == [
            {"name": "general", "server": "198.51.100.20"}
        ]
    finally:
        filtered.unlink(missing_ok=True)


def test_filtered_candidate_fails_closed_on_invalid_shapes(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("- not-a-mapping\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="candidate is not a YAML mapping"):
        _filtered_candidate(candidate, {"node"})

    candidate.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="proxy-providers must be a mapping"):
        _filtered_candidate(candidate, {"node"})

    candidate.write_text(
        dump_yaml({"proxy-providers": {"cr_ai_us": {"payload": "invalid"}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="provider payload is invalid"):
        _filtered_candidate(candidate, {"node"})


def test_empty_probe_summary_is_aggregate_only() -> None:
    assert _empty_probe_summary(
        {"method": "GET", "expected_status": "200", "url": "https://secret.invalid"}
    ) == {
        "method": "GET",
        "expected_status": "200",
        "passed": 0,
        "failed": 0,
        "outcomes": {},
    }


def test_probe_names_skips_probe_when_live_set_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_probe(*args: object, **kwargs: object) -> set[str]:
        raise AssertionError("probe_ai_nodes must not run for an empty live set")

    monkeypatch.setattr(ai_application, "probe_ai_nodes", fail_probe)

    qualified, diagnostics = _probe_names(
        binary=tmp_path / "mihomo",
        candidate=tmp_path / "candidate.yaml",
        names=set(),
        probes=({"name": "ai_openai"},),
        workers=1,
    )

    assert qualified == set()
    assert diagnostics == {}


def test_probe_names_removes_temporary_filtered_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    temporary = tmp_path / "temporary.yaml"
    temporary.write_text("proxy-providers: {}\n", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_filtered(candidate: Path, names: set[str]) -> Path:
        seen["candidate"] = candidate
        seen["names"] = set(names)
        return temporary

    def fake_probe(
        binary: Path,
        candidate: Path,
        probes: tuple[dict[str, object], ...],
        *,
        workers: int,
        diagnostics: dict[str, object],
    ) -> set[str]:
        seen["binary"] = binary
        seen["target"] = candidate
        seen["workers"] = workers
        diagnostics["tested_nodes"] = 1
        return {"node-a"}

    monkeypatch.setattr(ai_application, "_filtered_candidate", fake_filtered)
    monkeypatch.setattr(ai_application, "probe_ai_nodes", fake_probe)

    qualified, diagnostics = _probe_names(
        binary=tmp_path / "mihomo",
        candidate=tmp_path / "candidate.yaml",
        names={"node-a"},
        probes=({"name": "ai_openai"},),
        workers=3,
    )

    assert qualified == {"node-a"}
    assert diagnostics == {"tested_nodes": 1}
    assert seen["target"] == temporary
    assert seen["names"] == {"node-a"}
    assert seen["workers"] == 3
    assert not temporary.exists()
