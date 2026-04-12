.DEFAULT_GOAL := help

.PHONY: help install lint format format-check typecheck test test-guardrails check quality

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
