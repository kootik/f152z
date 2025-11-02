#!/bin/bash

# f152z Backup Script
# Version: 2.0

readonly ENV_FILE="${1:-prod.env}"
readonly BACKUP_DIR="${2:-backups}"
readonly RETENTION_DAYS="${3:-30}"

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

echo "===== Резервное копирование f152z ====="

# Создание директории для бэкапов
mkdir -p "$BACKUP_DIR"

# Имя файла бэкапа
timestamp=$(date +%Y-%m-%d_%H-%M-%S)
backup_file="${BACKUP_DIR}/backup_${timestamp}.sql.gz"

# Создание бэкапа базы данных
echo "Создание резервной копии базы данных..."
if $COMPOSER exec -T postgres pg_dump -U flask_user flask_app | gzip > "$backup_file"; then
    echo "✓ Бэкап создан: $backup_file"
    
    # Вывод размера
    size=$(du -h "$backup_file" | cut -f1)
    echo "  Размер: $size"
else
    echo "✗ Ошибка при создании бэкапа"
    exit 1
fi

# Создание бэкапа конфигурации
echo "Создание резервной копии конфигурации..."
config_backup="${BACKUP_DIR}/config_${timestamp}.tar.gz"
tar czf "$config_backup" \
    "$ENV_FILE" \
    docker-compose.yml \
    nginx/ \
    .admin_created \
    2>/dev/null || true

echo "✓ Конфигурация сохранена: $config_backup"

# Очистка старых бэкапов
if [[ "$RETENTION_DAYS" -gt 0 ]]; then
    echo "Удаление бэкапов старше $RETENTION_DAYS дней..."
    find "$BACKUP_DIR" -type f -name "*.gz" -mtime +"$RETENTION_DAYS" -delete
    echo "✓ Старые бэкапы удалены"
fi

echo "===== Резервное копирование завершено ====="
