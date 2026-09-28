from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-inventory-audit.yml"


def test_release_inventory_workflow_is_read_only_main_only_and_serialized() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    document = yaml.load(text, Loader=yaml.BaseLoader)

    assert isinstance(document, dict)
    assert "workflow_dispatch" in document["on"]
    assert document["permissions"] == {"contents": "read"}
    assert "clash-relay-production-commit-${{ github.ref }}" in text
    assert "if: github.ref == 'refs/heads/main'" in text
    assert "run: clash-relay audit-release-inventory" in text
    assert "run: clash-relay audit-release-state" in text
    assert "CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}" in text
    assert "CLOUDFLARE_ACCOUNT_ID: ${{ vars.CLOUDFLARE_ACCOUNT_ID }}" in text
    assert "CLOUDFLARE_KV_NAMESPACE_TITLE: ${{ vars.CLOUDFLARE_KV_NAMESPACE_TITLE }}" in text
    assert "publish-release" not in text
    assert "apply-release-retention" not in text
    assert "workflow_run:" not in text
