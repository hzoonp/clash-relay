from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

AI_COUNTRY_GROUPS = [
    "AI · 新加坡",
    "AI · 日本",
    "AI · 美国",
    "AI · 香港",
    "AI · 台湾",
    "AI · 韩国",
    "AI · 其他地区",
]


def test_repository_audit_passes(repo_root: Path) -> None:
    result = subprocess.run(
        ["python", "scripts/repository_audit.py"],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert result.returncode == 0, result.stdout


def test_secret_and_generated_paths_are_ignored(repo_root: Path) -> None:
    ignored = [
        ".env",
        ".env.production",
        ".secrets/subscriptions.yaml",
        "secrets.yaml",
        "subscriptions.secret.json",
        "dist/config.yaml",
        ".work/candidate.yaml",
        "providers/cache.yaml",
    ]
    for value in ignored:
        result = subprocess.run(["git", "check-ignore", "-q", value], cwd=repo_root, check=False)
        assert result.returncode == 0, value


def test_public_per_fork_declarations_are_not_ignored(repo_root: Path) -> None:
    for value in ["config.yaml", "subscriptions.yaml"]:
        result = subprocess.run(["git", "check-ignore", "-q", value], cwd=repo_root, check=False)
        assert result.returncode == 1, value


def test_public_production_skips_individual_invalid_proxy_entries(repo_root: Path) -> None:
    for filename in ("config.yaml", "config.example.yaml"):
        document = yaml.safe_load((repo_root / filename).read_text(encoding="utf-8"))
        assert document["generation"]["invalid_proxy_policy"] == "skip"


def test_public_production_ai_candidates_are_country_scoped_and_live_gated(
    repo_root: Path,
) -> None:
    policies = yaml.safe_load((repo_root / "policies.yaml").read_text(encoding="utf-8"))
    subscriptions = yaml.safe_load((repo_root / "subscriptions.yaml").read_text(encoding="utf-8"))
    acl = yaml.safe_load((repo_root / "rules/acl4ssr.yaml").read_text(encoding="utf-8"))

    assert set(policies["country_classification"]["aliases"]) >= {
        "HK",
        "TW",
        "SG",
        "JP",
        "US",
        "KR",
    }
    for subscription in subscriptions["subscriptions"]:
        assert "ai" in subscription["allowed_uses"]
        assert "*" in subscription["allowed_countries"]

    pools = {item["display_name"]: item for item in policies["pools"]}
    for group_name in AI_COUNTRY_GROUPS:
        pool = pools[group_name]
        assert pool["source_use"] == "ai"
        assert pool["on_empty"] == "reject"
        assert pool["probe"] == "connectivity"

    for probe_name in ("ai_openai", "ai_claude", "ai_gemini"):
        probe = policies["probes"][probe_name]
        assert probe["url"].startswith("https://")
        assert probe["expected_status"] == "200-399"

    ai_group = next(item for item in acl["groups"] if item["display_name"] == "人工智能")
    members = [item.get("group", item.get("builtin")) for item in ai_group["members"]]
    assert members == [*AI_COUNTRY_GROUPS, "DIRECT"]


def test_lock_files_pin_every_dependency(repo_root: Path) -> None:
    for filename in ["requirements.lock", "requirements-dev.lock"]:
        for raw in (repo_root / filename).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("-r "):
                continue
            assert "==" in line, f"unlocked dependency in {filename}: {line}"


def test_workflows_parse_as_yaml(repo_root: Path) -> None:
    for path in (repo_root / ".github/workflows").glob("*.yml"):
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        assert isinstance(document, dict)
        assert "jobs" in document


def test_stable_workflows_have_no_always_publication_path(repo_root: Path) -> None:
    ci = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "always()" not in ci
    assert "continue-on-error" not in ci

    publish = (repo_root / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    assert "continue-on-error" not in publish
    assert publish.count("always()") == 1
    assert (
        "- name: Remove private candidate\n"
        "        if: always()\n"
        "        run: rm -rf .work/private"
    ) in publish

    publication_start = publish.index("- name: Publish exact validated bytes to Cloudflare KV")
    publication_end = publish.index("- name: Record publication result")
    publication_block = publish[publication_start:publication_end]
    assert "always()" not in publication_block

    assert ".work/private/config.yaml" in publish
    assert "python scripts/qualify_ai.py" in publish
    assert "validate_core v1.19.30" in publish
    assert "validate_core v1.19.29" in publish
    assert "clash-relay publish-cloudflare-kv" in publish
    assert publish.index("python scripts/qualify_ai.py") < publish.index("validate_core v1.19.30")
    assert publish.index("python scripts/qualify_ai.py") < publish.index("validate_core v1.19.29")
    assert publish.index("validate_core v1.19.30") < publish.index(
        "clash-relay publish-cloudflare-kv"
    )
    assert publish.index("validate_core v1.19.29") < publish.index(
        "clash-relay publish-cloudflare-kv"
    )


def test_public_production_has_no_sensitive_github_publisher(repo_root: Path) -> None:
    publish = (repo_root / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    assert "github.ref == 'refs/heads/main'" in publish
    assert "--mode cloudflare_kv" in publish
    assert "CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}" in publish
    assert "CLOUDFLARE_ACCOUNT_ID: ${{ vars.CLOUDFLARE_ACCOUNT_ID }}" in publish
    assert "CLOUDFLARE_KV_NAMESPACE_TITLE: ${{ vars.CLOUDFLARE_KV_NAMESPACE_TITLE }}" in publish
    assert "actions/upload-artifact" not in publish
    assert "actions/download-artifact" not in publish
    assert "gh release" not in publish
    assert "publish-gist" not in publish
    assert "PUBLISH_PUBLIC_RELEASE" not in publish
    assert "PUBLISH_UNLISTED_GIST" not in publish
