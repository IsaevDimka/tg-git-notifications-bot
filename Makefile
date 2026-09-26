.DEFAULT_GOAL := help
.PHONY: help deps test lint check run install build up down restart status logs update backup deploy deploy-env deploy-status deploy-logs deploy-restart

COMPOSE ?= docker compose
TS      := $(shell date +%Y%m%d-%H%M%S)
HOST_ID := $(shell id -u):$(shell id -g)

help: ## Show available commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

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

install: ## First run: ask for the bot token, write .env, start the bot
	@if [ -f .env ]; then \
		echo ".env already exists — keeping it."; \
	else \
		cp .env.example .env && chmod 600 .env; \
		printf "Telegram bot token from @BotFather: "; stty -echo 2>/dev/null; read -r token; stty echo 2>/dev/null; echo; \
		[ -n "$$token" ] || { rm -f .env; echo "No token given — nothing changed."; exit 1; }; \
		sed -i.bak "s|^TELEGRAM_TOKEN=.*|TELEGRAM_TOKEN=$$token|" .env && rm -f .env.bak; \
	fi
	$(COMPOSE) pull || $(COMPOSE) build
	$(COMPOSE) up -d
	@echo "Started. Open your bot in Telegram and send /start — the first user becomes admin."

status: ## Show whether the bot container is running and healthy
	$(COMPOSE) ps

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

# --- remote deploy (ssh + docker compose on DEPLOY_HOST) ------------------------------------

DEPLOY_HOST ?= tg-bot
DEPLOY_USER ?= devops
DEPLOY_DIR  ?= /opt/tg-git-notifications-bot
IMAGE_TAG   ?= latest
SSH         := ssh $(DEPLOY_USER)@$(DEPLOY_HOST)
REMOTE      := cd $(DEPLOY_DIR) && IMAGE_TAG=$(IMAGE_TAG) docker compose

deploy: ## Copy deploy/docker-compose.prod.yml to DEPLOY_HOST, pull IMAGE_TAG and start
	$(SSH) 'mkdir -p $(DEPLOY_DIR)/data && test -f $(DEPLOY_DIR)/.env || { echo "No $(DEPLOY_DIR)/.env — run: make deploy-env"; exit 1; }'
	scp -q deploy/docker-compose.prod.yml $(DEPLOY_USER)@$(DEPLOY_HOST):$(DEPLOY_DIR)/docker-compose.yml
	$(SSH) '$(REMOTE) pull && $(REMOTE) up -d && $(REMOTE) ps'

deploy-env: .env ## Upload local .env to DEPLOY_HOST (mode 0600)
	$(SSH) 'mkdir -p $(DEPLOY_DIR)/data'
	scp -q .env $(DEPLOY_USER)@$(DEPLOY_HOST):$(DEPLOY_DIR)/.env
	$(SSH) 'chmod 600 $(DEPLOY_DIR)/.env'

deploy-status: ## Remote container state and health
	$(SSH) '$(REMOTE) ps'

deploy-logs: ## Follow remote logs
	$(SSH) -t '$(REMOTE) logs -f --tail=100'

deploy-restart: ## Restart the remote bot
	$(SSH) '$(REMOTE) restart'

.env:
	@echo "No .env found. Run: cp .env.example .env  — then set TELEGRAM_TOKEN."; exit 1
