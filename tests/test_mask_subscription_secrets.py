from __future__ import annotations

import json

from scripts.mask_subscription_secrets import main


def test_masker_masks_each_source_url_and_final_client_entry(monkeypatch, capsys):
    subscription = "https://subscription.example/source"
    profile_url = "https://entry.example/profile" + "?key=" + "fixture-secret"
    monkeypatch.setenv(
        "CLASH_RELAY_SUBSCRIPTIONS",
        json.dumps({"SUBSCRIPTION_1_URL": subscription}),
    )
    monkeypatch.setenv("CLASH_RELAY_PROFILE_URL", profile_url)

    assert main() == 0
    lines = capsys.readouterr().out.splitlines()
    assert f"::add-mask::{subscription}" in lines
    assert f"::add-mask::{profile_url}" in lines


def test_masker_skips_unconfigured_profile_secret(monkeypatch, capsys):
    monkeypatch.delenv("CLASH_RELAY_PROFILE_URL", raising=False)
    monkeypatch.setenv("CLASH_RELAY_SUBSCRIPTIONS", "{}")

    assert main() == 0
    assert capsys.readouterr().out == ""
