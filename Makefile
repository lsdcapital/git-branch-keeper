.PHONY: help build check fix format lint type-check test clean

help:
	@echo "Available targets:"
	@echo "  make build        - Set up development environment"
	@echo "  make check        - Run all quality checks (lint, type-check, test, format)"
	@echo "  make fix          - Auto-fix issues (ruff, black)"
	@echo "  make format       - Format code with black"
	@echo "  make lint         - Lint code with ruff"
	@echo "  make typecheck    - Type check with mypy"
	@echo "  make test         - Run pytest"
	@echo "  make clean        - Clean up cache files"
	@echo "  make help         - Show this help message"

build:
	uv sync --dev

check:
	@echo "\n========================================================"
	@echo "Running lint..."
	@echo "========================================================"
	uv run ruff check .
	@echo "\n========================================================"
	@echo "Running typecheck..."
	@echo "========================================================"
	uv run mypy .
	@echo "\n========================================================"
	@echo "Running tests..."
	@echo "========================================================"
	uv run pytest
	@echo "\n========================================================"
	@echo "Running format check..."
	@echo "========================================================"
	uv run black --check .
	@echo "\n========================================================"
	@echo "✅ All checks passed!"
	@echo "========================================================"

format:
	uv run black .

fix:
	uv run ruff check . --fix
	uv run black .

lint:
	uv run ruff check . --fix

typecheck:
	uv run mypy .

test:
	uv run pytest

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
