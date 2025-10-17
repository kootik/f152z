#!/bin/bash
set -e

# --- Переменные ---
# Теперь репозиторий не нужен, но нужно знать имя образа
IMAGE_NAME="ghcr.io/kootik/f152z:refactor-docker-ci"

# --- Функции ---
print_color() {
    COLOR=$1
    TEXT=$2
    NC='\033[0m' # No Color
    case $COLOR in
        "green") echo -e "\033[0;32m${TEXT}${NC}" ;;
        "yellow") echo -e "\033[0;33m${TEXT}${NC}" ;;
        "blue") echo -e "\033[0;34m${TEXT}${NC}" ;;
        *) echo "$TEXT" ;;
    esac
}

# --- Начало скрипта ---
print_color "blue" "===== Автономный скрипт развертывания приложения ====="

# 1. Создание .env файла
if [ -f ".env" ]; then
    print_color "yellow" ".env файл уже существует. Пропускаем создание."
    export $(grep SERVER_NAME .env)
else
    print_color "green" "1/7: Создание .env файла..."
    echo "FLASK_ENV=production" > .env
    read -p "Введите SECRET_KEY: " SECRET_KEY
    echo "SECRET_KEY=${SECRET_KEY}" >> .env
    read -p "Введите пароль для базы данных (DB_PASSWORD): " DB_PASSWORD
    echo "DB_PASSWORD=${DB_PASSWORD}" >> .env
    read -p "Введите разрешенные домены CORS (например, https://domain.com): " CORS_ORIGINS
    echo "CORS_ORIGINS=${CORS_ORIGINS}" >> .env
    read -p "Введите доменное имя сервера (SERVER_NAME): " SERVER_NAME
    echo "SERVER_NAME=${SERVER_NAME}" >> .env
    print_color "green" ".env файл успешно создан."
fi

# 2. Генерация docker-compose.yml
print_color "green" "2/7: Генерация файла docker-compose.yml..."
cat << EOF > docker-compose.yml
services:
  postgres:
    image: postgres:15-alpine
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
    restart: unless-stopped
  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped
  app:
    image: ${IMAGE_NAME}:main # Используем тег 'main' для стабильной версии
    environment:
      FLASK_ENV: production
      SECRET_KEY: \${SECRET_KEY}
      DATABASE_URI: postgresql://flask_user:\${DB_PASSWORD}@postgres/flask_app
      REDIS_URL: redis://redis:6379
      CORS_ORIGINS: \${CORS_ORIGINS}
      SERVER_NAME: \${SERVER_NAME}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf
      - ./nginx/ssl:/etc/nginx/ssl:ro
    depends_on:
      - app
    restart: unless-stopped
volumes:
  postgres_data:
EOF

# --- NEW BLOCK: Create the update.sh script ---
print_color "green" "Creating the update script (update.sh)..."
cat << 'EOF' > update.sh
#!/bin/bash
set -e
# --- Functions ---
print_color() {
    COLOR=$1
    TEXT=$2
    NC='\033[0m' # No Color
    case $COLOR in
        "green") echo -e "\033[0;32m${TEXT}${NC}" ;;
        "blue") echo -e "\033[0;34m${TEXT}${NC}" ;;
        *) echo "$TEXT" ;;
    esac
}
# --- Script Start ---
print_color "blue" "===== f152z Application Update Script ====="
# 1. Pull the new application image
print_color "green" "1/3: Pulling new image..."
docker-compose pull app
# 2. Relaunch the 'app' service with the new image
print_color "green" "2/3: Relaunching 'app' service..."
docker-compose up -d --no-deps app
# 3. Apply database migrations (if any)
print_color "green" "3/3: Applying database migrations..."
docker-compose exec -T app flask db upgrade
print_color "blue" "===== Update complete! ====="
EOF
chmod +x update.sh
# --- END OF NEW BLOCK ---

# 3. Генерация nginx.conf
print_color "green" "3/7: Генерация файла nginx.conf..."
mkdir -p nginx
cat << EOF > nginx/nginx.conf
server {
    listen 80;
    server_name ${SERVER_NAME};
    # Редирект на HTTPS
    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name ${SERVER_NAME};

    ssl_certificate /etc/nginx/ssl/fz152.crt;
    ssl_certificate_key /etc/nginx/ssl/fz152.key;

    location / {
        proxy_pass http://app:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

# 4. Генерация самоподписанного SSL-сертификата
CERT_DIR="nginx/ssl"
KEY_FILE="${CERT_DIR}/fz152.key"
CERT_FILE="${CERT_DIR}/fz152.crt"

if [ -f "$KEY_FILE" ] && [ -f "$CERT_FILE" ]; then
    print_color "yellow" "SSL-сертификаты уже существуют. Пропускаем генерацию."
else
    print_color "green" "4/7: Генерация SSL-сертификата для ${SERVER_NAME}..."
    mkdir -p $CERT_DIR
    openssl req -x509 -nodes -newkey rsa:2048 \
        -keyout "$KEY_FILE" \
        -out "$CERT_FILE" \
        -days 365 \
        -subj "/CN=${SERVER_NAME}"
fi

# 5. Загрузка образов
print_color "green" "5/7: Загрузка Docker-образов..."
docker-compose pull

# 6. Запуск контейнеров
print_color "green" "6/7: Запуск всех сервисов..."
docker-compose up -d

print_color "blue" "Ожидание запуска базы данных..."
sleep 15

# 7. Первичная настройка приложения
print_color "green" "7/7: Первичная настройка (миграции и создание администратора)..."
docker-compose exec -T app flask db upgrade
read -p "Введите email для нового администратора: " ADMIN_EMAIL
read -sp "Введите пароль для нового администратора: " ADMIN_PASSWORDecho
docker-compose exec -T app flask create-admin "$ADMIN_EMAIL" "$ADMIN_PASSWORD"



print_color "blue" "===== Развертывание успешно завершено! ====="
print_color "yellow" "Для последующих обновлений используйте скрипт ./update.sh"
