from __future__ import annotations

import json
from pathlib import Path

import pytest

from clash_relay import __version__, cli
from clash_relay.errors import ValidationError


def _project_args(project_paths: dict[str, Path]) -> list[str]:
    return [
        "--config",
        str(project_paths["config_path"]),
        "--subscriptions",
        str(project_paths["subscriptions_path"]),
        "--policies",
        str(project_paths["policies_path"]),
    ]


def _inject(monkeypatch, fixture_env: dict[str, str]) -> None:
    for key, value in fixture_env.items():
        monkeypatch.setenv(key, value)


def test_validate_project_command(project_paths, capsys) -> None:
    result = cli.main(["validate-project", *_project_args(project_paths)])
    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"
    assert output["enabled_subscriptions"] == 3


def test_cloudflare_cli_uses_smoke_and_compensating_release_adapter(
    project_paths, monkeypatch, tmp_path, capsys
):
    calls = []

    def publish(**kwargs):
        calls.append(kwargs)
        return {"status": "unchanged", "final_link_smoke": {"status": "passed"}}

    monkeypatch.setattr(cli, "publish_production_release", publish)
    candidate, binary = tmp_path / "candidate.yaml", tmp_path / "mihomo"
    assert (
        cli.main(
            [
                "publish-cloudflare-kv",
                *_project_args(project_paths),
                "--candidate",
                str(candidate),
                "--mihomo-bin",
                str(binary),
                "--account-id",
                "account-override",
                "--namespace-title",
                "namespace-override",
                "--key",
                "custom-config",
            ]
        )
        == 0
    )
    assert calls[0]["mihomo_binary"] == binary
    assert calls[0]["env"]["CLOUDFLARE_ACCOUNT_ID"] == "account-override"
    assert calls[0]["env"]["CLOUDFLARE_KV_NAMESPACE_TITLE"] == "namespace-override"
    assert calls[0]["project"].config["publishing"]["cloudflare_kv"]["key"] == "custom-config"
    assert json.loads(capsys.readouterr().out)["status"] == "unchanged"


def test_generate_and_check_commands(
    project_paths, fixture_env, monkeypatch, tmp_path: Path, capsys
) -> None:
    _inject(monkeypatch, fixture_env)
    output = tmp_path / "config.yaml"
    report = tmp_path / "report.json"
    args = [
        "generate",
        *_project_args(project_paths),
        "--output",
        str(output),
        "--report",
        str(report),
    ]
    assert cli.main(args) == 0
    first_stdout = json.loads(capsys.readouterr().out)
    assert first_stdout["status"] == "generated"
    assert output.is_file()
    assert json.loads(report.read_text(encoding="utf-8"))["candidate_sha256"]
    assert cli.main([*args, "--check"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "unchanged"


def test_generate_check_detects_drift(
    project_paths, fixture_env, monkeypatch, tmp_path: Path, capsys
) -> None:
    _inject(monkeypatch, fixture_env)
    output = tmp_path / "config.yaml"
    base = ["generate", *_project_args(project_paths), "--output", str(output)]
    assert cli.main(base) == 0
    capsys.readouterr()
    output.write_text(output.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
    assert cli.main([*base, "--check"]) == 2
    assert "differs" in capsys.readouterr().err


def test_validate_existing_candidate(
    project_paths, fixture_env, monkeypatch, tmp_path: Path, capsys
) -> None:
    _inject(monkeypatch, fixture_env)
    output = tmp_path / "candidate.yaml"
    assert cli.main(["generate", *_project_args(project_paths), "--output", str(output)]) == 0
    capsys.readouterr()
    assert cli.main(["validate", "--candidate", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["static_validation"] == "passed"


def test_reconcile_release_command_is_read_only_adapter(
    project_paths, monkeypatch, tmp_path: Path, capsys
) -> None:
    candidate = tmp_path / "candidate.yaml"
    previous = tmp_path / "previous.yaml"
    candidate.write_text("candidate\n", encoding="utf-8")
    previous.write_text("previous\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_reconcile(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "committed", "requires_manual_action": False}

    monkeypatch.setattr(cli, "reconcile_production_release", fake_reconcile)

    assert (
        cli.main(
            [
                "reconcile-release",
                *_project_args(project_paths),
                "--candidate",
                str(candidate),
                "--previous",
                str(previous),
            ]
        )
        == 0
    )
    assert captured["candidate"] == candidate
    assert captured["previous"] == previous
    assert json.loads(capsys.readouterr().out)["status"] == "committed"


def test_release_retention_plan_command_is_read_only_adapter(
    project_paths, monkeypatch, capsys
) -> None:
    captured: dict[str, object] = {}

    def fake_plan(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "planned", "mutation": "none"}

    monkeypatch.setattr(cli, "plan_production_release_retention", fake_plan)

    assert (
        cli.main(
            [
                "plan-release-retention",
                *_project_args(project_paths),
                "--retention-days",
                "45",
            ]
        )
        == 0
    )
    assert captured["retention_days"] == 45
    assert json.loads(capsys.readouterr().out)["mutation"] == "none"


def test_release_retention_apply_requires_an_explicit_cli_confirmation(
    project_paths, monkeypatch, tmp_path: Path, capsys
) -> None:
    plan = tmp_path / "retention-plan.json"
    plan.write_text('{"status":"planned"}', encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_apply(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "deleted", "deleted_objects": 0}

    monkeypatch.setattr(cli, "apply_production_release_retention", fake_apply)
    assert (
        cli.main(
            [
                "apply-release-retention",
                *_project_args(project_paths),
                "--plan",
                str(plan),
                "--confirm-retention-delete",
            ]
        )
        == 0
    )
    assert captured["plan"] == {"status": "planned"}
    assert json.loads(capsys.readouterr().out)["status"] == "deleted"


def test_build_writes_only_after_real_core_validation(
    project_paths, fixture_env, monkeypatch, tmp_path: Path, capsys
) -> None:
    _inject(monkeypatch, fixture_env)
    monkeypatch.setattr(
        cli,
        "validate_with_mihomo",
        lambda *args, **kwargs: {"config_test": "passed", "startup_smoke": "passed"},
    )
    binary = tmp_path / "mihomo"
    binary.write_text("mock", encoding="utf-8")
    output = tmp_path / "production.yaml"
    result = cli.main(
        [
            "build",
            *_project_args(project_paths),
            "--mihomo-bin",
            str(binary),
            "--output",
            str(output),
        ]
    )
    assert result == 0
    assert output.is_file()
    assert json.loads(capsys.readouterr().out)["status"] == "built"


def test_build_failure_never_writes_output(
    project_paths, fixture_env, monkeypatch, tmp_path: Path, capsys
) -> None:
    _inject(monkeypatch, fixture_env)

    def reject(*args, **kwargs):
        raise ValidationError("core rejected candidate")

    monkeypatch.setattr(cli, "validate_with_mihomo", reject)
    binary = tmp_path / "mihomo"
    binary.write_text("mock", encoding="utf-8")
    output = tmp_path / "production.yaml"
    result = cli.main(
        [
            "build",
            *_project_args(project_paths),
            "--mihomo-bin",
            str(binary),
            "--output",
            str(output),
        ]
    )
    assert result == 2
    assert not output.exists()
    assert "core rejected" in capsys.readouterr().err


def test_artifact_publication_gate_command(project_paths, capsys) -> None:
    result = cli.main(
        [
            "publication-gate",
            *_project_args(project_paths),
            "--mode",
            "artifact",
        ]
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "artifact"


def test_release_publication_gate_is_closed_by_default(project_paths, capsys) -> None:
    result = cli.main(
        [
            "publication-gate",
            *_project_args(project_paths),
            "--mode",
            "github_release",
            "--acknowledgement",
            "I_UNDERSTAND_THIS_PUBLISHES_PROXY_CREDENTIALS",
        ]
    )
    assert result == 2
    assert "disabled" in capsys.readouterr().err


def test_missing_secret_returns_controlled_error(project_paths, monkeypatch, capsys) -> None:
    for name in ("SUB_PRIMARY", "SUB_SECONDARY", "SUB_SPECIAL", "CLASH_RELAY_SUBSCRIPTIONS"):
        monkeypatch.delenv(name, raising=False)
    result = cli.main(["generate", *_project_args(project_paths), "--output", "ignored.yaml"])
    assert result == 2
    assert "missing subscription URL secret" in capsys.readouterr().err


def test_version_option(capsys) -> None:
    with pytest.raises(SystemExit, match="0"):
        cli.main(["--version"])
    assert capsys.readouterr().out.strip() == __version__
