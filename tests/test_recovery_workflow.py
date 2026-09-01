from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLLBACK = ROOT / ".github" / "workflows" / "rollback.yml"


def test_rollback_is_manual_confirmed_and_main_only() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    assert "  workflow_dispatch:\n" in text
    assert "  push:\n" not in text
    assert "      confirm:\n" in text
    assert "        default: false\n" in text
    assert "github.ref == 'refs/heads/main' && inputs.confirm == true" in text
    assert "clash-relay-publish-${{ github.ref }}" in text


def test_rollback_fetches_private_versioned_previous_release_without_subscription_secrets() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    assert "python scripts/fetch_previous_config.py" in text
    assert "--output .work/private/rollback.yaml" in text
    assert "CLASH_RELAY_SUBSCRIPTIONS" not in text
    assert "CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}" in text
    assert "actions/upload-artifact" not in text
    assert "actions/download-artifact" not in text
    assert "gh release" not in text
    assert "publish-gist" not in text


def test_rollback_runs_current_policy_audit_before_core_validation_and_activation() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    fetch = text.index("Fetch private previous release")
    audit = text.index("Audit previous release against current production policy")
    validate = text.index("Validate previous release with pinned stable Mihomo matrix")
    activate = text.index("Activate audited validated previous release")
    assert fetch < audit < validate < activate
    audit_block = text[audit:validate]
    assert "python scripts/audit_production.py" in audit_block
    assert "--candidate .work/private/rollback.yaml" in audit_block


def test_rollback_uses_manifest_matrix_and_versioned_release_transaction() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    validate = text.index("Validate previous release with pinned stable Mihomo matrix")
    activate = text.index("Activate audited validated previous release")
    assert "python scripts/validate_mihomo_matrix.py" in text
    assert "--manifest tools/mihomo-versions.json" in text
    assert "v1.19.30" not in text
    assert "v1.19.29" not in text
    assert "python scripts/publish_release_bundle.py" in text[activate:]
    assert "clash-relay publish-cloudflare-kv" not in text
    assert validate < activate


def test_rollback_only_always_runs_private_cleanup() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    assert "continue-on-error" not in text
    assert text.count("always()") == 1
    assert (
        "- name: Remove private rollback candidate\n        if: always()\n        run: rm -rf .work/private"
    ) in text
    activation = text[
        text.index("Activate audited validated previous release") : text.index(
            "Remove private rollback candidate"
        )
    ]
    assert "always()" not in activation
