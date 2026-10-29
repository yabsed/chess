PORT ?= 5174

.PHONY: install backend frontend check build serve

install:
	uv sync --directory apps/backend --locked
	npm ci --prefix apps/frontend

backend:
	uv run --directory apps/backend uvicorn app.main:app --reload --ws-max-size 4096

frontend:
	npm run --prefix apps/frontend dev -- --port $(PORT)

check:
	uv run --directory apps/backend pytest -q
	npm run --prefix apps/frontend check

build:
	npm run --prefix apps/frontend build

serve:
	uv run --directory apps/backend uvicorn app.main:app --ws-max-size 4096

