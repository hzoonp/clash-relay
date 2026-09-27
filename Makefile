.PHONY: install lint typecheck unit integration fixture build check audit clean

install:
	python -m pip install --require-hashes --only-binary=:all: --requirement requirements-dev.lock
	python -m pip install --no-build-isolation --no-deps --editable .

lint:
	ruff check .
	ruff format --check .

typecheck:
	python scripts/run_project_checks.py typecheck

unit:
	pytest -m "not integration"

integration:
	pytest -m integration

fixture:
	python scripts/make_fixture_sources.py

build: fixture
	clash-relay generate --config tests/fixtures/project/config.yaml --subscriptions tests/fixtures/project/subscriptions.yaml --policies tests/fixtures/project/policies.yaml --secret-file .work/fixture-secrets.yaml --output dist/fixture/config.yaml

audit:
	python scripts/run_project_checks.py audit --offline-acl4ssr

check: lint typecheck unit audit build

clean:
	rm -rf .work dist/fixture .pytest_cache .ruff_cache
