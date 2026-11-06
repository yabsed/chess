PORT ?= 5174
IMAGE ?= chess-backend:local

.PHONY: install database backend frontend check image build serve

install:
	npm ci --prefix apps/frontend

database:
	docker compose up -d --wait postgres

backend: image
	IMAGE="$(IMAGE)" docker compose up -d --no-build backend

frontend:
	npm run --prefix apps/frontend dev -- --port $(PORT)

check:
	npm run --prefix apps/frontend check

image:
	docker build --platform linux/amd64 -t "$(IMAGE)" apps/backend

build:
	npm run --prefix apps/frontend build

serve:
	npm run --prefix apps/frontend preview -- --port $(PORT)
