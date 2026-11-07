PORT ?= 5174
IMAGE ?= chess-backend:local
PLATFORM ?= linux/arm64

.PHONY: install database backend frontend check image build serve infra-check

install:
	npm ci --prefix apps/frontend

database:
	docker compose -f infra/local/compose.yaml \
		up -d --wait postgres

backend: image
	IMAGE="$(IMAGE)" PLATFORM="$(PLATFORM)" docker compose -f infra/local/compose.yaml \
		up -d --no-build backend

frontend:
	npm run --prefix apps/frontend dev -- --port $(PORT)

check:
	npm run --prefix apps/frontend check

image:
	docker build --platform "$(PLATFORM)" -t "$(IMAGE)" apps/backend

build:
	npm run --prefix apps/frontend build

serve:
	npm run --prefix apps/frontend preview -- --port $(PORT)

infra-check:
	cfn-lint infra/foundation/template.yaml \
		infra/backend/template.yaml
	python3 -m unittest infra.test_migrate infra.test_foundation
