from __future__ import annotations

import sys
from pathlib import Path

import pytest

import clash_relay.mihomo as mihomo
from clash_relay.errors import ValidationError
from clash_relay.util import load_yaml_file


def test_run_executes_local_command_with_combined_output(tmp_path: Path) -> None:
    result = mihomo._run(
        [sys.executable, "-c", "print('mihomo-helper-ok')"],
        cwd=tmp_path,
        timeout=5,
    )

    assert result.returncode == 0
    assert "mihomo-helper-ok" in result.stdout


def test_run_wraps_missing_executable_as_validation_error(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="failed to execute Mihomo validation"):
        mihomo._run([str(tmp_path / "missing-binary")], cwd=tmp_path, timeout=1)


def test_validation_copy_rebinds_runtime_listeners_and_secret(tmp_path: Path) -> None:
    source = tmp_path / "candidate.yaml"
    source.write_text(
        "mixed-port: 7890\n"
        "external-controller: 0.0.0.0:9090\n"
        "secret: PRIVATE\n"
        "dns:\n"
        "  enable: true\n"
        "  listen: 0.0.0.0:1053\n"
        "proxies: []\n"
        "proxy-groups: []\n"
        "rules: []\n",
        encoding="utf-8",
    )
    workdir = tmp_path / "work"
    workdir.mkdir()

    target = mihomo._validation_copy(source, workdir)
    copied = load_yaml_file(target)

    assert copied["mixed-port"] != 7890
    assert str(copied["external-controller"]).startswith("127.0.0.1:")
    assert copied["secret"] == "clash-relay-validation-only"
    assert str(copied["dns"]["listen"]).startswith("127.0.0.1:")


def test_validation_copy_rejects_non_mapping_candidate(tmp_path: Path) -> None:
    source = tmp_path / "candidate.yaml"
    source.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="candidate is not a YAML mapping"):
        mihomo._validation_copy(source, tmp_path)


def test_validate_with_mihomo_rejects_missing_binary_before_subprocess(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxies: []\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="does not exist"):
        mihomo.validate_with_mihomo(tmp_path / "mihomo", candidate)


def test_validate_with_mihomo_rejects_non_executable_binary(tmp_path: Path) -> None:
    binary = tmp_path / "mihomo"
    binary.write_text("not executable\n", encoding="utf-8")
    binary.chmod(0o644)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxies: []\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="not executable"):
        mihomo.validate_with_mihomo(binary, candidate)


def test_load_candidate_accepts_mapping_and_rejects_sequence(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text("proxies: []\n", encoding="utf-8")
    sequence = tmp_path / "sequence.yaml"
    sequence.write_text("- one\n- two\n", encoding="utf-8")

    assert mihomo.load_candidate(mapping) == {"proxies": []}
    with pytest.raises(ValidationError, match="candidate must be a YAML mapping"):
        mihomo.load_candidate(sequence)
