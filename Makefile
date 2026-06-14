.DEFAULT_GOAL := help

.PHONY: help install lint format format-check typecheck test test-guardrails check quality verify-inventory verify-formal verify-fuzz-fast verify-docs verify-supply-chain verify-new-tests verify-known-baseline verify-repo-strict

help:
	@printf "Available targets:\n"
	@printf "  install        Install the package in editable mode\n"
	@printf "  lint           Run Ruff lint checks\n"
	@printf "  format         Format the repository with Ruff\n"
	@printf "  format-check   Check formatting without modifying files\n"
	@printf "  typecheck      Run ty type checking\n"
	@printf "  test           Run the full test suite with coverage\n"
	@printf "  test-guardrails Run the focused translator/recognize guardrail suite\n"
	@printf "  check          Run lint, format-check, and typecheck\n"
	@printf "  quality        Run check plus tests\n"
	@printf "  verify-inventory Run repo-wide verification inventory coverage gate\n"
	@printf "  verify-formal Run dependency-free formal model checkers\n"
	@printf "  verify-fuzz-fast Run bounded Python/frontend fuzz and fault gates\n"
	@printf "  verify-docs Run version and secret/documentation gates\n"
	@printf "  verify-supply-chain Run local supply-chain gates\n"
	@printf "  verify-new-tests Run newly added verification tests/gates\n"
	@printf "  verify-known-baseline Check the known full-pytest failure ledger exists\n"
	@printf "  verify-repo-strict Run release-blocking verification gates\n"

install:
	uv pip install -e .

lint:
	uv run ruff check . --output-format concise

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

typecheck:
	uv run ty check

test:
	uv run python -m pytest \
		tests \
		python/deepresearch_flow/ocr/tests \
		python/deepresearch_flow/paper/tests \
		python/deepresearch_flow/paper/snapshot/tests \
		python/deepresearch_flow/storage/tests \
		python/deepresearch_flow/translator/tests \
		--cov=deepresearch_flow \
		--cov-report=term-missing \
		-q

test-guardrails:
	uv run python -m pytest -q \
		python/deepresearch_flow/translator/tests/test_fixers.py \
		python/deepresearch_flow/translator/tests/test_protector.py \
		python/deepresearch_flow/translator/tests/test_engine_translate_guardrails.py \
		python/deepresearch_flow/translator/tests/test_cli_translate.py \
		tests/test_math.py \
		tests/test_mermaid.py

check: lint format-check typecheck

quality: check test

verify-inventory:
	uv run python tools/verification/generate_inventory.py --output docs/verification/repo-verification-inventory.json --check
	uv run python tools/verification/generate_bootstrap_manifest.py \
		--inventory docs/verification/repo-verification-inventory.json \
		--output docs/verification/repo-verification-manifest.yml \
		--check
	uv run python tools/verification/check_manifest_coverage.py \
		--inventory docs/verification/repo-verification-inventory.json \
		--manifest docs/verification/repo-verification-manifest.yml

verify-formal:
	uv run python tools/formal/check_all_models.py

verify-fuzz-fast:
	HYPOTHESIS_PROFILE=ci-fast uv run pytest \
		python/deepresearch_flow/paper/snapshot/tests/test_oauth_client_cache.py \
		tests/verification \
		-m "fuzz_fast or fault" \
		-q
	cd frontend && npm run test:fuzz

verify-docs:
	uv run python tools/verification/check_versions.py
	uv run python tools/verification/check_doc_secrets.py

verify-supply-chain:
	uv run python tools/verification/check_supply_chain.py

verify-new-tests:
	uv run pytest tests/verification -q
	$(MAKE) verify-inventory
	$(MAKE) verify-formal
	$(MAKE) verify-fuzz-fast
	$(MAKE) verify-docs
	$(MAKE) verify-supply-chain

verify-known-baseline:
	test -s docs/verification/baseline-pytest-failures.json

verify-repo-strict: check verify-new-tests verify-known-baseline
	uv run python -m compileall -q python tests tools
	$(MAKE) test
	cd frontend && npm test -- --run
	cd frontend && npm run build
	cd frontend && npm audit --audit-level=high
