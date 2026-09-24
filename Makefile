.DEFAULT_GOAL := help
.PHONY: help deps test lint check run build up down restart logs update backup

COMPOSE ?= docker compose
TS      := $(shell date +%Y%m%d-%H%M%S)
HOST_ID := $(shell id -u):$(shell id -g)

help: ## Show available commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  \033[36m%-9s\033[0m %s\n", $$1, $$2}'

# --- development --------------------------------------------------------------------------

deps: ## Install dependencies with uv
	uv sync

test: ## Run the test suite
	uv run pytest -q

lint: ## Run ruff
	uv run ruff check .

check: lint test ## Lint + tests

run: .env ## Run the bot locally (reads .env, data in ./data unless DATA_DIR is set)
	set -a; . ./.env; set +a; DATA_DIR=$${DATA_DIR:-./data} uv run python -m app

# --- server (docker compose) --------------------------------------------------------------

build: ## Build the image from source
	$(COMPOSE) build

up: .env ## Start the bot in the background
	$(COMPOSE) up -d

down: ## Stop the bot
	$(COMPOSE) down

restart: ## Restart the bot
	$(COMPOSE) restart

logs: ## Follow the logs
	$(COMPOSE) logs -f --tail=100

update: .env ## Pull the latest image (or build from source if unpublished) and restart
	$(COMPOSE) pull || $(COMPOSE) build
	$(COMPOSE) up -d

backup: .env ## Save bot.db + secret.key to ./backups (safe while running)
	mkdir -p backups
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR)/backups:/backup" --entrypoint sh bot \
		-c "python -m app.backup /backup/data-$(TS).tar.gz && chown $(HOST_ID) /backup/data-$(TS).tar.gz"
	@echo "Keep backups/data-$(TS).tar.gz private: it contains the key that decrypts all stored tokens."

.env:
	@echo "No .env found. Run: cp .env.example .env  — then set TELEGRAM_TOKEN."; exit 1
