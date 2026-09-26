from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mask_subscription_secrets.py"


def _run_masker(*, subscriptions: str, profile_url: str | None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CLASH_RELAY_SUBSCRIPTIONS"] = subscriptions
    if profile_url is None:
        env.pop("CLASH_RELAY_PROFILE_URL", None)
    else:
        env["CLASH_RELAY_PROFILE_URL"] = profile_url
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_masker_masks_each_source_url_and_final_client_entry():
    subscription = "https://subscription.example/source"
    profile_url = "https://entry.example/profile" + "?key=" + "fixture-secret"

    result = _run_masker(
        subscriptions=json.dumps({"SUBSCRIPTION_1_URL": subscription}),
        profile_url=profile_url,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert f"::add-mask::{subscription}" in lines
    assert f"::add-mask::{profile_url}" in lines


def test_masker_skips_unconfigured_profile_secret():
    result = _run_masker(subscriptions="{}", profile_url=None)

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == ""
