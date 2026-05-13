.PHONY: test lint typecheck run-chaos report clean docker-up docker-down

test:
	pytest -q

lint:
	ruff check src tests scripts

typecheck:
	mypy src

run-chaos:
	python scripts/run_chaos.py --config configs/default.yaml --out reports/metrics.json

report:
	python scripts/generate_report.py --metrics reports/metrics.json --out reports/final_report.md

docker-up:
	@(docker info >/dev/null 2>&1) || ( \
	  echo >&2 ""; \
	  echo >&2 "docker-up: Docker Engine is not reachable (daemon not running or Docker Desktop closed)."; \
	  echo >&2 "  Fix: start Docker Desktop on Windows and wait until it is fully started, then retry."; \
	  echo >&2 "  Skip: default config uses fakeredis in RAM — run make run-chaos without docker-up."; \
	  echo >&2 ""; \
	  exit 1)
	docker compose up -d

docker-down:
	docker compose down

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache reports/metrics.json reports/final_report.md
