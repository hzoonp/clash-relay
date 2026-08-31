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


def test_rollback_fetches_private_previous_bytes_without_subscription_secrets() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    assert "python scripts/fetch_previous_config.py" in text
    assert "--output .work/private/rollback.yaml" in text
    assert "CLASH_RELAY_SUBSCRIPTIONS" not in text
    assert "CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}" in text
    assert "actions/upload-artifact" not in text
    assert "actions/download-artifact" not in text
    assert "gh release" not in text
    assert "publish-gist" not in text


def test_rollback_revalidates_previous_bytes_with_both_cores_before_activation() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    fetch = text.index("Fetch private previous config")
    validate = text.index("Validate previous config with both pinned Mihomo cores")
    activate = text.index("Activate validated previous config")
    assert fetch < validate < activate
    assert "validate_core v1.19.30" in text
    assert "validate_core v1.19.29" in text
    assert text.index("validate_core v1.19.30") < activate
    assert text.index("validate_core v1.19.29") < activate
    assert "--candidate .work/private/rollback.yaml" in text
    activation_block = text[activate : text.index("Record rollback result")]
    assert "clash-relay publish-cloudflare-kv" in activation_block


def test_rollback_only_always_runs_private_cleanup() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    assert "continue-on-error" not in text
    assert text.count("always()") == 1
    assert (
        "- name: Remove private rollback candidate\n        if: always()\n        run: rm -rf .work/private"
    ) in text
    activation = text[
        text.index("Activate validated previous config") : text.index(
            "Remove private rollback candidate"
        )
    ]
    assert "always()" not in activation
