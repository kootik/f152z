#!/bin/bash

# f152z Monitoring Script
# Version: 2.0

readonly ENV_FILE="${1:-prod.env}"

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

# Проверка, запущен ли скрипт в интерактивном терминале
if ! [ -t 1 ]; then
    echo "Этот скрипт предназначен для запуска в интерактивном терминале."
    exit 1
fi

trap "tput cnorm; exit" INT TERM

tput civis # Скрыть курсор

while true; do
    clear
    echo "===== f152z Monitoring Dashboard (Обновляется каждые 5 секунд. Нажмите Ctrl+C для выхода) ====="
    echo ""

    # Статус сервисов
    echo "📊 Статус сервисов:"
    $COMPOSER ps --format "table {{.Name}}	{{.State}}	{{.Ports}}"
    echo ""

    # Использование ресурсов
    echo "💾 Использование ресурсов:"
    docker stats --no-stream --format "table {{.Container}}	{{.CPUPerc}}	{{.MemUsage}}	{{.NetIO}}" $($COMPOSER ps -q) 2>/dev/null || true
    echo ""

    # Размер volumes
    echo "📁 Размер данных:"
    docker system df -v | grep f152z || true
    echo ""

    # Последние логи с ошибками
    echo "⚠️  Последние ошибки (если есть):"
    $COMPOSER logs --tail=5 2>&1 | grep -E "ERROR|CRITICAL|FATAL|WARN" --color=always || echo "Ошибок не обнаружено"

    sleep 15
done
