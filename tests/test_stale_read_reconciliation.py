"""State model for stale KV read views after an ambiguous release commit.

The fake separates the state the remote service actually holds (``storage``) from
what a given observation path returns (``view``). Each key can be marked stale so
that an accepted write stays invisible, which models an eventually consistent read
path. Tests use this to characterise which verdicts the read-only reconciler can
produce, and what the lifecycle then does with the private recovery evidence.

Two kinds of assertion live here and must not be confused:

* characterisation of the read-only verdict — which read views produce
  ``not_committed`` while a live candidate is still stored remotely;
* acceptance of the lifecycle — a verdict that cannot date its own observation
  path never releases the only private copy, while a proven commit does.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from clash_relay.errors import CommitUnknownError, PublicationError
from clash_relay.production_lifecycle import ProductionLifecyclePaths, ProductionPipeline
from clash_relay.release_bundle import parse_release_pointer, publish_release_bundle, release_keys
from clash_relay.release_reconciliation import ReconciliationStatus, reconcile_release_bundle
from clash_relay.release_transaction import parse_release_transaction

KEY = "production-config"
FIRST = b"release: first\n"
CANDIDATE = b"release: second\n"


class LaggingKV:
    """Storage truth plus the read view the current observation path returns."""

    def __init__(self) -> None:
        self.storage: dict[str, bytes] = {}
        self.view: dict[str, bytes] = {}
        self.stale: set[str] = set()
        self.reject_once: set[str] = set()
        self.writes: list[str] = []

    def converge(self) -> None:
        """Model propagation completing on the path that observed the attempt."""
        self.view = dict(self.storage)

    def use_view(self, snapshot: dict[str, bytes]) -> None:
        """Model a later observation served by a path that saw fewer writes."""
        self.view = dict(snapshot)

    def pre_attempt_view(self) -> dict[str, bytes]:
        return dict(self.view)

    def factory(self, key: str) -> _Publisher:
        return _Publisher(self, key)


class _Publisher:
    def __init__(self, store: LaggingKV, key: str) -> None:
        self._store = store
        self._key = key

    def resolve_namespace_id(self) -> str:
        return "namespace-fixture"

    def read(self) -> bytes | None:
        return self._store.view.get(self._key)

    def publish(self, *, content: bytes) -> dict[str, Any]:
        store, key = self._store, self._key
        store.writes.append(key)
        if key in store.reject_once:
            store.reject_once.discard(key)
            raise PublicationError("simulated definite write rejection")
        store.storage[key] = bytes(content)
        if key not in store.stale:
            store.view[key] = bytes(content)
        return {
            "key": key,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }


def _ambiguous_second_release(store: LaggingKV, release_read_back_waits: list[float]) -> None:
    """Publish one release, then start an attempt whose activation cannot be verified."""
    publish_release_bundle(factory=store.factory, production_key=KEY, content=FIRST)
    store.stale = {KEY}
    with pytest.raises(CommitUnknownError) as error:
        publish_release_bundle(factory=store.factory, production_key=KEY, content=CANDIDATE)
    assert getattr(error.value, "production_changed", None) == "unknown"
    assert release_read_back_waits == [0.25, 0.5, 1.0, 2.0]
    release_read_back_waits.clear()


def _ambiguous_first_release(store: LaggingKV, release_read_back_waits: list[float]) -> None:
    store.stale = {KEY}
    with pytest.raises(CommitUnknownError) as error:
        publish_release_bundle(factory=store.factory, production_key=KEY, content=CANDIDATE)
    assert getattr(error.value, "production_changed", None) == "unknown"
    release_read_back_waits.clear()


def _reconcile(store: LaggingKV, previous: bytes | None):
    """Reconcile the way the public CLI does: one observation with no freshness claim."""
    return reconcile_release_bundle(
        factory=store.factory,
        production_key=KEY,
        candidate_content=CANDIDATE,
        previous_content=previous,
    )


def _dated_reconcile(store: LaggingKV, previous: bytes | None):
    """Reconcile with the caller's dated-read assertion, the conclusive non-commit path."""
    return reconcile_release_bundle(
        factory=store.factory,
        production_key=KEY,
        candidate_content=CANDIDATE,
        previous_content=previous,
        fresh_read_evidence=True,
    )


def test_visible_pending_transaction_forces_unknown_not_not_committed(
    release_read_back_waits: list[float],
) -> None:
    store = LaggingKV()
    _ambiguous_second_release(store, release_read_back_waits)
    assert store.storage[KEY] == CANDIDATE
    written = len(store.writes)

    result = _reconcile(store, FIRST)

    assert result.status is ReconciliationStatus.UNKNOWN
    assert result.reason == "transaction_incomplete_or_not_yet_visible"
    assert result.to_dict()["requires_manual_action"] is True
    assert len(store.writes) == written


def test_convergence_completes_the_interrupted_attempt_and_then_proves_commit(
    release_read_back_waits: list[float],
) -> None:
    store = LaggingKV()
    _ambiguous_second_release(store, release_read_back_waits)
    store.converge()
    written = len(store.writes)

    interrupted = _reconcile(store, FIRST)
    resumed = publish_release_bundle(factory=store.factory, production_key=KEY, content=CANDIDATE)
    committed = _reconcile(store, FIRST)

    assert interrupted.status is ReconciliationStatus.UNKNOWN
    assert resumed["recovered_transaction"] is True
    assert resumed["production_changed"] is True
    assert committed.status is ReconciliationStatus.COMMITTED
    assert committed.reason == "release_transaction_fully_visible"
    assert committed.production_changed is True
    assert committed.to_dict()["requires_manual_action"] is False
    assert committed.current_release_id == hashlib.sha256(CANDIDATE).hexdigest()
    keys = release_keys(KEY)
    assert (
        parse_release_pointer(store.storage[keys.previous_pointer])
        == hashlib.sha256(FIRST).hexdigest()
    )
    after_resume = len(store.writes)
    assert _reconcile(store, FIRST).status is ReconciliationStatus.COMMITTED
    assert len(store.writes) == after_resume
    assert written < after_resume


def test_definite_rejection_stays_indeterminate_for_the_untouched_client_key(
    release_read_back_waits: list[float],
) -> None:
    store = LaggingKV()
    publish_release_bundle(factory=store.factory, production_key=KEY, content=FIRST)
    store.reject_once = {KEY}

    with pytest.raises(PublicationError):
        publish_release_bundle(factory=store.factory, production_key=KEY, content=CANDIDATE)

    assert store.storage[KEY] == FIRST
    written = len(store.writes)
    result = _reconcile(store, FIRST)

    assert result.status is ReconciliationStatus.INDETERMINATE
    assert result.reason == "pre_attempt_production_still_live"
    assert result.production_changed == "unknown"
    assert result.observation_freshness == "unsupported"
    assert result.to_dict()["requires_manual_action"] is True
    assert len(store.writes) == written

    dated = _dated_reconcile(store, FIRST)

    assert dated.status is ReconciliationStatus.NOT_COMMITTED
    assert dated.production_changed is False
    assert dated.observation_freshness == "fresh"


def test_first_release_with_a_lagging_activation_never_reports_not_committed(
    release_read_back_waits: list[float],
) -> None:
    store = LaggingKV()
    _ambiguous_first_release(store, release_read_back_waits)
    assert store.storage[KEY] == CANDIDATE
    written = len(store.writes)

    result = _reconcile(store, None)

    assert result.status is ReconciliationStatus.UNKNOWN
    # The staged current pointer and the pending intent are both visible on the
    # attempt's own path, so the first release cannot be called not committed there.
    assert result.reason == "transaction_incomplete_or_not_yet_visible"
    assert len(store.writes) == written

    store.converge()
    publish_release_bundle(factory=store.factory, production_key=KEY, content=CANDIDATE)
    converged = _reconcile(store, None)

    assert converged.status is ReconciliationStatus.COMMITTED
    assert converged.reason == "first_release_fully_visible"
    assert parse_release_pointer(store.storage.get(release_keys(KEY).previous_pointer)) is None


def test_observation_path_that_never_saw_the_attempt_is_indeterminate(
    release_read_back_waits: list[float],
) -> None:
    """The stale view the V01 review established: an undatable verdict, not a non-commit.

    A live candidate is observed only when the reconciliation is served by a path
    on which the pending transaction, the accepted activation write, and the
    staged pointer are all invisible again. Without dated read evidence the
    verdict cannot be reported as a conclusive ``not_committed``.
    """
    store = LaggingKV()
    publish_release_bundle(factory=store.factory, production_key=KEY, content=FIRST)
    pre_attempt = store.pre_attempt_view()
    _ambiguous_second_release(store, release_read_back_waits)
    assert store.storage[KEY] == CANDIDATE
    store.use_view(pre_attempt)

    result = _reconcile(store, FIRST)

    assert result.status is ReconciliationStatus.INDETERMINATE
    assert result.reason == "pre_attempt_production_still_live"
    assert result.production_changed == "unknown"
    assert result.observation_freshness == "unsupported"
    assert result.to_dict()["requires_manual_action"] is True
    assert store.storage[KEY] == CANDIDATE
    pending = parse_release_transaction(store.storage[release_keys(KEY).transaction])
    assert pending is not None
    assert pending.to_release_id == hashlib.sha256(CANDIDATE).hexdigest()
    assert pending.from_release_id == hashlib.sha256(FIRST).hexdigest()


def test_dated_observation_of_the_same_state_is_a_conclusive_non_commit(
    release_read_back_waits: list[float],
) -> None:
    """The conclusive path: the same bytes, but with dated read evidence."""
    store = LaggingKV()
    publish_release_bundle(factory=store.factory, production_key=KEY, content=FIRST)
    pre_attempt = store.pre_attempt_view()
    _ambiguous_second_release(store, release_read_back_waits)
    store.use_view(pre_attempt)

    result = _dated_reconcile(store, FIRST)

    assert result.status is ReconciliationStatus.NOT_COMMITTED
    assert result.reason == "pre_attempt_production_still_live"
    assert result.production_changed is False
    assert result.observation_freshness == "fresh"
    assert result.to_dict()["requires_manual_action"] is False


def test_a_visible_pending_transaction_stays_unknown_even_with_dated_evidence(
    release_read_back_waits: list[float],
) -> None:
    store = LaggingKV()
    _ambiguous_second_release(store, release_read_back_waits)

    result = _dated_reconcile(store, FIRST)

    assert result.status is ReconciliationStatus.UNKNOWN
    assert result.reason == "transaction_incomplete_or_not_yet_visible"
    assert result.observation_freshness is None


def test_hiding_only_the_pending_intent_reports_indeterminate_for_a_live_candidate(
    release_read_back_waits: list[float],
) -> None:
    """The weaker assumption the V01 review established for a second release.

    The attempt's activation write stayed invisible on its own read path while the
    current pointer was never updated, so hiding the pending intent alone is
    enough. No staged or updated pointer has to become invisible again.
    """
    store = LaggingKV()
    _ambiguous_second_release(store, release_read_back_waits)
    keys = release_keys(KEY)
    assert store.view[KEY] == FIRST
    assert store.storage[KEY] == CANDIDATE
    assert (
        parse_release_pointer(store.view.get(keys.current_pointer))
        != hashlib.sha256(CANDIDATE).hexdigest()
    )

    store.view.pop(keys.transaction)

    result = _reconcile(store, FIRST)

    assert result.status is ReconciliationStatus.INDETERMINATE
    assert result.reason == "pre_attempt_production_still_live"
    assert result.production_changed == "unknown"
    assert store.storage[KEY] == CANDIDATE
    assert store.storage[keys.transaction] is not None


def test_an_empty_first_release_view_is_indeterminate_for_a_live_candidate(
    release_read_back_waits: list[float],
) -> None:
    """The first-release variant of the same read-view hazard.

    The pending intent is visible on the attempt's own path, which is why the
    earlier lagging-activation track never reached this verdict. A path that shows
    nothing at all — no production value, no pointer, no intent — is reported as
    ``indeterminate`` while the remote copy holds the first candidate.
    """
    store = LaggingKV()
    _ambiguous_first_release(store, release_read_back_waits)
    assert store.storage[KEY] == CANDIDATE
    store.use_view({})

    result = _reconcile(store, None)

    assert result.status is ReconciliationStatus.INDETERMINATE
    assert result.reason == "pre_attempt_production_still_live"
    assert result.production_changed == "unknown"
    assert store.storage[KEY] == CANDIDATE


def _copy_names(directory: Path) -> list[str]:
    """List a recovery directory without depending on filesystem enumeration order."""
    return sorted(entry.name for entry in directory.iterdir())


_ENV = {
    "CLOUDFLARE_API_TOKEN": "fixture-token",
    "CLOUDFLARE_ACCOUNT_ID": "fixture-account",
    "CLOUDFLARE_KV_NAMESPACE_TITLE": "fixture-namespace",
}


def _lifecycle_evidence(store: LaggingKV, monkeypatch, tmp_path):
    """Drive the real reconciliation through the lifecycle and report the retained copy."""
    paths = ProductionLifecyclePaths.canonical(tmp_path)
    paths.private_dir.mkdir(parents=True)
    (paths.private_dir / "config.yaml").write_bytes(CANDIDATE)
    (paths.private_dir / "current-production.yaml").write_bytes(FIRST)
    (paths.private_dir / "current-production.captured").write_text("captured\n")
    for name, value in _ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        "clash_relay.production_application._publisher",
        lambda **kwargs: store.factory(kwargs["key_name"]),
    )
    project = SimpleNamespace(config={"publishing": {"cloudflare_kv": {"key": KEY}}})

    status = ProductionPipeline(paths, publish=True)._preserve_ambiguous_release(project)

    recovery_root = paths.work_dir / "recovery"
    copies = sorted(recovery_root.iterdir()) if recovery_root.exists() else []
    metadata = (
        json.loads((copies[0] / "metadata.json").read_text(encoding="utf-8")) if copies else None
    )
    return status, copies, metadata


def test_lifecycle_retains_exact_evidence_while_the_commit_stays_unknown(
    release_read_back_waits: list[float], monkeypatch, tmp_path
) -> None:
    store = LaggingKV()
    _ambiguous_second_release(store, release_read_back_waits)

    status, copies, metadata = _lifecycle_evidence(store, monkeypatch, tmp_path)

    assert status == "preserved"
    assert _copy_names(copies[0]) == [
        "candidate.yaml",
        "metadata.json",
        "previous.yaml",
    ]
    assert (copies[0] / "candidate.yaml").read_bytes() == CANDIDATE
    assert (copies[0] / "previous.yaml").read_bytes() == FIRST
    assert metadata is not None
    assert metadata["reconciliation_status"] == "unknown"


def test_lifecycle_keeps_the_only_private_copy_when_the_read_path_lagged(
    release_read_back_waits: list[float], monkeypatch, tmp_path
) -> None:
    """V01 acceptance: an undatable observation never releases the copy.

    The remote copy holds the candidate, so discarding the local copy on the
    strength of one read-only pass would leave no private evidence at all. The
    observed verdict is recorded instead of acted on.
    """
    store = LaggingKV()
    publish_release_bundle(factory=store.factory, production_key=KEY, content=FIRST)
    pre_attempt = store.pre_attempt_view()
    _ambiguous_second_release(store, release_read_back_waits)
    store.use_view(pre_attempt)

    status, copies, metadata = _lifecycle_evidence(store, monkeypatch, tmp_path)

    assert status == "preserved"
    assert metadata is not None
    assert metadata["reconciliation_status"] == "indeterminate"
    assert _copy_names(copies[0]) == [
        "candidate.yaml",
        "metadata.json",
        "previous.yaml",
    ]
    assert (copies[0] / "candidate.yaml").read_bytes() == CANDIDATE
    assert (copies[0] / "previous.yaml").read_bytes() == FIRST
    assert store.storage[KEY] == CANDIDATE


def test_lifecycle_retains_no_evidence_once_the_attempt_completed_after_convergence(
    release_read_back_waits: list[float], monkeypatch, tmp_path
) -> None:
    store = LaggingKV()
    _ambiguous_second_release(store, release_read_back_waits)
    store.converge()
    publish_release_bundle(factory=store.factory, production_key=KEY, content=CANDIDATE)

    status, copies, metadata = _lifecycle_evidence(store, monkeypatch, tmp_path)

    assert status == "committed"
    assert copies == []
    assert metadata is None


def _declarations(paths: ProductionLifecyclePaths) -> None:
    for name in ("config.yaml", "subscriptions.yaml", "policies.yaml"):
        (paths.config.parent / name).write_text("fixture: true\n", encoding="utf-8")


def _drive_run(
    store: LaggingKV,
    monkeypatch: pytest.MonkeyPatch,
    paths: ProductionLifecyclePaths,
    *,
    release_failure: Exception,
) -> ProductionPipeline:
    """Drive ``run()`` to the release stage without generation or kernel work."""
    _declarations(paths)
    project = SimpleNamespace(
        config={
            "publishing": {
                "artifact": False,
                "github_release": {"enabled": False},
                "gist": {"enabled": False},
                "cloudflare_kv": {"enabled": True, "key": KEY},
            }
        }
    )
    monkeypatch.setattr("clash_relay.production_lifecycle.ProjectPaths.load", lambda self: project)
    for name, value in _ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        "clash_relay.production_application._publisher",
        lambda **kwargs: store.factory(kwargs["key_name"]),
    )
    pipeline = ProductionPipeline(paths, publish=True)
    monkeypatch.setattr(pipeline, "_generate", lambda: {"status": "passed"})
    monkeypatch.setattr(pipeline, "_load_derived_state", lambda project_arg: None)
    monkeypatch.setattr(pipeline, "_download_primary_mihomo", lambda: paths.bin_dir / "mihomo")
    monkeypatch.setattr(pipeline, "_qualify", lambda binary: {"status": "passed"})
    monkeypatch.setattr(pipeline, "_render_source_quality", lambda: {"status": "passed"})
    monkeypatch.setattr(
        pipeline, "_render_flclash_client_contract", lambda project_arg: {"status": "passed"}
    )

    def stage(project_arg, binary):
        pipeline._private("config.yaml").write_bytes(CANDIDATE)
        pipeline._private("current-production.yaml").write_bytes(FIRST)
        pipeline._private("current-production.captured").write_text("captured\n", encoding="utf-8")
        raise release_failure

    monkeypatch.setattr(pipeline, "_release_candidate_stage", stage)
    return pipeline


def _run_stale_view_directory(
    release_read_back_waits: list[float], monkeypatch, work_dir: Path
) -> Path:
    """Drive ``run()`` over a stale view and return the single recovery directory."""
    store = LaggingKV()
    publish_release_bundle(factory=store.factory, production_key=KEY, content=FIRST)
    pre_attempt = store.pre_attempt_view()
    _ambiguous_second_release(store, release_read_back_waits)
    store.use_view(pre_attempt)
    paths = ProductionLifecyclePaths.canonical(work_dir)
    pipeline = _drive_run(
        store,
        monkeypatch,
        paths,
        release_failure=CommitUnknownError("simulated ambiguous activation"),
    )

    with pytest.raises(CommitUnknownError) as error:
        pipeline.run()

    assert getattr(error.value, "recovery_status", None) == "preserved"
    assert (
        getattr(error.value, "candidate_release_id", None) == hashlib.sha256(CANDIDATE).hexdigest()
    )
    assert not paths.private_dir.exists()
    assert store.storage[KEY] == CANDIDATE
    return sorted((paths.work_dir / "recovery").iterdir())[0]


def test_run_keeps_the_recovery_copy_and_cleans_the_private_directory(
    release_read_back_waits: list[float], monkeypatch, tmp_path
) -> None:
    """V01 end-to-end: exception, retention and the ``finally`` cleanup in one pass."""
    copies = _run_stale_view_directory(release_read_back_waits, monkeypatch, tmp_path)

    assert _copy_names(copies) == [
        "candidate.yaml",
        "metadata.json",
        "previous.yaml",
    ]
    metadata = json.loads((copies / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["reconciliation_status"] == "indeterminate"
    assert (copies / "candidate.yaml").read_bytes() == CANDIDATE


def test_recovery_listing_does_not_depend_on_enumeration_order(
    release_read_back_waits: list[float], monkeypatch, tmp_path
) -> None:
    """R12: both legal enumeration orders must read as identical on every trace.

    The reverse order is injected before the production ``run()``, so the run
    trace is covered as well, not only the helper indirection.
    """
    straight = Path.iterdir

    def reverse_order(self: Path):
        return iter(sorted(straight(self), key=lambda entry: entry.name, reverse=True))

    store = LaggingKV()
    _ambiguous_second_release(store, release_read_back_waits)
    _, ascending, _ = _lifecycle_evidence(store, monkeypatch, tmp_path / "ascending")

    monkeypatch.setattr(Path, "iterdir", reverse_order)
    store = LaggingKV()
    _ambiguous_second_release(store, release_read_back_waits)
    _, descending, _ = _lifecycle_evidence(store, monkeypatch, tmp_path / "descending")

    run_work_dir = tmp_path / "run-reverse-order"
    run_work_dir.mkdir()
    store = LaggingKV()
    _ambiguous_second_release(store, release_read_back_waits)
    run_copy = _run_stale_view_directory(release_read_back_waits, monkeypatch, run_work_dir)

    assert _copy_names(ascending[0]) == ["candidate.yaml", "metadata.json", "previous.yaml"]
    assert _copy_names(descending[0]) == _copy_names(ascending[0])
    assert _copy_names(run_copy) == _copy_names(ascending[0])


def test_run_retains_no_copy_when_the_commit_is_proven_and_cleans_private(
    release_read_back_waits: list[float], monkeypatch, tmp_path
) -> None:
    """A proven commit stays a distinct path: no recovery copy, private dir removed."""
    store = LaggingKV()
    _ambiguous_second_release(store, release_read_back_waits)
    store.converge()
    publish_release_bundle(factory=store.factory, production_key=KEY, content=CANDIDATE)
    paths = ProductionLifecyclePaths.canonical(tmp_path)
    pipeline = _drive_run(
        store,
        monkeypatch,
        paths,
        release_failure=CommitUnknownError("simulated committed activation"),
    )

    with pytest.raises(CommitUnknownError) as error:
        pipeline.run()

    assert getattr(error.value, "recovery_status", None) == "committed"
    assert not paths.private_dir.exists()
    assert not (paths.work_dir / "recovery").exists()


def test_run_does_not_treat_a_definite_rejection_as_an_ambiguous_commit(
    release_read_back_waits: list[float], monkeypatch, tmp_path
) -> None:
    """A definite refusal never enters the ambiguous path or writes a copy."""
    store = LaggingKV()
    paths = ProductionLifecyclePaths.canonical(tmp_path)
    pipeline = _drive_run(
        store,
        monkeypatch,
        paths,
        release_failure=PublicationError("simulated definite write rejection"),
    )

    with pytest.raises(PublicationError) as error:
        pipeline.run()

    assert not hasattr(error.value, "recovery_status")
    assert not paths.private_dir.exists()
    assert not (paths.work_dir / "recovery").exists()
    assert store.storage == {}
