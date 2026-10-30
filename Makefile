PORT ?= 5174

.PHONY: install backend backend-docker frontend check build serve

install:
	uv sync --directory apps/backend --locked
	npm ci --prefix apps/frontend

backend:
	uv run --directory apps/backend uvicorn app.main:app --reload --ws-max-size 4096

backend-docker:
	docker build -t chess-backend apps/backend
	docker run --rm --name chess-backend -p 127.0.0.1:8000:8000 chess-backend

frontend:
	npm run --prefix apps/frontend dev -- --port $(PORT)

check:
	npm run --prefix apps/frontend check

build:
	npm run --prefix apps/frontend build

serve:
	uv run --directory apps/backend uvicorn app.main:app --ws-max-size 4096
