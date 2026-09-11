def _read(path: str) -> str:
    with open(path, encoding="utf-8") as stream:
        return stream.read()


def test_canonical_runner_keeps_decision_sha_lifecycle_and_audit_in_order() -> None:
    runner = _read("scripts/run_production_release.py")
    ordered = (
        "decision = resolve_publication_decision(",
        "publish = decision.should_publish",
        "_enforce_validated_ci_sha(publish=publish)",
        "result = ProductionLifecycleResult.from_pipeline(",
        "audit_production_event_result(result, decision)",
        "print(json.dumps(result.as_dict()",
    )
    positions = [runner.find(token) for token in ordered]

    assert all(position >= 0 for position in positions)
    assert positions == sorted(positions)
    assert runner.count("ProductionLifecycleResult.from_pipeline(") == 1
    assert runner.count("audit_production_event_result(result, decision)") == 1


def test_publish_workflow_has_no_second_production_orchestration_path() -> None:
    workflow = _read(".github/workflows/publish.yml")

    assert workflow.count("python scripts/run_production_release.py") == 1
    assert "publish_release_bundle.py" not in workflow
    assert "run_production_pipeline.py" not in workflow
    assert "check_promotion_guard.py" not in workflow
    assert "validate_mihomo_matrix.py" not in workflow
