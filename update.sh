#!/bin/bash

# f152z Update Script
# Version: 2.0

readonly ENV_FILE="${1:-prod.env}"
readonly BACKUP_BEFORE_UPDATE="${2:-true}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Ошибка: $ENV_FILE не найден!"
    exit 1
fi

# Определяем Docker Compose
if docker compose version &>/dev/null; then
    COMPOSER="docker compose --env-file $ENV_FILE"
elif command -v docker-compose &>/dev/null; then
    COMPOSER="docker-compose --env-file $ENV_FILE"
else
    echo "Ошибка: Docker Compose не найден!"
    exit 1
fi

echo "===== Обновление f152z ====="

# Создание бэкапа
if [[ "$BACKUP_BEFORE_UPDATE" == "true" ]]; then
    echo "Создание резервной копии базы данных..."
    mkdir -p backups
    
    backup_file="backups/backup_$(date +%Y%m%d_%H%M%S).sql.gz"
    
    if $COMPOSER exec -T postgres pg_dump -U flask_user flask_app | gzip > "$backup_file"; then
        echo "✓ Бэкап сохранен в $backup_file"
    else
        echo "⚠ Не удалось создать бэкап. Продолжить? (y/n)"
        read -r continue_update
        if [[ "$continue_update" != "y" ]]; then
            exit 1
        fi
    fi
fi

# Загрузка новой версии
echo "Загрузка обновлений..."
if ! $COMPOSER pull; then
    echo "Ошибка при загрузке образов"
    exit 1
fi

# Перезапуск с новым образом
echo "Перезапуск сервисов..."
if ! $COMPOSER up -d --remove-orphans; then
    echo "Ошибка при запуске сервисов"
    exit 1
fi

# Применение миграций
echo "Применение миграций базы данных..."
if ! $COMPOSER exec -T app flask db upgrade; then
    echo "Ошибка при применении миграций"
    exit 1
fi

# Проверка статуса
sleep 5
if $COMPOSER ps | grep -q "app.*Up"; then
    echo "✓ Обновление успешно завершено!"
else
    echo "✗ Ошибка при запуске приложения"
    echo "Проверьте логи: $COMPOSER logs app"
    exit 1
fi
