.PHONY: install lint format test run dry-run watch status test-destinations clean

install:
	python -m pip install -e ".[dev]"

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

test:
	pytest

run:
	restock-alerts run

dry-run:
	restock-alerts run --dry-run

watch:
	restock-alerts watch --every 10

status:
	restock-alerts status

test-destinations:
	restock-alerts test-destinations

clean:
	rm -rf build dist src/*.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
