.PHONY: help up down logs install seed migrate api worker test lint fmt eval-smoke eval-regression eval-adversarial eval-ci

help:
	@echo "up           - start docker compose stack"
	@echo "down         - stop stack"
	@echo "install      - install package in editable mode with dev extras"
	@echo "migrate      - create tables (alembic upgrade head)"
	@echo "seed         - load seed tickets + KB articles"
	@echo "api          - run FastAPI locally"
	@echo "worker       - run arq worker locally"
	@echo "test         - run pytest"
	@echo "lint         - ruff check"
	@echo "fmt          - ruff format"
	@echo "eval-smoke   - run eval smoke suite"
	@echo "eval-regression - run eval regression suite"
	@echo "eval-adversarial - run eval adversarial suite"
	@echo "eval-ci      - run the offline CI eval gates (smoke+adversarial+regression)"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

install:
	pip install -e ".[dev]"

migrate:
	python -c "from apps.api.db import init_db; init_db()"

seed:
	python -m apps.api.seed

api:
	uvicorn apps.api.main:app --reload --port 8000

worker:
	arq apps.worker.main.WorkerSettings

test:
	pytest -q

lint:
	ruff check .

fmt:
	ruff format .

eval-smoke:
	python -m evals.cli run --suite smoke --config configs/v1.yaml

eval-regression:
	python -m evals.cli run --suite regression --config configs/v1.yaml

eval-adversarial:
	python -m evals.cli run --suite adversarial --config configs/v1.yaml

# Reproduce the GitHub Actions eval gates locally. Hermetic: configs/ci.yaml
# forces the stub LLM + heuristic judge, so no API key or network is needed.
# CI runs each suite as its own step; here we chain them so a failing gate stops
# the target with a non-zero exit.
eval-ci:
	python -m evals.cli run --suite smoke --config configs/ci.yaml && \
	python -m evals.cli run --suite adversarial --config configs/ci.yaml && \
	python -m evals.cli run --suite regression --config configs/ci.yaml
