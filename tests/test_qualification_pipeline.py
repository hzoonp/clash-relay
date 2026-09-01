from __future__ import annotations

import sys
from pathlib import Path

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


def test_pipeline_uses_private_sequential_stage_files(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("proxy-groups: []\nproxy-providers: {}\nproxies: []\n", encoding="utf-8")
    policies = tmp_path / "policies.yaml"
    policies.write_text("version: 1\n", encoding="utf-8")
    mihomo = tmp_path / "mihomo"
    mihomo.write_text("fake", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    _fake_stage(
        scripts / "qualify_browsing.py",
        "browsing_stage",
        "{'status':'qualified','automatic_nodes':3}",
    )
    _fake_stage(
        scripts / "qualify_ai.py",
        "ai_stage",
        "{'status':'qualified','diagnostics':{'qualification_mode':'per-service'}}",
    )

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
    assert "browsing_stage" not in candidate.read_text(encoding="utf-8")
    assert result["status"] == "qualified"
    assert [row["name"] for row in result["stages"]] == [
        "generated",
        "browsing_transport_qualified",
        "ai_qualified",
        "final_qualified",
    ]
    assert browsing_report.exists()
    assert ai_report.exists()
