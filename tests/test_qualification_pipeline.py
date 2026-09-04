from __future__ import annotations

import sys
from pathlib import Path

import pytest

from clash_relay.errors import ValidationError
from clash_relay.qualification_pipeline import run_qualification_pipeline


def _fake_stage(path: Path, marker: str, payload: str) -> None:
    path.write_text(
        "from __future__ import annotations\n"
        "import argparse, json\n"
        "from pathlib import Path\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('--candidate', required=True)\n"
        "args,_=p.parse_known_args()\n"
        f"with Path(args.candidate).open('a', encoding='utf-8') as h: h.write('\\n{marker}: true\\n')\n"
        f"print(json.dumps({payload}))\n",
        encoding="utf-8",
    )


def _pipeline_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxy-groups: []\nproxy-providers: {}\nproxies: []\n", encoding="utf-8")
    policies = Path(__file__).resolve().parent / "fixtures/project/policies.yaml"
    mihomo = tmp_path / "mihomo"
    mihomo.write_text("fake", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    return candidate, policies, mihomo, scripts


def _fake_success_tail(scripts: Path) -> None:
    _fake_stage(
        scripts / "qualify_ai.py",
        "ai_stage",
        "{'status':'qualified','diagnostics':{'qualification_mode':'per-service'}}",
    )
    _fake_stage(
        scripts / "harden_openai_runtime.py",
        "openai_runtime_stage",
        "{'status':'passed','selection':'stable_first_fallback','runtime_regions':2}",
    )


def test_pipeline_uses_private_sequential_stage_files(tmp_path: Path) -> None:
    candidate, policies, mihomo, scripts = _pipeline_inputs(tmp_path)
    _fake_stage(
        scripts / "qualify_browsing.py",
        "browsing_stage",
        "{'status':'qualified','automatic_nodes':3}",
    )
    _fake_success_tail(scripts)

    output = tmp_path / "final.yaml"
    browsing_report = tmp_path / "browsing.json"
    ai_report = tmp_path / "ai.json"
    result = run_qualification_pipeline(
        candidate=candidate,
        output=output,
        policies=policies,
        mihomo_bin=mihomo,
        stage_dir=tmp_path / "stages",
        browsing_report=browsing_report,
        ai_report=ai_report,
        script_dir=scripts,
        python_executable=sys.executable,
    )

    text = output.read_text(encoding="utf-8")
    assert "browsing_stage: true" in text
    assert "ai_stage: true" in text
    assert "openai_runtime_stage: true" in text
    assert "browsing_stage" not in candidate.read_text(encoding="utf-8")
    assert result["status"] == "qualified"
    assert result["policy_model_version"] == 2
    assert result["browsing"]["stage_attempts"] == 1
    assert result["browsing"]["recovered_by_retry"] is False
    assert result["browsing"]["recovered_failure_category"] is None
    assert [row["name"] for row in result["stages"]] == [
        "generated",
        "browsing_transport_qualified",
        "ai_qualified",
        "openai_client_path_hardened",
        "final_qualified",
    ]
    assert result["ai"]["client_path_status"] == "passed"
    assert result["ai"]["client_path_selection"] == "stable_first_fallback"
    assert result["ai"]["client_path_regions"] == 2
    assert browsing_report.exists()
    assert ai_report.exists()


def test_pipeline_retries_only_structured_transient_from_immutable_candidate(
    tmp_path: Path,
) -> None:
    candidate, policies, mihomo, scripts = _pipeline_inputs(tmp_path)
    browsing = scripts / "qualify_browsing.py"
    browsing.write_text(
        "from __future__ import annotations\n"
        "import argparse, json\n"
        "from pathlib import Path\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('--candidate', required=True)\n"
        "args,_=p.parse_known_args()\n"
        "state=Path(__file__).with_suffix('.count')\n"
        "count=int(state.read_text(encoding='utf-8')) if state.exists() else 0\n"
        "state.write_text(str(count + 1), encoding='utf-8')\n"
        "candidate=Path(args.candidate)\n"
        "if count == 0:\n"
        "    with candidate.open('a', encoding='utf-8') as h: h.write('\\nfailed_attempt_marker: true\\n')\n"
        "    print(json.dumps({'status':'rejected','stage':'browsing','failure_category':'transient','retryable':True,'diagnostics':{'tested_nodes':4,'qualified_nodes':0,'successful_samples':0,'failed_samples':12,'outcomes':{'probe_error':12}}}))\n"
        "    raise SystemExit(2)\n"
        "with candidate.open('a', encoding='utf-8') as h: h.write('\\nbrowsing_stage: true\\n')\n"
        "print(json.dumps({'status':'qualified','automatic_nodes':3}))\n",
        encoding="utf-8",
    )
    _fake_success_tail(scripts)

    output = tmp_path / "final.yaml"
    result = run_qualification_pipeline(
        candidate=candidate,
        output=output,
        policies=policies,
        mihomo_bin=mihomo,
        stage_dir=tmp_path / "stages",
        browsing_report=tmp_path / "browsing.json",
        ai_report=tmp_path / "ai.json",
        script_dir=scripts,
        python_executable=sys.executable,
    )

    assert (scripts / "qualify_browsing.count").read_text(encoding="utf-8") == "2"
    assert result["browsing"]["stage_attempts"] == 2
    assert result["browsing"]["recovered_by_retry"] is True
    assert result["browsing"]["recovered_failure_category"] == "transient"
    text = output.read_text(encoding="utf-8")
    assert "browsing_stage: true" in text
    assert "failed_attempt_marker" not in text


def test_pipeline_does_not_retry_policy_rejection(tmp_path: Path) -> None:
    candidate, policies, mihomo, scripts = _pipeline_inputs(tmp_path)
    browsing = scripts / "qualify_browsing.py"
    browsing.write_text(
        "from __future__ import annotations\n"
        "import argparse, json\n"
        "from pathlib import Path\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('--candidate', required=True)\n"
        "p.parse_known_args()\n"
        "state=Path(__file__).with_suffix('.count')\n"
        "count=int(state.read_text(encoding='utf-8')) if state.exists() else 0\n"
        "state.write_text(str(count + 1), encoding='utf-8')\n"
        "print(json.dumps({'status':'rejected','stage':'transport','failure_category':'policy_rejection','retryable':False,'transport_diagnostics':{'tested_nodes':8,'tcp_qualified_nodes':8,'udp_qualified_nodes':0}}))\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="policy_rejection"):
        run_qualification_pipeline(
            candidate=candidate,
            output=tmp_path / "final.yaml",
            policies=policies,
            mihomo_bin=mihomo,
            stage_dir=tmp_path / "stages",
            browsing_report=tmp_path / "browsing.json",
            ai_report=tmp_path / "ai.json",
            script_dir=scripts,
            python_executable=sys.executable,
        )

    assert (scripts / "qualify_browsing.count").read_text(encoding="utf-8") == "1"


def test_pipeline_unstructured_rejection_is_protocol_error_and_not_retried(tmp_path: Path) -> None:
    candidate, policies, mihomo, scripts = _pipeline_inputs(tmp_path)
    browsing = scripts / "qualify_browsing.py"
    browsing.write_text(
        "from pathlib import Path\n"
        "state=Path(__file__).with_suffix('.count')\n"
        "count=int(state.read_text(encoding='utf-8')) if state.exists() else 0\n"
        "state.write_text(str(count + 1), encoding='utf-8')\n"
        "print('legacy failure')\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="protocol_error"):
        run_qualification_pipeline(
            candidate=candidate,
            output=tmp_path / "final.yaml",
            policies=policies,
            mihomo_bin=mihomo,
            stage_dir=tmp_path / "stages",
            browsing_report=tmp_path / "browsing.json",
            ai_report=tmp_path / "ai.json",
            script_dir=scripts,
            python_executable=sys.executable,
        )

    assert (scripts / "qualify_browsing.count").read_text(encoding="utf-8") == "1"


def test_pipeline_surfaces_only_aggregate_rejection_diagnostics(tmp_path: Path) -> None:
    candidate, policies, mihomo, scripts = _pipeline_inputs(tmp_path)
    browsing = scripts / "qualify_browsing.py"
    browsing.write_text(
        "from __future__ import annotations\n"
        "import argparse, json, sys\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('--candidate', required=True)\n"
        "p.parse_known_args()\n"
        "print(json.dumps({'status':'rejected','stage':'transport','failure_category':'policy_rejection','retryable':False,'diagnostics':{'tested_nodes':7,'qualified_nodes':5,'outcomes':{'success':15}},'transport_diagnostics':{'tested_nodes':8,'tcp_qualified_nodes':8,'udp_qualified_nodes':0,'selector_failures':0}}))\n"
        "print('server=private.example token=top-secret', file=sys.stderr)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as caught:
        run_qualification_pipeline(
            candidate=candidate,
            output=tmp_path / "final.yaml",
            policies=policies,
            mihomo_bin=mihomo,
            stage_dir=tmp_path / "stages",
            browsing_report=tmp_path / "browsing.json",
            ai_report=tmp_path / "ai.json",
            script_dir=scripts,
            python_executable=sys.executable,
        )

    message = str(caught.value)
    assert "policy_rejection" in message
    assert '"stage":"transport"' in message
    assert '"tested_nodes":8' in message
    assert '"udp_qualified_nodes":0' in message
    assert "private.example" not in message
    assert "top-secret" not in message
