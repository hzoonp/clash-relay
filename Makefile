.PHONY: install lint unit integration fixture build check audit clean

install:
	python -m pip install -r requirements-dev.lock -e .

lint:
	ruff check .
	ruff format --check .

unit:
	pytest -m "not integration"

integration:
	pytest -m integration

fixture:
	python scripts/make_fixture_sources.py

build: fixture
	clash-relay build --config tests/fixtures/project/config.yaml --subscriptions tests/fixtures/project/subscriptions.yaml --services services.yaml --policies policies.yaml --secret-file .work/fixture-secrets.yaml --output dist/fixture/config.yaml

audit:
	python scripts/repository_audit.py

check: lint unit audit build

clean:
	rm -rf .work dist/fixture .pytest_cache .ruff_cache
