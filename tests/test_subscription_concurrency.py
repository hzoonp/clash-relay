from __future__ import annotations

from threading import Barrier, Event, Lock

import pytest

import clash_relay.builder as builder
from clash_relay.errors import FetchError, GenerationError
from clash_relay.fetch import fetch_subscription


def test_parallel_completion_preserves_candidate_and_source_order(
    project_paths,
    fixture_env,
    built_candidate,
):
    rendezvous = Barrier(3)
    others_done = Event()
    lock = Lock()
    completed = []

    def fetcher(url, **options):
        text = fetch_subscription(url, **options)
        rendezvous.wait(timeout=5)
        if url == fixture_env["SUB_PRIMARY"]:
            assert others_done.wait(timeout=5)
        with lock:
            completed.append(url)
            if len(completed) == 2:
                others_done.set()
        return text

    result = builder.build_candidate(**project_paths, env=fixture_env, fetcher=fetcher)
    assert completed[-1] == fixture_env["SUB_PRIMARY"]
    assert result.yaml_text == built_candidate.yaml_text
    assert result.report == built_candidate.report


def test_subscription_concurrency_is_bounded(monkeypatch, project_paths, fixture_env):
    monkeypatch.setattr(builder, "_SUBSCRIPTION_FETCH_WORKERS", 2)
    rendezvous = Barrier(2)
    lock = Lock()
    active = peak = started = 0

    def fetcher(url, **options):
        nonlocal active, peak, started
        with lock:
            active += 1
            started += 1
            ordinal = started
            peak = max(peak, active)
        try:
            if ordinal <= 2:
                rendezvous.wait(timeout=5)
            return fetch_subscription(url, **options)
        finally:
            with lock:
                active -= 1

    builder.build_candidate(**project_paths, env=fixture_env, fetcher=fetcher)
    assert peak == 2
    assert started == 3
    assert active == 0


def test_parallel_fetch_retains_required_failure_redaction(project_paths, fixture_env):
    def fetcher(url, **options):
        if url == fixture_env["SUB_PRIMARY"]:
            raise FetchError("failed " + url)
        return fetch_subscription(url, **options)

    with pytest.raises(GenerationError) as error:
        builder.build_candidate(**project_paths, env=fixture_env, fetcher=fetcher)
    assert "failed" in str(error.value)
    assert fixture_env["SUB_PRIMARY"] not in str(error.value)
