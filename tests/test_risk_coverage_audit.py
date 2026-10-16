from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_audit(repo_root: Path) -> ModuleType:
    path = repo_root / "scripts" / "audit_risk_coverage.py"
    spec = importlib.util.spec_from_file_location("test_risk_coverage_audit", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _class_element(filename: str, line_rate: str) -> str:
    return (
        f'        <class name="{Path(filename).name}" filename="{filename}"'
        f' complexity="0" line-rate="{line_rate}" branch-rate="0.5">\n'
        "          <methods/>\n"
        "          <lines/>\n"
        "        </class>\n"
    )


def _report(
    module: ModuleType,
    line_rate: str = "1.0",
    *,
    summary_line_rate: str = "1.0",
    extra_classes: str = "",
    class_rates: dict[str, str] | None = None,
) -> str:
    rates = class_rates or {}
    classes = "".join(
        _class_element(name, rates.get(name, line_rate)) for name in module.RISK_COVERAGE_FLOORS
    )
    return (
        '<?xml version="1.0" ?>\n'
        f'<coverage version="7.6.1" line-rate="{summary_line_rate}" branch-rate="0.5"'
        ' lines-valid="10" lines-covered="9" branches-valid="2" branches-covered="1"'
        f' complexity="0" timestamp="1730000000" lines="10">\n'
        "  <sources>\n    <source>.</source>\n  </sources>\n"
        "  <packages>\n"
        '    <package name="src.clash_relay" line-rate="0.9" branch-rate="0.5" complexity="0">\n'
        "      <classes>\n"
        f"{classes}{extra_classes}"
        "      </classes>\n"
        "    </package>\n"
        "  </packages>\n"
        "</coverage>\n"
    )


def _written(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "coverage.xml"
    path.write_text(text, encoding="utf-8")
    return path


def test_all_floor_ratios_present_passes(repo_root: Path, tmp_path: Path) -> None:
    module = _load_audit(repo_root)
    path = _written(tmp_path, _report(module))

    assert module.audit(path) == []


@pytest.mark.parametrize(
    "raw",
    [
        "NaN",
        "nan",
        "-NaN",
        "inf",
        "Infinity",
        "-inf",
        "1e309",
        "-1e309",
        "-1",
        "-0.25",
        "1.0000001",
        "2",
        "",
    ],
)
def test_line_rate_outside_the_unit_interval_fails_the_audit(
    repo_root: Path, tmp_path: Path, raw: str
) -> None:
    module = _load_audit(repo_root)
    path = _written(tmp_path, _report(module, raw))

    with pytest.raises(ValueError, match="line-rate"):
        module.audit(path)


@pytest.mark.parametrize("raw", ["ninety-four", "94%", "1,0"])
def test_non_numeric_line_rate_fails_the_audit(repo_root: Path, tmp_path: Path, raw: str) -> None:
    module = _load_audit(repo_root)
    path = _written(tmp_path, _report(module, raw))

    with pytest.raises(ValueError, match="line-rate"):
        module.audit(path)


def test_corrupt_summary_ratio_fails_the_audit(repo_root: Path, tmp_path: Path) -> None:
    module = _load_audit(repo_root)
    path = _written(tmp_path, _report(module, summary_line_rate="NaN"))

    with pytest.raises(ValueError, match="line-rate"):
        module.audit(path)


def test_corrupt_ratio_outside_the_ratchet_fails_the_audit(repo_root: Path, tmp_path: Path) -> None:
    module = _load_audit(repo_root)
    unrelated = _class_element("src/clash_relay/unratcheted.py", "NaN")
    path = _written(tmp_path, _report(module, extra_classes=unrelated))

    with pytest.raises(ValueError, match="line-rate"):
        module.audit(path)


def test_missing_report_fails_the_audit(repo_root: Path, tmp_path: Path) -> None:
    module = _load_audit(repo_root)

    with pytest.raises(ValueError, match="failed to read coverage XML"):
        module.audit(tmp_path / "absent.xml")


def test_ratio_equal_to_the_floor_passes(repo_root: Path, tmp_path: Path) -> None:
    module = _load_audit(repo_root)
    rates = {name: repr(floor / 100.0) for name, floor in module.RISK_COVERAGE_FLOORS.items()}
    path = _written(tmp_path, _report(module, class_rates=rates))

    assert module.audit(path) == []


def test_ratio_below_the_floor_still_fails(repo_root: Path, tmp_path: Path) -> None:
    module = _load_audit(repo_root)
    path = _written(
        tmp_path, _report(module, class_rates={"src/clash_relay/release_bundle.py": "0.949"})
    )

    failures = module.audit(path)

    assert failures == ["src/clash_relay/release_bundle.py: 94.90% < required 95.00%"]


def test_cli_exit_codes_distinguish_corrupt_from_regressed_reports(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_audit(repo_root)
    healthy = _written(tmp_path, _report(module))

    monkeypatch.setattr(sys, "argv", ["audit_risk_coverage.py", str(healthy)])
    assert module.main() == 0
    assert "risk-based coverage ratchet: passed" in capsys.readouterr().out

    corrupt = tmp_path / "corrupt.xml"
    corrupt.write_text(_report(module, "NaN"), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["audit_risk_coverage.py", str(corrupt)])
    assert module.main() == 2
    assert "risk coverage audit" in capsys.readouterr().err

    regressed = tmp_path / "regressed.xml"
    regressed.write_text(
        _report(module, class_rates={"src/clash_relay/release_bundle.py": "0.10"}), encoding="utf-8"
    )
    monkeypatch.setattr(sys, "argv", ["audit_risk_coverage.py", str(regressed)])
    assert module.main() == 1
    assert "risk coverage regression" in capsys.readouterr().err
