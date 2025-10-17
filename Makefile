# Makefile
.PHONY: help build build-dev test format lint security run run-dev logs stop clean shell db-migrate

# --- Переменные ---
DOCKER_REGISTRY ?= ghcr.io
DOCKER_ORG ?= kootik
DOCKER_IMAGE ?= fztests
DOCKER_TAG ?= latest
FULL_IMAGE_NAME = $(DOCKER_REGISTRY)/$(DOCKER_ORG)/$(DOCKER_IMAGE):$(DOCKER_TAG)

# --- Команды ---

help: ## Показать это справочное сообщение.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}' 

build: ## Build Docker image
	docker build -t $(FULL_IMAGE_NAME) \
		--build-arg BUILD_DATE=`date -u +"%Y-%m-%dT%H:%M:%SZ"` \
		--build-arg VCS_REF=`git rev-parse --short HEAD` \
		--build-arg VERSION=$(DOCKER_TAG) \
		.
build-dev: ## Build development image
	docker build --target tester -t $(DOCKER_IMAGE):dev .


# --- КОМАНДЫ ДЛЯ КАЧЕСТВА КОДА ---
format: build-dev ## Автоматически отформатировать весь Python-код (black, isort).
	docker run --rm -v $(PWD):/app $(DOCKER_IMAGE):dev sh -c "black . && isort ."
lint: build-dev ## Проверить форматирование и качество кода (flake8, black, isort).
	docker run --rm -v $(PWD):/app $(DOCKER_IMAGE):dev sh -c "flake8 app/ && black --check app/ && isort --check-only app/"

test: build-dev ## Запустить Pytest, используя код с хост-машины.
	docker run --rm -v $(PWD):/app $(DOCKER_IMAGE):dev pytest tests/ -v
security: build-dev ## Запустить сканеры безопасности bandit и safety.
	docker run --rm -v $(PWD):/app $(DOCKER_IMAGE):dev sh -c "bandit -r app/ && safety check -r requirements.txt"

# --- КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ КОНТЕЙНЕРАМИ ---
run: ## Запустить приложение с помощью docker-compose (production).
	docker compose up -d --build

run-dev: ## Запустить в режиме разработки (требует docker-compose.dev.yml).
	docker compose -f docker compose.yml -f docker-compose.dev.yml up

logs: ## Показать логи запущенных контейнеров.
	docker compose logs -f

stop: ## Остановить все контейнеры.
	docker compose down

clean: ## Остановить контейнеры и удалить все данные (тома).
	docker compose down -v
	docker system prune -f

push: build ## Отправить production-образ в registry.
	docker push $(FULL_IMAGE_NAME)

deep-clean: ## Удалить все файлы кэша Python и Pytest.
	@find . -type d -name "__pycache__" -exec rm -r {} +
	@rm -rf .pytest_cache
	@echo "All cache files have been removed."

shell: ## Открыть командную оболочку Bash в контейнере 'app'.
	docker compose exec app /bin/bash

# --- КОМАНДЫ ДЛЯ РАБОТЫ С БАЗОЙ ДАННЫХ И REDIS ---
db-migrate: ## Применить миграции базы данных.
	docker compose exec app flask db upgrade

db-shell: ## Открыть консоль psql в контейнере 'postgres'.
	docker compose exec postgres psql -U flask_user -d flask_app

redis-cli: ## Открыть консоль redis-cli в контейнере 'redis'.
	docker compose exec redis redis-cli

backup: ## Создать бэкап базы данных.
	docker compose exec postgres pg_dump -U flask_user flask_app | gzip > backup_`date +%Y%m%d_%H%M%S`.sql.gz

restore: ## Восстановить БД из бэкапа.
	@read -p "Enter backup file name: " backup_file; \
	gunzip < $$backup_file | docker-compose exec -T postgres psql -U flask_user flask_app
