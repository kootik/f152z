#!/bin/bash


# =============================================================================
# f152z Deployment Script v2.9 (финальная версия)
# =============================================================================
# Автоматизированный скрипт развертывания веб-приложения f152z
# Требования: Linux-based OS, Docker, Docker Compose, sudo права
# =============================================================================

# --- Константы и конфигурация ---
readonly SCRIPT_VERSION="2.9"
readonly IMAGE_NAME="ghcr.io/kootik/f152z"
readonly IMAGE_TAG="refactor-docker-ci"
readonly ENV_FILE="prod.env"
readonly BACKUP_DIR=".backups"
readonly LOG_FILE="deploy_$(date +%Y%m%d_%H%M%S).log"
readonly REQUIRED_COMMANDS=("openssl" "getent" "id" "curl")

# Флаги для отслеживания состояния
DEPLOYMENT_SUCCESSFUL=false
CLEANUP_NEEDED=false

# --- Утилиты для вывода и логирования ---
print_color() {
    local color=$1
    local text=$2
    local no_newline=${3:-false}
    
    # Коды цветов ANSI
    declare -A colors=(
        ["red"]='\033[0;31m'
        ["green"]='\033[0;32m'
        ["yellow"]='\033[0;33m'
        ["blue"]='\033[0;34m'
        ["magenta"]='\033[0;35m'
        ["cyan"]='\033[0;36m'
    )
    local nc='\033[0m' # No Color
    
    local color_code="${colors[$color]:-$nc}"
    
    if [[ "$no_newline" == "true" ]]; then
        echo -en "${color_code}${text}${nc}" | tee -a "$LOG_FILE"
    else
        echo -e "${color_code}${text}${nc}" | tee -a "$LOG_FILE"
    fi
}

print_header() {
    local title=$1
    local width=70
    local padding=$(( (width - ${#title}) / 2 ))
    
    echo "" | tee -a "$LOG_FILE"
    print_color "cyan" "$(printf '=%.0s' {1..70})"
    print_color "cyan" "$(printf ' %.0s' $(seq 1 $padding))$title"
    print_color "cyan" "$(printf '=%.0s' {1..70})"
    echo "" | tee -a "$LOG_FILE"
}

print_step() {
    local step_num=$1
    local total_steps=$2
    local description=$3
    print_color "blue" "[$step_num/$total_steps] $description"
}

show_spinner() {
    local pid=$1
    local delay=0.1
    local spinstr='|/-\'
    
    while ps -p $pid > /dev/null 2>&1; do
        local temp=${spinstr#?}
        printf " [%c]  " "$spinstr"
        local spinstr=$temp${spinstr%"$temp"}
        sleep $delay
        printf "\b\b\b\b\b\b"
    done
    printf "      \b\b\b\b"
}

# --- Функции обработки ошибок ---
error_exit() {
    print_color "red" "❌ ОШИБКА: $1"
    cleanup_on_error
    exit 1
}

cleanup_on_error() {
    if [[ "$CLEANUP_NEEDED" == "true" ]] && [[ "$DEPLOYMENT_SUCCESSFUL" == "false" ]]; then
        print_color "yellow" "\nВыполняется очистка после ошибки..."
        
        # Останавливаем контейнеры, если они были запущены
        if [[ -f "docker-compose.yml" ]] && command -v docker &>/dev/null; then
            docker compose down --remove-orphans 2>/dev/null || docker-compose down --remove-orphans 2>/dev/null || true
        fi
        
        print_color "yellow" "Очистка завершена."
    fi
}

# Устанавливаем trap для обработки прерываний
trap cleanup_on_error EXIT INT TERM

# --- Функции проверки зависимостей ---
check_sudo() {
    if ! command -v sudo &>/dev/null; then
        print_color "red" "sudo не установлен на этой системе."
        print_color "yellow" "Для продолжения необходимо установить sudo или выполнить скрипт от root."
        return 1
    fi
    
    if ! sudo -n true 2>/dev/null; then
        print_color "yellow" "Требуется ввести пароль sudo для продолжения."
        if ! sudo true; then
            print_color "red" "Не удалось получить права sudo."
            return 1
        fi
    fi
    return 0
}

check_required_commands() {
    local missing_commands=()
    
    for cmd in "${REQUIRED_COMMANDS[@]}"; do
        if ! command -v "$cmd" &>/dev/null; then
            missing_commands+=("$cmd")
        fi
    done
    
    if [[ ${#missing_commands[@]} -gt 0 ]]; then
        print_color "red" "Отсутствуют необходимые команды: ${missing_commands[*]}"
        print_color "yellow" "Установите их перед запуском скрипта."
        return 1
    fi
    return 0
}

detect_os() {
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        OS=$ID
        OS_VERSION=$VERSION_ID
        print_color "green" "Обнаружена ОС: $PRETTY_NAME"
    else
        error_exit "Не удалось определить операционную систему"
    fi
}

check_docker_compose() {
    if command -v docker &>/dev/null && docker compose version &>/dev/null; then
        COMPOSER="docker compose --env-file $ENV_FILE --project-directory ."
        COMPOSE_VERSION=$(docker compose version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
        print_color "green" "✓ Найден docker compose (plugin) версии $COMPOSE_VERSION"
    elif command -v docker-compose &>/dev/null; then
        COMPOSER="docker-compose --env-file $ENV_FILE --project-directory ."
        COMPOSE_VERSION=$(docker-compose --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
        print_color "green" "✓ Найден docker-compose (standalone) версии $COMPOSE_VERSION"
    else
        return 1
    fi
    return 0
}

install_docker_instructions() {
    print_color "red" "Docker или Docker Compose не установлены."
    print_color "yellow" "Инструкции по установке для вашей ОС ($OS):"
    echo ""
    
    case "$OS" in
        ubuntu|debian)
            local codename
            codename=$(. /etc/os-release && echo "$VERSION_CODENAME")

            if [[ -z "$codename" ]]; then
                print_color "red" "Ошибка: не удалось определить кодовое имя дистрибутива (VERSION_CODENAME)."
                print_color "red" "Пожалуйста, проверьте содержимое файла /etc/os-release."
                return 1
            fi
            
            local docker_repo_command="echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${OS} ${codename} stable\" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null"

            cat << EOF
# 1. Обновите список пакетов:
sudo apt-get update
# 2. Установите необходимые пакеты:
sudo apt-get install -y ca-certificates curl gnupg
# 3. Добавьте официальный GPG ключ Docker:
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/${OS}/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
# 4. Добавьте репозиторий Docker (выполните эту команду):
${docker_repo_command}
# 5. Установите Docker Engine и Docker Compose:
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
# 6. Запустите Docker:
sudo systemctl start docker
sudo systemctl enable docker
EOF
            ;;
        centos|rhel|fedora)
            cat << 'EOF'
# 1. Установите необходимые пакеты:
sudo dnf -y install dnf-plugins-core
# 2. Добавьте репозиторий Docker:
sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo
# 3. Установите Docker Engine и Compose:
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
# 4. Запустите и включите Docker:
sudo systemctl start docker
sudo systemctl enable docker
EOF
            ;;
        *)
            print_color "yellow" "Автоматические инструкции для $OS недоступны."
            print_color "yellow" "Посетите https://docs.docker.com/engine/install/ для получения инструкций."
            ;;
    esac
}

# --- Функции настройки Docker ---
setup_docker_permissions() {
    local needs_relog=false
    
    # Проверяем существование группы docker
    if ! getent group docker >/dev/null 2>&1; then
        print_color "yellow" "Создание группы 'docker'..."
        if ! sudo groupadd docker; then
            error_exit "Не удалось создать группу docker"
        fi
        needs_relog=true
    fi
    
    # Проверяем членство пользователя в группе docker
    if ! id -nG "$USER" | grep -qw "docker"; then
        print_color "yellow" "Добавление пользователя '$USER' в группу 'docker'..."
        if ! sudo usermod -aG docker "$USER"; then
            error_exit "Не удалось добавить пользователя в группу docker"
        fi
        needs_relog=true
    fi
    
    if [[ "$needs_relog" == "true" ]]; then
        print_color "red" "╔══════════════════════════════════════════════════════════╗"
        print_color "red" "║                          ВАЖНО!                          ║"
        print_color "red" "║                                                          ║"
        print_color "red" "║ Права доступа к Docker были настроены.                   ║"
        print_color "red" "║ Для вступления изменений в силу необходимо:              ║"
        print_color "red" "║                                                          ║"
        print_color "red" "║ 1. Полностью выйти из системы (logout)                   ║"
        print_color "red" "║ 2. Войти заново                                          ║"
        print_color "red" "║ 3. Запустить скрипт повторно                             ║"
        print_color "red" "║                                                          ║"
        print_color "red" "╚══════════════════════════════════════════════════════════╝"
        exit 0
    fi
    
    print_color "green" "✓ Права доступа к Docker настроены корректно"
}

# --- Функции для работы с вводом пользователя ---
read_required_input() {
    local prompt=$1
    local var_name=$2
    local is_password=${3:-false}
    local value=""
    
    while [[ -z "$value" ]]; do
        if [[ "$is_password" == "true" ]]; then
            read -rsp "$prompt: " value
            echo "" # Новая строка после скрытого ввода
            
            # Для паролей требуем подтверждение
            local confirm=""
            read -rsp "Подтвердите пароль: " confirm
            echo ""
            
            if [[ "$value" != "$confirm" ]]; then
                print_color "red" "Пароли не совпадают. Попробуйте снова."
                value=""
                continue
            fi
        else
            read -rp "$prompt: " value
        fi
        
        if [[ -z "$value" ]]; then
            print_color "yellow" "⚠ Это поле обязательно для заполнения. Попробуйте снова."
        fi
    done
    
    # Используем declare -g для создания глобальной переменной из функции
    declare -g "$var_name=$value"
}

generate_secret_key() {
    # Генерируем криптографически стойкий случайный ключ
    openssl rand -hex 32
}

# --- Функции создания конфигураций ---
backup_existing_file() {
    local file=$1
    if [[ -f "$file" ]]; then
        mkdir -p "$BACKUP_DIR"
        local backup_name="${BACKUP_DIR}/$(basename "$file").$(date +%Y%m%d_%H%M%S).bak"
        cp "$file" "$backup_name"
        print_color "yellow" "Существующий файл $file сохранен в $backup_name"
    fi
}

create_env_file() {
    if [[ -f "$ENV_FILE" ]]; then
        print_color "yellow" "Файл $ENV_FILE уже существует."
        read -rp "Перезаписать? (y/N): " overwrite
        
        if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
            print_color "blue" "Используется существующий $ENV_FILE"
            # Загружаем существующие переменные
            set -a
            source "$ENV_FILE"
            set +a
            return 0
        fi
        
        backup_existing_file "$ENV_FILE"
    fi
    
    print_color "green" "Создание файла конфигурации окружения..."
    
    # Генерируем SECRET_KEY автоматически
    local secret_key
    secret_key=$(generate_secret_key)
    print_color "green" "✓ SECRET_KEY сгенерирован автоматически"
    
    # Запрашиваем остальные параметры
    read_required_input "Введите пароль для базы данных" "db_password" true
    read_required_input "Введите доменное имя сервера (например: example.com)" "server_name" false
    read_required_input "Введите разрешенные домены CORS (например: https://example.com)" "cors_origins" false
    
    # Создаем файл с правильными правами доступа
    (
        umask 0177
        cat > "$ENV_FILE" << EOF
# Автоматически сгенерированный файл конфигурации
# Создан: $(date)
# НЕ КОММИТЬТЕ ЭТОТ ФАЙЛ В GIT!

FLASK_ENV=production
SECRET_KEY=${secret_key}
DB_PASSWORD=${db_password}
SERVER_NAME=${server_name}
CORS_ORIGINS=${cors_origins}
EOF
    )
    
    print_color "green" "✓ Файл $ENV_FILE успешно создан с правами 600"
    
    # Загружаем переменные окружения
    set -a
    source "$ENV_FILE"
    set +a
}

generate_docker_compose() {
    backup_existing_file "docker-compose.yml"
    
    print_color "green" "Генерация docker-compose.yml..."
    
    cat > docker-compose.yml << EOF

services:
  postgres:
    image: postgres:15-alpine
    container_name: f152z_postgres
    environment:
      POSTGRES_DB: flask_app
      POSTGRES_USER: flask_user
      POSTGRES_PASSWORD: \${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U flask_user -d flask_app"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s
    restart: unless-stopped
    networks:
      - f152z_network

  redis:
    image: redis:7-alpine
    container_name: f152z_redis
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s
    restart: unless-stopped
    networks:
      - f152z_network

  app:
    image: ${IMAGE_NAME}:${IMAGE_TAG}
    container_name: f152z_app
    environment:
      FLASK_ENV: production
      SECRET_KEY: \${SECRET_KEY}
      DATABASE_URI: 'postgresql://flask_user:\${DB_PASSWORD}@postgres/flask_app'
      REDIS_URL: 'redis://redis:6379'
      CORS_ORIGINS: \${CORS_ORIGINS}
      SERVER_NAME: \${SERVER_NAME}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped
    networks:
      - f152z_network

  nginx:
    image: nginx:alpine
    container_name: f152z_nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro
    depends_on:
      - app
    restart: unless-stopped
    networks:
      - f152z_network

volumes:
  postgres_data:
    name: f152z_postgres_data

networks:
  f152z_network:
    name: f152z_network
    driver: bridge
EOF
    print_color "green" "✓ docker-compose.yml успешно создан"
}

generate_nginx_config() {
    mkdir -p nginx
    backup_existing_file "nginx/nginx.conf"
    
    print_color "green" "Генерация конфигурации Nginx..."
    
    cat > nginx/nginx.conf << EOF
# Nginx configuration for f152z
# Generated: $(date)

# Redirect HTTP to HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name ${SERVER_NAME};
    
    # Allow Let's Encrypt validation
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    
    # Redirect all other requests to HTTPS
    location / {
        return 301 https://\$host\$request_uri;
    }
}

# HTTPS Server
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${SERVER_NAME};
    
    # SSL Configuration
    ssl_certificate /etc/nginx/ssl/fz152.crt;
    ssl_certificate_key /etc/nginx/ssl/fz152.key;
    
    # Modern SSL configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-CHACHA20-POLY1305;
    ssl_prefer_server_ciphers off;
    
    # SSL session caching
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:50m;
    ssl_session_tickets off;
    
    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # Main application proxy
    location / {
        proxy_pass http://app:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # Proxy timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    # WebSocket support for Socket.IO
    location /socket.io {
        proxy_pass http://app:8000/socket.io;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        
        # WebSocket timeouts
        proxy_connect_timeout 7d;
        proxy_send_timeout 7d;
        proxy_read_timeout 7d;
    }
    
    # Static files caching (adjust path as needed)
    location /static {
        proxy_pass http://app:8000/static;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
EOF
    
    print_color "green" "✓ Конфигурация Nginx успешно создана"
}

setup_ssl_certificates() {
    local cert_dir="nginx/ssl"
    mkdir -p "$cert_dir"
    
    if [[ -f "${cert_dir}/fz152.key" ]] && [[ -f "${cert_dir}/fz152.crt" ]]; then
        print_color "yellow" "SSL сертификаты уже существуют"
        read -rp "Перегенерировать? (y/N): " regenerate
        
        if [[ ! "$regenerate" =~ ^[Yy]$ ]]; then
            return 0
        fi
        
        backup_existing_file "${cert_dir}/fz152.key"
        backup_existing_file "${cert_dir}/fz152.crt"
    fi
    
    print_color "yellow" "⚠ Генерация самоподписанного SSL сертификата"
    print_color "yellow" "  Для production окружения настоятельно рекомендуется использовать Let's Encrypt"
    
    openssl req -x509 -nodes -newkey rsa:2048 \
        -keyout "${cert_dir}/fz152.key" \
        -out "${cert_dir}/fz152.crt" \
        -days 365 \
        -subj "/C=RU/ST=Moscow/L=Moscow/O=fz152/OU=IT/CN=${SERVER_NAME}" \
        2>/dev/null
    
    # Устанавливаем правильные права доступа
    chmod 600 "${cert_dir}/fz152.key"
    chmod 644 "${cert_dir}/fz152.crt"
    
    print_color "green" "✓ SSL сертификаты успешно созданы"
}

# --- Функции развертывания ---
pull_docker_images() {
    print_color "green" "Загрузка Docker образов..." true
    ($COMPOSER pull 2>&1 | tee -a "$LOG_FILE") &
    local pid=$!
    show_spinner $pid
    wait $pid
    local exit_code=$?
    echo
    if [[ $exit_code -ne 0 ]]; then
        error_exit "Не удалось загрузить Docker образы. Проверьте лог: $LOG_FILE"
    fi
    
    print_color "green" "✓ Docker образы успешно загружены"
}

start_services() {
    print_color "green" "Запуск сервисов..." true
    CLEANUP_NEEDED=true
    ($COMPOSER up -d --remove-orphans 2>&1 | tee -a "$LOG_FILE") &
    local pid=$!
    show_spinner $pid
    wait $pid
    local exit_code=$?
    echo
    if [[ $exit_code -ne 0 ]]; then
        error_exit "Не удалось запустить сервисы. Проверьте лог: $LOG_FILE или 'make logs'"
    fi
    
    print_color "green" "✓ Все сервисы успешно запущены"
}

wait_for_database() {
    print_color "blue" "Ожидание готовности базы данных..."
    
    local max_attempts=30
    local attempt=0
    
    while [ $attempt -lt $max_attempts ]; do
        if $COMPOSER exec -T postgres pg_isready -U flask_user -d flask_app >/dev/null 2>&1; then
            printf "\r\033[K" 
            print_color "green" "✓ База данных готова к работе"
            return 0
        fi
        
        attempt=$((attempt + 1))
        printf "\r\033[K\033[0;33m  Ожидание... Попытка %d из %d\033[0m" "$attempt" "$max_attempts"
        sleep 2
    done
    
    echo 
    error_exit "База данных не готова после $max_attempts попыток"
}

initialize_application() {
    print_color "green" "Инициализация приложения..."
    
    # Применяем миграции
    print_color "blue" "Применение миграций базы данных..."
    if ! $COMPOSER exec -T app flask db upgrade 2>&1 | tee -a "$LOG_FILE"; then
        error_exit "Не удалось применить миграции базы данных"
    fi
    print_color "green" "✓ Миграции успешно применены"

    # Идемпотентное создание администратора
    local admin_flag_file=".admin_created"
    
    if [[ -f "$admin_flag_file" ]]; then
        # Загружаем email администратора из файла для итогового отчета
        declare -g admin_email
        admin_email=$(cat "$admin_flag_file")
        print_color "yellow" "✓ Учетная запись администратора ($admin_email) была создана ранее. Пропуск шага."
        return 0
    fi
    
    # Создаем администратора, если это первый запуск
    print_color "blue" "Создание учетной записи администратора..."
    read_required_input "Введите email администратора" "admin_email" false
    read_required_input "Введите пароль администратора" "admin_password" true
    
    # ВНИМАНИЕ: Пароль передается в командной строке и может быть кратковременно виден
    # в списке системных процессов. Это приемлемый риск для большинства сред,
    # но его следует учитывать на многопользовательских хостах.
    if ! $COMPOSER exec -T app flask create-admin "$admin_email" "$admin_password" 2>&1 | tee -a "$LOG_FILE"; then
        print_color "yellow" "⚠ Не удалось создать администратора (возможно, он уже существует в базе данных)."
    else
        print_color "green" "✓ Учетная запись администратора создана."
    fi

    # Создаем файл-флаг, чтобы этот шаг не выполнялся повторно
    echo "$admin_email" > "$admin_flag_file"
    print_color "green" "✓ Флаг '${admin_flag_file}' создан для предотвращения повторного создания администратора."
}

# --- Функции создания вспомогательных скриптов ---
create_utility_scripts() {
    print_color "green" "Создание вспомогательных скриптов..."
    
    # Создаем update.sh
    cat > update.sh << 'EOF'
#!/bin/bash
set -euo pipefail

# Скрипт обновления f152z
# Использование: ./update.sh [--backup]

ENV_FILE="prod.env"
CREATE_BACKUP=false

# Проверка аргументов
if [[ "${1:-}" == "--backup" ]]; then
    CREATE_BACKUP=true
fi

# Проверка наличия файла окружения
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ Ошибка: файл $ENV_FILE не найден!"
    exit 1
fi

# Надёжное определение команды Docker Compose с правильным приоритетом
if docker compose version &>/dev/null; then
    COMPOSER="docker compose --env-file $ENV_FILE"
elif command -v docker-compose &>/dev/null; then
    COMPOSER="docker-compose --env-file $ENV_FILE"
else
    echo "❌ Ошибка: не удалось найти 'docker compose' или 'docker-compose'!"
    exit 1
fi

echo "===== Обновление f152z ====="

# Создание резервной копии при необходимости
if [ "$CREATE_BACKUP" = true ]; then
    echo "📦 Создание резервной копии базы данных..."
    $COMPOSER exec -T postgres pg_dump -U flask_user flask_app | gzip > "backup_pre_update_$(date +%Y%m%d_%H%M%S).sql.gz"
    echo "✓ Резервная копия создана"
fi

# Обновление
echo "🔄 Загрузка новой версии приложения..."
$COMPOSER pull app

echo "🚀 Перезапуск приложения..."
$COMPOSER up -d --no-deps app

echo "🔧 Применение миграций базы данных..."
$COMPOSER exec -T app flask db upgrade

echo "✅ Обновление успешно завершено!"
echo ""
echo "Проверьте работу приложения:"
echo "  • Просмотр логов: $COMPOSER logs -f app"
echo "  • Статус сервисов: $COMPOSER ps"
EOF
    chmod +x update.sh
    
    # Создаем расширенный Makefile
    cat > Makefile << 'EOF'
# Makefile для управления проектом f152z
# Использование: make <команда>

# ИСПРАВЛЕНО: Явно указываем BASH как оболочку для make.
# Это решает проблемы с парсингом 'docker compose' в некоторых системах.
SHELL := /bin/bash
# Переменные
ENV_FILE := prod.env
BACKUP_DIR := .backups
LOG_DIR := logs

# Надёжное определение команды Docker Compose с приоритетом для плагина.
# Сначала пытаемся найти плагин 'docker compose'.
COMPOSE_V2 := $(shell docker compose version &>/dev/null && echo "docker compose")
# Затем, как резервный вариант, ищем 'docker-compose'.
COMPOSE_V1 := $(shell command -v docker-compose 2>/dev/null)

# Используем 'docker compose' (V2), если он доступен, иначе 'docker-compose' (V1).
COMPOSER_CMD := $(or $(COMPOSE_V2),$(COMPOSE_V1))

# Если ни одна команда не найдена, прерываем с ошибкой.
ifeq ($(COMPOSER_CMD),)
    $(error "Не удалось найти 'docker compose' или 'docker-compose'. Проверьте вашу установку Docker.")
endif

COMPOSE = $(COMPOSER_CMD) --env-file $(ENV_FILE)

# Цвета для вывода
RED := \033[0;31m
GREEN := \033[0;32m
YELLOW := \033[0;33m
BLUE := \033[0;34m
NC := \033[0m # No Color

# --- Основные команды ---
.DEFAULT_GOAL := help

.PHONY: help
help: ## Показать это справочное сообщение
	@echo -e "$(BLUE)f152z Management Commands$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "$(GREEN)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo -e "$(YELLOW)Примеры использования:$(NC)"
	@echo "  make up       - Запустить все сервисы"
	@echo "  make logs     - Просмотреть логи"
	@echo "  make backup   - Создать резервную копию"

.PHONY: up
up: ## Запустить все сервисы
	@echo -e "$(GREEN)Запуск сервисов...$(NC)"
	@$(COMPOSE) up -d
	@echo -e "$(GREEN)✓ Сервисы запущены$(NC)"

.PHONY: down
down: ## Остановить все сервисы
	@echo -e "$(YELLOW)Остановка сервисов...$(NC)"
	@$(COMPOSE) down
	@echo -e "$(GREEN)✓ Сервисы остановлены$(NC)"

.PHONY: restart
restart: down up ## Перезапустить все сервисы

.PHONY: status
status: ## Показать статус сервисов
	@$(COMPOSE) ps

.PHONY: logs
logs: ## Показать логи всех сервисов
	@$(COMPOSE) logs -f

.PHONY: logs-app
logs-app: ## Показать логи приложения
	@$(COMPOSE) logs -f app

.PHONY: logs-nginx
logs-nginx: ## Показать логи nginx
	@$(COMPOSE) logs -f nginx

# --- Управление данными ---
.PHONY: backup
backup: ## Создать резервную копию БД
	@mkdir -p $(BACKUP_DIR)
	@echo -e "$(BLUE)Создание резервной копии...$(NC)"
	@FILENAME="$(BACKUP_DIR)/backup_$$(date +%Y%m%d_%H%M%S).sql.gz"; \
	$(COMPOSE) exec -T postgres pg_dump -U flask_user flask_app | gzip > $$FILENAME; \
	echo -e "$(GREEN)✓ Резервная копия сохранена: $$FILENAME$(NC)"

.PHONY: restore
restore: ## Восстановить БД из резервной копии
	@echo -e "$(YELLOW)Доступные резервные копии:$(NC)"
	@ls -1 $(BACKUP_DIR)/*.sql.gz 2>/dev/null || echo "Нет доступных резервных копий"
	@read -p "Введите имя файла для восстановления: " backup_file; \
	if [ -f "$$backup_file" ]; then \
		echo -e "$(BLUE)Восстановление из $$backup_file...$(NC)"; \
		gunzip < $$backup_file | $(COMPOSE) exec -T postgres psql -U flask_user -d flask_app; \
		echo -e "$(GREEN)✓ Восстановление завершено$(NC)"; \
	else \
		echo -e "$(RED)❌ Файл $$backup_file не найден!$(NC)"; \
	fi

.PHONY: clean-backups
clean-backups: ## Удалить старые резервные копии (старше 30 дней)
	@echo -e "$(YELLOW)Удаление старых резервных копий...$(NC)"
	@find $(BACKUP_DIR) -name "*.sql.gz" -mtime +30 -delete
	@echo -e "$(GREEN)✓ Очистка завершена$(NC)"

# --- Отладка и диагностика ---
.PHONY: shell
shell: ## Открыть shell в контейнере приложения
	@$(COMPOSE) exec app /bin/bash

.PHONY: shell-db
shell-db: ## Открыть psql консоль
	@$(COMPOSE) exec postgres psql -U flask_user -d flask_app

.PHONY: shell-redis
shell-redis: ## Открыть redis-cli консоль
	@$(COMPOSE) exec redis redis-cli

.PHONY: test-health
test-health: ## Проверить здоровье всех сервисов
	@echo -e "$(BLUE)Проверка состояния сервисов...$(NC)"
	@$(COMPOSE) ps --format json | python3 -c "import sys, json; data = json.load(sys.stdin); [print(f\"{s['Service']}: {'✓ Healthy' if s.get('Health', '') == 'healthy' else '✗ ' + s.get('State', 'Unknown')}\") for s in data]" 2>/dev/null || $(COMPOSE) ps

.PHONY: stats
stats: ## Показать статистику использования ресурсов
	@docker stats --no-stream $$($(COMPOSE) ps -q)

# --- Обслуживание ---
.PHONY: update
update: ## Обновить приложение до последней версии
	@./update.sh --backup

.PHONY: prune
prune: ## Очистить неиспользуемые Docker ресурсы
	@echo -e "$(YELLOW)Очистка неиспользуемых Docker ресурсов...$(NC)"
	@docker system prune -af --volumes
	@echo -e "$(GREEN)✓ Очистка завершена$(NC)"

.PHONY: validate
validate: ## Проверить корректность конфигурации
	@echo -e "$(BLUE)Проверка конфигурации...$(NC)"
	@$(COMPOSE) config --quiet && echo -e "$(GREEN)✓ Конфигурация корректна$(NC)" || echo -e "$(RED)❌ Ошибка в конфигурации$(NC)"

# --- Опасные операции ---
.PHONY: destroy
destroy: ## ⚠️  Полностью удалить все данные и контейнеры
	@echo -e "$(RED)⚠️  ВНИМАНИЕ! Эта операция удалит ВСЕ данные!$(NC)"
	@read -p "Введите 'yes' для подтверждения: " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		$(COMPOSE) down -v; \
		echo -e "$(RED)Все данные удалены$(NC)"; \
	else \
		echo -e "$(GREEN)Операция отменена$(NC)"; \
	fi
EOF
    
    print_color "green" "✓ Вспомогательные скрипты созданы"
}

# --- Функция вывода итоговой информации ---
show_deployment_summary() {
    local public_ip
    public_ip=$(curl -s https://api.ipify.org 2>/dev/null || echo "не определен")
    
    print_header "РАЗВЕРТЫВАНИЕ УСПЕШНО ЗАВЕРШЕНО!"
    
    cat << EOF | tee -a "$LOG_FILE"
$(print_color "green" "✅ Система f152z успешно развернута и готова к работе")

$(print_color "cyan" "📊 Информация о развертывании:")
  • Версия скрипта: v${SCRIPT_VERSION}
  • Домен: ${SERVER_NAME}
  • URL: https://${SERVER_NAME}
  • IP сервера: ${public_ip}
  • Лог развертывания: ${LOG_FILE}

$(print_color "cyan" "🔐 Учетные данные:")
  • Email администратора: ${admin_email}
  • Пароль администратора НЕ СОХРАНЕН. Используйте тот, что ввели при установке.
  • Пароль базы данных сохранен в ${ENV_FILE}

$(print_color "cyan" "📝 Полезные команды:")
  • Просмотр статуса:     make status
  • Просмотр логов:       make logs
  • Создать бэкап:        make backup
  • Остановить систему:   make down
  • Перезапустить:        make restart
  • Все команды:          make help

$(print_color "yellow" "⚠️  Важные замечания:")
  1. Используется самоподписанный SSL сертификат
     Рекомендуется настроить Let's Encrypt для production
  
  2. Файл ${ENV_FILE} содержит критически важные данные
     НЕ коммитьте его в систему контроля версий
  
  3. Регулярно создавайте резервные копии:
     make backup

$(print_color "green" "🚀 Система готова к использованию!")
EOF
    
    DEPLOYMENT_SUCCESSFUL=true
}

# --- Главная функция ---
main() {
    # Инициализация
    print_header "f152z Deployment Script v${SCRIPT_VERSION}"
    print_color "blue" "Начало развертывания: $(date)"
    echo ""
    
    local total_steps=10
    local current_step=0
    
    # Шаг 1: Проверка зависимостей
    ((current_step++))
    print_step $current_step $total_steps "Проверка системных требований"
    detect_os
    check_required_commands || error_exit "Отсутствуют необходимые системные команды"
    check_sudo || error_exit "Требуются права sudo для продолжения"
    
    # Шаг 2: Проверка и установка Docker
    ((current_step++))
    print_step $current_step $total_steps "Проверка Docker и Docker Compose"
    if ! check_docker_compose; then
        install_docker_instructions
        error_exit "Установите Docker и запустите скрипт снова"
    fi
    
    # Шаг 3: Настройка прав Docker
    ((current_step++))
    print_step $current_step $total_steps "Настройка прав доступа к Docker"
    setup_docker_permissions
    
    # Шаг 4: Создание конфигурации
    ((current_step++))
    print_step $current_step $total_steps "Создание файла конфигурации"
    create_env_file
    
    # Шаг 5: Генерация docker-compose.yml
    ((current_step++))
    print_step $current_step $total_steps "Генерация docker-compose.yml"
    generate_docker_compose
    
    # Шаг 6: Настройка Nginx
    ((current_step++))
    print_step $current_step $total_steps "Настройка веб-сервера Nginx"
    generate_nginx_config
    
    # Шаг 7: SSL сертификаты
    ((current_step++))
    print_step $current_step $total_steps "Настройка SSL сертификатов"
    setup_ssl_certificates
    
    # Шаг 8: Загрузка образов и запуск
    ((current_step++))
    print_step $current_step $total_steps "Загрузка Docker образов и запуск сервисов"
    pull_docker_images
    start_services
    
    # Шаг 9: Инициализация приложения
    ((current_step++))
    print_step $current_step $total_steps "Инициализация приложения"
    wait_for_database
    initialize_application
    
    # Шаг 10: Создание вспомогательных скриптов
    ((current_step++))
    print_step $current_step $total_steps "Создание вспомогательных скриптов"
    create_utility_scripts
    
    # Вывод итоговой информации
    show_deployment_summary
}

# --- Точка входа ---
main "$@"
