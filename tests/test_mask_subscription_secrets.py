from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mask_subscription_secrets.py"


def _run_masker(*, subscriptions: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CLASH_RELAY_SUBSCRIPTIONS"] = subscriptions
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_masker_masks_each_source_url():
    subscription = "https://subscription.example/source"

    result = _run_masker(
        subscriptions=json.dumps({"SUBSCRIPTION_1_URL": subscription}),
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.splitlines() == [f"::add-mask::{subscription}"]


def test_masker_emits_nothing_without_subscriptions():
    result = _run_masker(subscriptions="{}")

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == ""
