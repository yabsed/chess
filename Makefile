PORT ?= 5174
CHESS_DATABASE_URL ?= postgresql://chess:chess@127.0.0.1:5432/chess
CHESS_TEST_DATABASE_URL ?= $(CHESS_DATABASE_URL)
export CHESS_DATABASE_URL CHESS_TEST_DATABASE_URL

.PHONY: install database backend backend-docker frontend check test build serve

install:
	uv sync --directory apps/backend --locked
	npm ci --prefix apps/frontend

database:
	docker compose up -d --wait postgres

backend:
	uv run --directory apps/backend uvicorn app.main:app --reload --ws-max-size 4096

backend-docker:
	docker compose up -d --build backend

frontend:
	npm run --prefix apps/frontend dev -- --port $(PORT)

check:
	npm run --prefix apps/frontend check

test:
	uv run --directory apps/backend python -m unittest discover -s tests -v

build:
	npm run --prefix apps/frontend build

serve:
	npm run --prefix apps/frontend preview -- --port $(PORT)
