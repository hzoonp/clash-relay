from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
README_ZH = ROOT / "README.zh-CN.md"
QUICKSTART = ROOT / "docs" / "quickstart.md"
QUICKSTART_ZH = ROOT / "docs" / "quickstart.zh-CN.md"


def _docs() -> tuple[str, str]:
    return (
        QUICKSTART.read_text(encoding="utf-8"),
        QUICKSTART_ZH.read_text(encoding="utf-8"),
    )


def test_readmes_surface_bilingual_fork_quickstart() -> None:
    english = README.read_text(encoding="utf-8")
    chinese = README_ZH.read_text(encoding="utf-8")

    assert "[Fork quickstart](docs/quickstart.md)" in english
    assert "[Fork 快速上手](docs/quickstart.zh-CN.md)" in chinese
    assert "publish=false" in english
    assert "publish=false" in chinese
    assert "validated rollback" in english
    assert "回滚" in chinese


def test_quickstart_names_every_required_secret_and_variable() -> None:
    for document in _docs():
        assert "CLASH_RELAY_SUBSCRIPTIONS" in document
        assert "CLOUDFLARE_API_TOKEN" in document
        assert "CLOUDFLARE_ACCOUNT_ID" in document
        assert "CLOUDFLARE_KV_NAMESPACE_TITLE" in document


def test_quickstart_locks_dry_run_browsing_history_ai_and_rollback_semantics() -> None:
    for document in _docs():
        assert "publish = false" in document
        assert "publish = true" in document
        assert "3/3" in document
        assert "2/3" in document
        assert "HMAC-SHA256" in document
        assert "OpenAI" in document
        assert "Claude" in document
        assert "Gemini" in document
        assert "Roll back production config" in document
        assert "confirm = true" in document
        assert "tools/mihomo-versions.json" in document
        assert "previous-release-v1" in document
        assert "current-policy" in document


def test_quickstart_examples_never_embed_real_subscription_urls() -> None:
    for document in _docs():
        urls = re.findall(r"""https://[^\s"'`<>]+""", document)
        assert urls
        assert all("example.invalid/" in url for url in urls)


def test_quickstart_warns_against_public_credential_storage() -> None:
    english, chinese = _docs()
    assert "must never be committed" in english
    assert "never committed or uploaded as an Artifact/Release/Gist" in english
    assert "不能提交进仓库" in chinese
    assert "从不 commit" in chinese
    assert "Artifact / Release / Gist" in chinese
