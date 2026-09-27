.PHONY: install lint typecheck unit integration fixture build check audit clean

install:
	python -m pip install --require-hashes --only-binary=:all: --requirement requirements-dev.lock
	python -m pip install --no-build-isolation --no-deps --editable .

lint:
	ruff check .
	ruff format --check .

typecheck:
	mypy --follow-imports=skip --ignore-missing-imports --check-untyped-defs \
		src/clash_relay/builder.py \
		src/clash_relay/classify.py \
		src/clash_relay/cli.py \
		src/clash_relay/config_loader.py \
		src/clash_relay/diagnose.py \
		src/clash_relay/node_policy.py \
		src/clash_relay/selector.py \
		src/clash_relay/policy_compiler.py \
		src/clash_relay/runtime_graph.py \
		src/clash_relay/availability.py \
		src/clash_relay/service_qualification.py \
		src/clash_relay/service_qualification_result.py \
		src/clash_relay/ai_application.py \
		src/clash_relay/ai_runtime_reliability.py \
		src/clash_relay/ai_service_qualification.py \
		src/clash_relay/browsing_application.py \
		src/clash_relay/browsing_runtime.py \
		src/clash_relay/scheduler_history.py \
		src/clash_relay/scheduler_policy.py \
		src/clash_relay/qualification_pipeline.py \
		src/clash_relay/qualification_performance.py \
		src/clash_relay/qualification_pipeline_result.py \
		src/clash_relay/qualification_reliability.py \
		src/clash_relay/production_application.py \
		src/clash_relay/production_pipeline.py \
		src/clash_relay/production_diagnostics.py \
		src/clash_relay/production_proof.py \
		src/clash_relay/production_event_audit.py \
		src/clash_relay/production_failure_metrics.py \
		src/clash_relay/promotion_guard.py \
		src/clash_relay/production_release_stage.py \
		src/clash_relay/health_check_inventory.py \
		src/clash_relay/release_bundle.py \
		src/clash_relay/release_reconciliation.py \
		src/clash_relay/publishers/cloudflare_kv.py \
		src/clash_relay/operational_slo.py \
		src/clash_relay/slo_application.py \
		src/clash_relay/production_lifecycle.py \
		src/clash_relay/production_lifecycle_result.py \
		src/clash_relay/publication_decision.py

unit:
	pytest -m "not integration"

integration:
	pytest -m integration

fixture:
	python scripts/make_fixture_sources.py

build: fixture
	clash-relay generate --config tests/fixtures/project/config.yaml --subscriptions tests/fixtures/project/subscriptions.yaml --policies tests/fixtures/project/policies.yaml --secret-file .work/fixture-secrets.yaml --output dist/fixture/config.yaml

audit:
	python scripts/repository_audit.py
	python scripts/audit_documentation_contract.py
	python scripts/audit_architecture_contract.py
	python scripts/audit_operational_slo_contract.py
	python scripts/audit_client_dns_verification_contract.py
	python scripts/audit_service_qualification_contract.py
	python scripts/audit_v2_release_contract.py
	python scripts/audit_supply_chain.py
	python scripts/audit_acl4ssr_fidelity.py --offline

check: lint typecheck unit audit build

clean:
	rm -rf .work dist/fixture .pytest_cache .ruff_cache
