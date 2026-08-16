.PHONY: install run mongodb seed indexes test lint format clean

install:
	poetry install

mongodb:
	docker compose up -d mongodb

indexes:
	poetry run python scripts/create_indexes.py

seed:
	poetry run python scripts/seed_clinic.py

seed-fresh:
	poetry run python scripts/seed_clinic.py --wipe
	poetry run python scripts/create_indexes.py

run:
	poetry run uvicorn app.main:app --host 0.0.0.0 --port 4226 --reload

run-prod:
	poetry run uvicorn app.main:app --host 0.0.0.0 --port 4226 --workers 2

test:
	poetry run pytest -q

test-cov:
	poetry run pytest --cov=app --cov-report=term-missing

lint:
	poetry run ruff check app/ scripts/ tests/

format:
	poetry run black app/ scripts/ tests/ && poetry run ruff check --fix app/ scripts/ tests/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
