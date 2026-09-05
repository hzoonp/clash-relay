from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from clash_relay.policy_document import load_policy_document

AI_COUNTRY_GROUPS = [
    "AI · 美国",
    "AI · 新加坡",
    "AI · 日本",
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
    policies = load_policy_document(repo_root / "policies.yaml").document
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
    assert "AI · 香港" not in pools
    for group_name in AI_COUNTRY_GROUPS:
        pool = pools[group_name]
        assert pool["source_use"] == "ai"
        assert pool["on_empty"] == "reject"
        assert pool["probe"] == "connectivity"
        assert "HK" not in pool["regions"]

    for probe_name in ("ai_openai", "ai_claude", "ai_gemini"):
        probe = policies["probes"][probe_name]
        assert probe["url"].startswith("https://")
        assert probe["expected_status"] == "200-399"

    ai_group = next(item for item in acl["groups"] if item["display_name"] == "人工智能")
    members = [item.get("group", item.get("builtin")) for item in ai_group["members"]]
    assert members == [*AI_COUNTRY_GROUPS, "DIRECT"]
    assert "AI · 香港" not in members


def _logical_requirements(path: Path) -> list[str]:
    logical: list[str] = []
    current: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        current.append(line.removesuffix("\\").strip())
        if not line.endswith("\\"):
            logical.append(" ".join(current))
            current = []
    assert not current
    return logical


def test_lock_files_pin_and_hash_every_external_dependency(repo_root: Path) -> None:
    for filename in ["requirements.lock", "requirements-dev.lock"]:
        for requirement in _logical_requirements(repo_root / filename):
            if requirement.startswith("-r "):
                continue
            assert "==" in requirement.split()[0], (
                f"unlocked dependency in {filename}: {requirement}"
            )
            assert "--hash=sha256:" in requirement, (
                f"unhashed dependency in {filename}: {requirement}"
            )


def test_workflows_parse_as_yaml(repo_root: Path) -> None:
    for path in (repo_root / ".github/workflows").glob("*.yml"):
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        assert isinstance(document, dict)
        assert "jobs" in document


def test_stable_workflows_keep_production_fail_closed_and_limit_best_effort_state(
    repo_root: Path,
) -> None:
    ci = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    validate = (repo_root / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")
    assert "always()" not in ci
    assert "continue-on-error" not in ci
    assert "uses: ./.github/workflows/validate.yml" in ci
    assert "--require-hashes" in validate
    assert "--cov-fail-under=69" in validate
    assert "mypy --follow-imports=skip" in validate

    publish = (repo_root / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    lifecycle = (repo_root / "src" / "clash_relay" / "production_lifecycle.py").read_text(
        encoding="utf-8"
    )

    assert "continue-on-error" not in publish
    assert "always()" not in publish
    assert publish.count("python scripts/run_production_release.py") == 1
    assert "uses: ./.github/workflows/validate.yml" in publish
    assert "needs.validate.outputs.validated_sha == github.sha" in publish
    assert "python scripts/run_production_pipeline.py" not in publish
    assert "python scripts/check_promotion_guard.py" not in publish
    assert "python scripts/validate_mihomo_matrix.py" not in publish
    assert "python scripts/publish_release_bundle.py" not in publish
    assert "python scripts/qualify_candidate.py" not in publish
    assert "python scripts/qualify_ai.py" not in publish
    assert "python - <<" not in publish
    assert "v1.19.30" not in publish
    assert "v1.19.29" not in publish

    qualify = lifecycle.index("pipeline = self._qualify(binary)")
    guard = lifecycle.index("promotion = self._promotion_guard(project)")
    matrix = lifecycle.index("matrix = self._validate_matrix(binary)")
    release = lifecycle.index("release = self._publish_release(project)")
    persist = lifecycle.index("derived_state = self._persist_derived_state(project)")
    assert qualify < guard < matrix < release < persist
    assert lifecycle.count("self._best_effort_state(") == 3
    assert "persist_ai_qualification_cache" in lifecycle
    assert "persist_scheduler_history" in lifecycle
    assert "persist_production_metrics" in lifecycle
    assert "check_promotion_guard.py" not in lifecycle
    assert "validate_mihomo_matrix.py" not in lifecycle
    assert "publish_release_bundle.py" not in lifecycle
    assert "finally:" in lifecycle
    assert "shutil.rmtree(self.paths.private_dir" in lifecycle


def test_public_production_has_no_sensitive_github_publisher(repo_root: Path) -> None:
    publish = (repo_root / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    lifecycle = (repo_root / "src" / "clash_relay" / "production_lifecycle.py").read_text(
        encoding="utf-8"
    )
    assert "github.ref == 'refs/heads/main'" in publish
    assert 'publication_gate(project.config, "cloudflare_kv")' in lifecycle
    assert "CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}" in publish
    assert "CLOUDFLARE_ACCOUNT_ID: ${{ vars.CLOUDFLARE_ACCOUNT_ID }}" in publish
    assert "CLOUDFLARE_KV_NAMESPACE_TITLE: ${{ vars.CLOUDFLARE_KV_NAMESPACE_TITLE }}" in publish
    assert "actions/upload-artifact" not in publish
    assert "actions/download-artifact" not in publish
    assert "gh release" not in publish
    assert "publish-gist" not in publish
    assert "PUBLISH_PUBLIC_RELEASE" not in publish
    assert "PUBLISH_UNLISTED_GIST" not in publish
