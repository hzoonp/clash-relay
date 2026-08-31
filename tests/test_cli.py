from __future__ import annotations

import json

import pytest

from clash_relay import cli


def _project_args(paths) -> list[str]:
    return [
        "--config",
        str(paths["config_path"]),
        "--subscriptions",
        str(paths["subscriptions_path"]),
        "--services",
        str(paths["services_path"]),
        "--policies",
        str(paths["policies_path"]),
    ]


def test_generate_command(project_paths, fixture_env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "build_candidate", lambda **_kwargs: object())
    monkeypatch.setattr(cli, "write_build", lambda *_args, **_kwargs: None)
    result = cli.main(["generate", *_project_args(project_paths), "--output", "ignored.yaml"])
    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "generated"


def test_generate_check_command(project_paths, fixture_env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "build_candidate", lambda **_kwargs: object())
    monkeypatch.setattr(cli, "write_build", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "check_existing", lambda *_args, **_kwargs: True)
    result = cli.main(
        ["generate", *_project_args(project_paths), "--output", "ignored.yaml", "--check"]
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "unchanged"


def test_validate_command(project_paths, monkeypatch, tmp_path, capsys) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxy-groups: []\nrules: []\n", encoding="utf-8")
    monkeypatch.setattr(cli, "validate_candidate_file", lambda *_args, **_kwargs: {"status": "ok"})
    result = cli.main(
        [
            "validate",
            *_project_args(project_paths),
            "--candidate",
            str(candidate),
            "--mihomo-bin",
            "mihomo",
        ]
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_build_command(project_paths, fixture_env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "build_candidate", lambda **_kwargs: object())
    monkeypatch.setattr(cli, "validate_candidate", lambda *_args, **_kwargs: {"status": "ok"})
    monkeypatch.setattr(cli, "write_build", lambda *_args, **_kwargs: None)
    result = cli.main(
        [
            "build",
            *_project_args(project_paths),
            "--output",
            "ignored.yaml",
            "--mihomo-bin",
            "mihomo",
        ]
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "validated"


def test_publication_gate_command(project_paths, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "evaluate_publication_gate", lambda *_args, **_kwargs: {"status": "allowed"})
    result = cli.main(["publication-gate", *_project_args(project_paths), "--mode", "cloudflare_kv"])
    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "allowed"


def test_publish_cloudflare_kv_command(project_paths, monkeypatch, tmp_path, capsys) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxy-groups: []\nrules: []\n", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "publish_candidate_to_cloudflare_kv",
        lambda *_args, **_kwargs: {"status": "published"},
    )
    result = cli.main(
        ["publish-cloudflare-kv", *_project_args(project_paths), "--candidate", str(candidate)]
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "published"


def test_publication_gate_rejection_returns_controlled_error(
    project_paths, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        cli,
        "evaluate_publication_gate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("disabled")),
    )
    result = cli.main(["publication-gate", *_project_args(project_paths), "--mode", "github_release"])
    assert result == 2
    assert "disabled" in capsys.readouterr().err


def test_publish_cloudflare_kv_requires_confirmation(
    project_paths, monkeypatch, tmp_path, capsys
) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxy-groups: []\nrules: []\n", encoding="utf-8")
    result = cli.main(
        [
            "publish-cloudflare-kv",
            *_project_args(project_paths),
            "--candidate",
            str(candidate),
            "--confirmation",
            "NOT_CONFIRMED",
        ]
    )
    assert result == 2
    assert "confirmation" in capsys.readouterr().err.lower()


def test_publish_cloudflare_kv_rejects_disabled_publication_mode(
    project_paths, monkeypatch, tmp_path, capsys
) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxy-groups: []\nrules: []\n", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "publish_candidate_to_cloudflare_kv",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("disabled")),
    )
    result = cli.main(
        [
            "publish-cloudflare-kv",
            *_project_args(project_paths),
            "--candidate",
            str(candidate),
            "--confirmation",
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
    assert "1.2.0" in capsys.readouterr().out
