from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


def _load_audit(repo_root: Path):
    path = repo_root / "scripts" / "audit_v2_release_contract.py"
    spec = importlib.util.spec_from_file_location("audit_v2_release_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v2_release_audit_passes_repository_tree(repo_root: Path) -> None:
    audit = _load_audit(repo_root)
    assert audit.audit(repo_root) == []


def test_public_v2_source_boundary_is_frozen(repo_root: Path) -> None:
    document = yaml.safe_load((repo_root / "subscriptions.yaml").read_text(encoding="utf-8"))
    assert document["version"] == 2
    first = next(entry for entry in document["subscriptions"] if entry["id"] == "subscription_1")
    assert first["allowed_uses"] == ["browsing", "ai"]
    assert first["max_node_multiplier"] == 2.0
    assert first["ingest_order"] == 100
    assert "priority" not in first


def test_v2_runtime_has_no_legacy_previous_release_fallback(repo_root: Path) -> None:
    text = (repo_root / "src" / "clash_relay" / "release_bundle.py").read_text(encoding="utf-8")
    assert "legacy_previous" not in text
    assert "legacy-previous-v1" not in text
    assert 'f"{production_key}.previous-v1"' not in text


def test_phase_only_documents_are_not_release_assets(repo_root: Path) -> None:
    audit = _load_audit(repo_root)
    assert all(not (repo_root / relative).exists() for relative in audit.STALE_PHASE_DOCS)
