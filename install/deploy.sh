#!/bin/bash


# ============================================================================
# f152z Deployment Script - Enterprise Edition (Security Hardened)
# Version: 5.0 (Полностью переработанная версия)
# ============================================================================

readonly SCRIPT_VERSION="5.0"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly IMAGE_NAME="ghcr.io/kootik/f152z"
readonly IMAGE_TAG="${F152Z_IMAGE_TAG:-refactor-docker-ci}"
readonly ENV_FILE="${F152Z_ENV_FILE:-prod.env}"
readonly BACKUP_DIR="${F152Z_BACKUP_DIR:-.backups}"
readonly LOG_DIR="${F152Z_LOG_DIR:-.logs}"
readonly LOG_FILE="${LOG_DIR}/deploy_$(date +%Y%m%d_%H%M%S).log"
readonly REQUIRED_COMMANDS=("openssl" "getent" "id" "curl" "mktemp")
readonly MIN_DOCKER_VERSION="20.10.0"
readonly MIN_COMPOSE_VERSION="2.0.0"

# Configuration
DEPLOYMENT_SUCCESSFUL=false
CLEANUP_NEEDED=false
INTERACTIVE_MODE="${F152Z_INTERACTIVE:-true}"
DRY_RUN="${F152Z_DRY_RUN:-false}"
USE_LETSENCRYPT="${F152Z_USE_LETSENCRYPT:-false}"
LETSENCRYPT_EMAIL="${F152Z_LETSENCRYPT_EMAIL:-}"
PROCEED_WITH_DEPLOYMENT=false
AUTO_INSTALL_DOCKER="false"

# ============================================================================
# Utility Functions
# ============================================================================

setup_logging() {
    mkdir -p "$LOG_DIR"
    exec 1> >(tee -a "$LOG_FILE")
    exec 2>&1
}

print_color() {
    local color=$1
    local text=$2
    local no_newline=${3:-false}

    declare -A colors=(
        ["red"]='\033[0;31m'
        ["green"]='\033[0;32m'
        ["yellow"]='\033[0;33m'
        ["blue"]='\033[0;34m'
        ["cyan"]='\033[0;36m'
        ["magenta"]='\033[0;35m'
    )

    local nc='\033[0m'
    local color_code="${colors[$color]:-$nc}"

    if [[ "$no_newline" == "true" ]]; then
        echo -en "${color_code}${text}${nc}"
    else
        echo -e "${color_code}${text}${nc}"
    fi
}

print_header() {
    local title=$1
    local width=70
    local padding=$(( (width - ${#title}) / 2 ))

    echo ""
    print_color "cyan" "$(printf '=%.0s' {1..70})"
    print_color "cyan" "$(printf ' %.0s' $(seq 1 $padding))$title"
    print_color "cyan" "$(printf '=%.0s' {1..70})"
    echo ""
}

print_step() {
    local current=$1
    local total=$2
    local description=$3

    print_color "blue" "[$current/$total] $description"
}

show_spinner() {
    local pid=$1
    local delay=0.1
    local spinstr='⣾⣽⣻⢿⡿⣟⣯⣷'

    while ps -p "$pid" > /dev/null 2>&1; do
        local temp=${spinstr#?}
        printf " [%c]   " "$spinstr"
        spinstr=$temp${spinstr%"$temp"}
        sleep $delay
        printf "\b\b\b\b\b\b"
    done

    printf "       \b\b\b\b"
}

error_exit() {
    print_color "red" "✗ ОШИБКА: $1"
    cleanup_on_error
    exit 1
}

cleanup_on_error() {
    if [[ "$CLEANUP_NEEDED" == "true" ]] && [[ "$DEPLOYMENT_SUCCESSFUL" == "false" ]]; then
        print_color "yellow" "\nВыполняется очистка после ошибки..."

        if [[ -f "docker-compose.yml" ]] && command -v docker &>/dev/null; then
            docker compose down --remove-orphans 2>/dev/null || \
            docker-compose down --remove-orphans 2>/dev/null || true
        fi

        print_color "yellow" "Очистка завершена."
    fi
}

trap cleanup_on_error EXIT INT TERM

# ============================================================================
# Help and Usage
# ============================================================================

show_usage_and_exit() {
    print_header "f152z Deployment Script v${SCRIPT_VERSION}"

    print_color "cyan" "Этот скрипт автоматизирует развертывание приложения f152z."
    echo ""

    print_color "yellow" "Режимы запуска:"
    echo "  1. Интерактивный режим (рекомендуется для первой установки):"
    print_color "green" "     $0 --start"
    echo "     Скрипт задаст все необходимые вопросы (домен, пароли и т.д.)."
    echo ""
    echo "  2. Автоматический режим (для CI/CD и скриптов):"
    print_color "green" "     $0 --non-interactive"
    echo "     Все параметры должны быть заданы через переменные окружения."
    echo ""

    print_color "yellow" "Основные опции:"
    echo "  --start             ▶️  Запустить интерактивную установку."
    echo "  --install-docker     🐳 Автоматически установить Docker, если он отсутствует."
    echo "  --non-interactive   🤖 Запустить в неинтерактивном (автоматическом) режиме."
    echo "  --dry-run           🔬 Тестовый запуск без реального применения изменений."
    echo "  --use-letsencrypt   🔒 Использовать Let's Encrypt для получения SSL-сертификата."
    echo "  --help              ❓ Показать эту справку и выйти."
    echo ""

    print_color "yellow" "Переменные окружения для автоматического режима:"
    echo "  F152Z_INTERACTIVE=false"
    echo "  F152Z_DB_PASSWORD=..."
    echo "  F152Z_SERVER_NAME=..."
    echo "  F152Z_CORS_ORIGINS=..."
    echo "  F152Z_ADMIN_EMAIL=..."
    echo "  F152Z_ADMIN_PASSWORD=..."
    echo "  F152Z_USE_LETSENCRYPT=true"
    echo "  F152Z_LETSENCRYPT_EMAIL=..."
    echo ""
    exit 0
}

# ============================================================================
# Validation Functions
# ============================================================================

validate_email() {
    local email="$1"
    local regex="^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"

    if [[ ! "$email" =~ $regex ]]; then
        print_color "red" "Некорректный формат email: $email"
        return 1
    fi

    return 0
}

validate_domain() {
    local domain="$1"
    local regex="^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"

    # Также разрешаем localhost и IP-адреса для разработки
    if [[ "$domain" == "localhost" ]] || [[ "$domain" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        return 0
    fi

    if [[ ! "$domain" =~ $regex ]]; then
        print_color "red" "Некорректный формат домена: $domain"
        return 1
    fi

    return 0
}

validate_password_strength() {
    local password="$1"
    local min_length=12

    if [[ ${#password} -lt $min_length ]]; then
        print_color "red" "Пароль должен быть не менее $min_length символов"
        return 1
    fi

    if ! [[ "$password" =~ [A-Z] && "$password" =~ [a-z] && "$password" =~ [0-9] ]]; then
        print_color "red" "Пароль должен содержать заглавные, строчные буквы и цифры"
        return 1
    fi

    # Проверка на общие слабые пароли
    local weak_passwords=("password" "12345678" "qwerty" "admin")
    local lower_password=$(echo "$password" | tr '[:upper:]' '[:lower:]')

    for weak in "${weak_passwords[@]}"; do
        if [[ "$lower_password" == *"$weak"* ]]; then
            print_color "red" "Пароль содержит слабую комбинацию: $weak"
            return 1
        fi
    done

    return 0
}

# ============================================================================
# Security Functions
# ============================================================================

generate_secure_password() {
    local length="${1:-20}"
    openssl rand -base64 "$((length * 3 / 4))" | tr -d '\n' | head -c "$length"
}

generate_secret_key() {
    openssl rand -hex 32
}

secure_file_permissions() {
    local file="$1"
    local perms="${2:-600}"

    if [[ ! -f "$file" ]]; then
        return 0
    fi

    chmod "$perms" "$file" || {
        print_color "yellow" "Предупреждение: не удалось установить права $perms для $file"
        return 1
    }

    # Проверяем, что права установились корректно (кроссплатформенно)
    local actual_perms
    if stat --version &>/dev/null; then
        # GNU stat (Linux)
        actual_perms=$(stat -c %a "$file" 2>/dev/null)
    else
        # BSD stat (macOS)
        actual_perms=$(stat -f %A "$file" 2>/dev/null)
    fi

    if [[ "$actual_perms" != "$perms" ]]; then
        print_color "yellow" "Предупреждение: фактические права $actual_perms отличаются от запрошенных $perms для $file"
    fi
}

atomic_write() {
    local target_file="$1"
    local content="$2"
    local perms="${3:-644}"
    local temp_file

    temp_file=$(mktemp "${target_file}.XXXXXX") || {
        error_exit "Не удалось создать временный файл для $target_file"
    }

    # Записываем во временный файл
    echo -e "$content" > "$temp_file"

    # Устанавливаем права
    chmod "$perms" "$temp_file"

    # Атомарно перемещаем на место
    mv -f "$temp_file" "$target_file"
}

# ============================================================================
# System Check Functions
# ============================================================================

check_sudo() {
    if [[ "$EUID" -eq 0 ]]; then
        print_color "yellow" "Скрипт запущен от root. Рекомендуется запускать от обычного пользователя с sudo."
        return 0
    fi

    if ! command -v sudo &>/dev/null; then
        print_color "red" "sudo не установлен."
        return 1
    fi

    if ! sudo -n true 2>/dev/null; then
        if [[ "$INTERACTIVE_MODE" == "true" ]]; then
            print_color "yellow" "Требуется ввести пароль sudo для продолжения."
            if ! sudo true; then
                print_color "red" "Не удалось получить права sudo."
                return 1
            fi
        else
            print_color "red" "Требуются права sudo в неинтерактивном режиме."
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
        # shellcheck disable=SC1091
        . /etc/os-release
        OS=$ID
        OS_VERSION=$VERSION_ID
        print_color "green" "✓ Обнаружена ОС: $PRETTY_NAME"
    else
        error_exit "Не удалось определить операционную систему"
    fi
}

version_compare() {
    local version1="$1"
    local version2="$2"

    if [[ "$version1" == "$version2" ]]; then
        return 0
    fi

    local IFS=.
    local i ver1=($version1) ver2=($version2)

    for ((i=0; i<${#ver1[@]}; i++)); do
        if [[ -z ${ver2[i]} ]]; then
            ver2[i]=0
        fi

        if ((10#${ver1[i]} > 10#${ver2[i]})); then
            return 0
        fi

        if ((10#${ver1[i]} < 10#${ver2[i]})); then
            return 1
        fi
    done

    return 0
}

check_docker_version() {
    local docker_version
    docker_version=$(docker version --format '{{.Server.Version}}' 2>/dev/null | cut -d'-' -f1)

    if [[ -z "$docker_version" ]]; then
        print_color "red" "Не удалось определить версию Docker"
        return 1
    fi

    if ! version_compare "$docker_version" "$MIN_DOCKER_VERSION"; then
        print_color "red" "Требуется Docker версии $MIN_DOCKER_VERSION или выше (текущая: $docker_version)"
        return 1
    fi

    print_color "green" "✓ Docker версии $docker_version"
    return 0
}

check_docker_compose() {
    if command -v docker &>/dev/null && docker compose version &>/dev/null; then
        local compose_version
        compose_version=$(docker compose version --short 2>/dev/null)

        if ! version_compare "$compose_version" "$MIN_COMPOSE_VERSION"; then
            print_color "red" "Требуется Docker Compose версии $MIN_COMPOSE_VERSION или выше"
            return 1
        fi

        COMPOSER="docker compose --env-file $ENV_FILE --project-directory ."
        print_color "green" "✓ Найден Docker Compose plugin версии $compose_version"
    elif command -v docker-compose &>/dev/null; then
        local compose_version
        compose_version=$(docker-compose version --short 2>/dev/null)

        if ! version_compare "$compose_version" "$MIN_COMPOSE_VERSION"; then
            print_color "red" "Требуется Docker Compose версии $MIN_COMPOSE_VERSION или выше"
            return 1
        fi

        COMPOSER="docker-compose --env-file $ENV_FILE --project-directory ."
        print_color "green" "✓ Найден docker-compose standalone версии $compose_version"
    else
        return 1
    fi

    return 0
}


check_and_install_make() {
    if command -v make &>/dev/null; then
        print_color "green" "✓ 'make' уже установлен."
        return 0
    fi

    print_color "yellow" "Команда 'make' не найдена. Попытка установки..."

    if [[ "$INTERACTIVE_MODE" == "true" ]]; then
        read -rp "Продолжить установку 'make'? (y/n): " install_confirm
        if [[ "$install_confirm" != "y" ]]; then
            error_exit "Установка 'make' отменена. Makefile и команда 'make help' будут недоступны."
        fi
    fi

    # Скрываем вывод, чтобы не засорять лог развертывания
    # В случае ошибки, она все равно будет обработана
    case "$OS" in
        ubuntu|debian)
            sudo apt-get update >/dev/null 2>&1
            if ! sudo apt-get install -y make; then
                error_exit "Не удалось установить 'make' с помощью apt-get."
            fi
            ;;
        centos|rhel|fedora)
            if ! sudo dnf install -y make; then
                error_exit "Не удалось установить 'make' с помощью dnf."
            fi
            ;;
        *)
            print_color "red" "Не удалось автоматически установить 'make' для ОС: $OS."
            print_color "red" "Пожалуйста, установите 'make' вручную и перезапустите скрипт."
            return 1
            ;;
    esac

    if ! command -v make &>/dev/null; then
        error_exit "Команда 'make' все еще недоступна после попытки установки."
    fi

    print_color "green" "✓ 'make' успешно установлен."
}

install_docker_instructions() {
    print_color "red" "Docker или Docker Compose не установлены или не соответствуют минимальным требованиям."
    print_color "yellow" "Инструкции по установке для вашей ОС ($OS):"
    echo ""

    case "$OS" in
        ubuntu|debian)
            # Убираем кавычки у EOF, чтобы переменная $OS корректно подставлялась в URL
            cat << EOF
# 1. Обновите список пакетов:
sudo apt-get update

# 2. Установите необходимые пакеты:
sudo apt-get install -y ca-certificates curl gnupg

# 3. Добавьте официальный GPG ключ Docker:
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL "https://download.docker.com/linux/${OS}/gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# 4. Добавьте репозиторий Docker:
echo \
  "deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${OS} \
  \$(. /etc/os-release && echo "\$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 5. Установите Docker Engine и Docker Compose:
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 6. Добавьте вашего пользователя в группу docker, чтобы не использовать sudo:
sudo usermod -aG docker \$USER
echo "ВАЖНО: Перезайдите в систему или выполните 'newgrp docker', чтобы изменения вступили в силу."
EOF
            ;;
        centos|rhel|fedora)
            # Убираем кавычки у EOF и исправляем URL репозитория
            cat << EOF
# 1. Установите DNF плагины:
sudo dnf -y install dnf-plugins-core

# 2. Добавьте репозиторий Docker для вашей ОС:
sudo dnf config-manager --add-repo "https://download.docker.com/linux/centos/docker-ce.repo"

# 3. Установите Docker Engine и Compose:
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 4. Запустите и включите Docker в автозагрузку:
sudo systemctl start docker
sudo systemctl enable docker

# 5. Добавьте вашего пользователя в группу docker, чтобы не использовать sudo:
sudo usermod -aG docker \$USER
echo "ВАЖНО: Перезайдите в систему или выполните 'newgrp docker', чтобы изменения вступили в силу."
EOF
            ;;
        altlinux)
            cat << EOF
# ALT Linux использует apt-get, но пакеты называются docker-engine и docker-compose.

# 1. Обновите список пакетов:
sudo apt-get update

# 2. Установите Docker Engine и Docker Compose:
sudo apt-get install -y docker-engine docker-compose

# 3. Запустите сервис Docker и добавьте его в автозагрузку:
sudo systemctl enable --now docker

# 4. Добавьте вашего пользователя в группу docker, чтобы не использовать sudo:
sudo usermod -aG docker $USER
echo "ВАЖНО: Перезайдите в систему или выполните 'newgrp docker', чтобы изменения вступили в силу."
EOF
            ;;
        *)
            print_color "yellow" "Автоматические инструкции для $OS недоступны."
            print_color "yellow" "Посетите https://docs.docker.com/engine/install/ для получения инструкций."
            ;;
    esac
}

install_docker() {
    print_color "yellow" "Запускаю автоматическую установку Docker для ОС: $OS..."

    case "$OS" in
        ubuntu|debian)
            print_color "blue" "[1/5] Обновление списка пакетов..."
            sudo apt-get update || error_exit "Не удалось обновить пакеты apt"
            
            print_color "blue" "[2/5] Установка зависимостей..."
            sudo apt-get install -y ca-certificates curl gnupg || error_exit "Не удалось установить зависимости"
            
            print_color "blue" "[3/5] Добавление GPG ключа Docker..."
            sudo install -m 0755 -d /etc/apt/keyrings
            curl -fsSL "https://download.docker.com/linux/${OS}/gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg || error_exit "Не удалось скачать GPG ключ"
            sudo chmod a+r /etc/apt/keyrings/docker.gpg
            
            print_color "blue" "[4/5] Добавление репозитория Docker..."
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${OS} $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
            
            print_color "blue" "[5/5] Установка Docker Engine и Docker Compose..."
            sudo apt-get update || error_exit "Не удалось обновить пакеты apt после добавления репозитория"
            sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin || error_exit "Не удалось установить пакеты Docker"
            ;;
        centos|rhel|fedora)
            print_color "blue" "[1/3] Установка DNF плагинов..."
            sudo dnf -y install dnf-plugins-core || error_exit "Не удалось установить dnf-plugins-core"
            
            print_color "blue" "[2/3] Добавление репозитория Docker..."
            sudo dnf config-manager --add-repo "https://download.docker.com/linux/centos/docker-ce.repo" || error_exit "Не удалось добавить репозиторий Docker"
            
            print_color "blue" "[3/3] Установка Docker Engine и Docker Compose..."
            sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin || error_exit "Не удалось установить пакеты Docker"
            ;;
        altlinux)
            print_color "blue" "[1/2] Обновление списка пакетов..."
            sudo apt-get update || error_exit "Не удалось обновить пакеты apt"
            
            print_color "blue" "[2/2] Установка пакетов docker-engine и docker-compose..."
            sudo apt-get install -y docker-engine docker-compose || error_exit "Не удалось установить docker-engine и docker-compose."
            ;;
        *)
            error_exit "Автоматическая установка Docker для ОС '$OS' не поддерживается. Пожалуйста, установите вручную."
            ;;
    esac

    print_color "green" "✓ Docker успешно установлен."
    print_color "yellow" "Настройка прав доступа и запуск сервиса..."
    sudo systemctl start docker
    sudo systemctl enable docker
    if ! sudo usermod -aG docker "$USER"; then
        print_color "yellow" "Предупреждение: не удалось добавить пользователя в группу docker. Команды 'docker' могут требовать 'sudo'."
    fi

    # --- НОВЫЙ БЛОК: ПРЕДЛОЖЕНИЕ ПЕРЕЗАГРУЗКИ ---
    print_color "red" "\n!!! УСТАНОВКА ЗАВЕРШЕНА !!!"
    print_color "yellow" "Для применения прав доступа к Docker и завершения установки требуется перезагрузка."
    
    if [[ "$INTERACTIVE_MODE" == "true" ]]; then
        read -rp "Перезагрузить систему сейчас? (y/n): " reboot_confirm
        if [[ "$reboot_confirm" == "y" || "$reboot_confirm" == "Y" ]]; then
            print_color "green" "Перезагрузка..."
            sudo reboot
        else
            print_color "yellow" "Перезагрузка отменена. Пожалуйста, перезагрузите систему вручную."
            print_color "cyan" "После этого перезапустите скрипт развертывания: ./$(basename "$0") --start"
        fi
    else
        # В неинтерактивном режиме просто выводим сообщение
        print_color "yellow" "Система требует перезагрузки. Пожалуйста, перезагрузите сервер вручную."
    fi

    exit 0
}

setup_docker_permissions() {
    local needs_relog=false

    if ! getent group docker >/dev/null 2>&1; then
        print_color "yellow" "Создание группы 'docker'..."
        if ! sudo groupadd docker; then
            error_exit "Не удалось создать группу docker"
        fi
        needs_relog=true
    fi

    if ! id -nG "$USER" | grep -qw "docker"; then
        print_color "yellow" "Добавление пользователя '$USER' в группу 'docker'..."
        if ! sudo usermod -aG docker "$USER"; then
            error_exit "Не удалось добавить пользователя в группу docker"
        fi
        needs_relog=true
    fi

    # Применяем изменения группы для текущей сессии
    if [[ "$needs_relog" == "true" ]]; then
        print_color "red" "\n⚠ ВАЖНО! Права Docker были изменены."
        print_color "yellow" "Выполните одно из следующих действий:"
        print_color "cyan" "  1. Выйдите из системы и войдите снова"
        print_color "cyan" "  2. Выполните: newgrp docker"
        print_color "cyan" "  3. Перезапустите скрипт с sudo"

        if [[ "$INTERACTIVE_MODE" == "true" ]]; then
            read -rp "Попробовать применить изменения сейчас? (y/n): " apply_now
            if [[ "$apply_now" == "y" ]]; then
                exec sg docker "$0" "$@"
            fi
        fi

        exit 0
    fi

    print_color "green" "✓ Права доступа к Docker настроены"
}

# ============================================================================
# Configuration Functions
# ============================================================================

read_config_value() {
    local var_name="$1"
    local prompt="$2"
    local is_password="${3:-false}"
    local validator="${4:-}"
    local env_var_name="F152Z_${var_name^^}"

    # Проверяем переменную окружения
    if [[ -n "${!env_var_name:-}" ]]; then
        declare -g "$var_name=${!env_var_name}"

        # Валидация, если указана
        if [[ -n "$validator" ]]; then
            if ! $validator "${!var_name}"; then
                error_exit "Некорректное значение в переменной $env_var_name"
            fi
        fi

        return 0
    fi

    # Интерактивный режим
    if [[ "$INTERACTIVE_MODE" == "true" ]]; then
        local value=""

        while [[ -z "$value" ]]; do
            if [[ "$is_password" == "true" ]]; then
                read -rsp "$prompt: " value
                echo ""

                if [[ -n "$validator" ]]; then
                    if ! $validator "$value"; then
                        value=""
                        continue
                    fi
                fi

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

                if [[ -n "$validator" ]] && [[ -n "$value" ]]; then
                    if ! $validator "$value"; then
                        value=""
                        continue
                    fi
                fi
            fi

            if [[ -z "$value" ]]; then
                print_color "yellow" "⚠ Это поле обязательно для заполнения."
            fi
        done

        declare -g "$var_name=$value"
    else
        error_exit "Переменная $env_var_name не установлена в неинтерактивном режиме"
    fi
}

backup_existing_file() {
    local file="$1"

    if [[ -f "$file" ]]; then
        mkdir -p "$BACKUP_DIR"
        local backup_name="${BACKUP_DIR}/$(basename "$file").$(date +%Y%m%d_%H%M%S).bak"

        if cp "$file" "$backup_name"; then
            print_color "yellow" "📦 Бэкап $file сохранен в $backup_name"
        else
            print_color "red" "Не удалось создать бэкап $file"
        fi
    fi
}

create_env_file() {
    if [[ -f "$ENV_FILE" ]]; then
        print_color "yellow" "📋 $ENV_FILE уже существует."

        if [[ "$INTERACTIVE_MODE" == "true" ]]; then
            read -rp "Использовать существующий файл? (y/n): " use_existing

            if [[ "$use_existing" == "y" ]]; then
                set -a
                # shellcheck disable=SC1090
                source "$ENV_FILE"
                set +a
                return 0
            else
                backup_existing_file "$ENV_FILE"
            fi
        else
            set -a
            # shellcheck disable=SC1090
            source "$ENV_FILE"
            set +a
            return 0
        fi
    fi

    print_color "green" "Создание файла конфигурации..."

    local secret_key
    secret_key=$(generate_secret_key)

    read_config_value "db_password" "Введите пароль для БД (мин. 12 символов)" true validate_password_strength
    read_config_value "server_name" "Введите домен (например, example.com)" false validate_domain
    read_config_value "cors_origins" "Введите домены CORS (через запятую)" false
    read_config_value "admin_email" "Введите email администратора" false validate_email
    read_config_value "admin_password" "Введите пароль администратора" true validate_password_strength

    local env_content
    env_content=$(cat <<EOF
# f152z Configuration File
# Generated: $(date)
# Version: $SCRIPT_VERSION

# Application Settings
FLASK_ENV=production
SECRET_KEY=${secret_key}

# Database Settings
DB_PASSWORD=${db_password}

# Server Settings
SERVER_NAME=${server_name}
CORS_ORIGINS=${cors_origins}

# Admin Settings
ADMIN_EMAIL=${admin_email}

# Docker Settings
COMPOSE_PROJECT_NAME=f152z
DOCKER_BUILDKIT=1
COMPOSE_DOCKER_CLI_BUILD=1
EOF
)

    atomic_write "$ENV_FILE" "$env_content" "600"

    print_color "green" "✓ $ENV_FILE создан"

    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
}

# ============================================================================
# Docker Compose Generation
# ============================================================================

generate_docker_compose() {
    backup_existing_file "docker-compose.yml"
    
    print_color "green" "Генерация docker-compose.yml..."
    
    local certbot_nginx_volumes=""
    local certbot_service=""

    if [[ "$USE_LETSENCRYPT" == "true" ]]; then
        certbot_nginx_volumes=$(cat <<'EOM'
      - ./certbot/conf:/etc/letsencrypt:ro
      - ./certbot/www:/var/www/certbot:ro
EOM
)
        certbot_service=$(cat <<'EOM'

  certbot:
    image: certbot/certbot
    container_name: f152z_certbot
    volumes:
      - ./certbot/conf:/etc/letsencrypt
      - ./certbot/www:/var/www/certbot
    entrypoint: "/bin/sh -c 'trap exit TERM; while :; do certbot renew; sleep 12h & wait \$$!; done;'"
    restart: unless-stopped
    networks:
      - f152z_network
EOM
)
    fi

    local compose_content
    compose_content=$(cat <<EOM
# f152z Docker Compose Configuration
# Generated: $(date)
# Version: $SCRIPT_VERSION

services:
  postgres:
    image: postgres:15-alpine
    container_name: f152z_postgres
    environment:
      POSTGRES_DB: flask_app
      POSTGRES_USER: flask_user
      POSTGRES_PASSWORD: \${DB_PASSWORD}
      POSTGRES_INITDB_ARGS: '--encoding=UTF-8 --lc-collate=C --lc-ctype=C'
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./backups:/backups
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U flask_user -d flask_app || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    restart: unless-stopped
    networks:
      - f152z_network
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 1G
        reservations:
          cpus: '0.25'
          memory: 256M
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

  redis:
    image: redis:7-alpine
    container_name: f152z_redis
    command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    restart: unless-stopped
    networks:
      - f152z_network
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 512M
        reservations:
          cpus: '0.1'
          memory: 128M
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

  app:
    image: ${IMAGE_NAME}:${IMAGE_TAG}
    container_name: f152z_app
    environment:
      FLASK_ENV: production
      SECRET_KEY: \${SECRET_KEY}
      DATABASE_URI: 'postgresql://flask_user:\${DB_PASSWORD}@postgres:5432/flask_app'
      REDIS_URL: 'redis://redis:6379'
      CORS_ORIGINS: \${CORS_ORIGINS}
      SERVER_NAME: \${SERVER_NAME}
      API_KEY_FRONTEND_CLIENT: \${API_KEY_FRONTEND_CLIENT}
      PYTHONUNBUFFERED: 1
      WORKERS: 4
    volumes:
      - static_data:/app/static
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped
    networks:
      - f152z_network
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    tmpfs:
      - /tmp
      - /run

  nginx:
    image: nginx:alpine
    container_name: f152z_nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro
      - static_data:/app/static:ro
${certbot_nginx_volumes}
    depends_on:
      - app
    restart: unless-stopped
    networks:
      - f152z_network
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 512M
        reservations:
          cpus: '0.1'
          memory: 128M
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
${certbot_service}

volumes:
  postgres_data:
    name: f152z_postgres_data
  redis_data:
    name: f152z_redis_data
  static_data:
    name: f152z_static_data

networks:
  f152z_network:
    name: f152z_network
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16
EOM
)
    
    atomic_write "docker-compose.yml" "$compose_content" "600"
    
    print_color "green" "✓ docker-compose.yml создан"
}

# ============================================================================
# Nginx Configuration
# ============================================================================

generate_nginx_config() {
    mkdir -p nginx
    backup_existing_file "nginx/nginx.conf"
    
    print_color "green" "Генерация конфигурации Nginx..."
    
    local nginx_config
    read -r -d '' nginx_config << EOM
# f152z Nginx Configuration
# Generated: $(date)
# Version: $SCRIPT_VERSION

# Rate limiting
limit_req_zone \$binary_remote_addr zone=general:10m rate=10r/s;
limit_req_zone \$binary_remote_addr zone=api:10m rate=30r/s;

# Upstream configuration
upstream app_backend {
    least_conn;
    server app:8000 max_fails=3 fail_timeout=30s;
}

# HTTP server - redirect to HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name ${SERVER_NAME};
    
    # Let's Encrypt challenge
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    
    # Redirect all other traffic to HTTPS
    location / {
        return 301 https://\$host\$request_uri;
    }
}

# HTTPS server
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${SERVER_NAME};
    
    # SSL Configuration
EOM
    
    if [[ "$USE_LETSENCRYPT" == "true" ]]; then
        nginx_config+=$'\n    ssl_certificate /etc/letsencrypt/live/'"${SERVER_NAME}"$'/fullchain.pem;'
        nginx_config+=$'\n    ssl_certificate_key /etc/letsencrypt/live/'"${SERVER_NAME}"$'/privkey.pem;'
        nginx_config+=$'\n    ssl_trusted_certificate /etc/letsencrypt/live/'"${SERVER_NAME}"$'/chain.pem;'
    else
        nginx_config+=$'\n    ssl_certificate /etc/nginx/ssl/fz152.crt;'
        nginx_config+=$'\n    ssl_certificate_key /etc/nginx/ssl/fz152.key;'
    fi
    
    local nginx_config_end
    read -r -d '' nginx_config_end << EOM
    
    # Modern SSL configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:10m;
    ssl_session_tickets off;
    ssl_stapling on;
    ssl_stapling_verify on;
    
    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy "default-src 'self' https:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline';" always;
    
    # HSTS
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
    
    # Logging
    access_log /var/log/nginx/f152z_access.log combined;
    error_log /var/log/nginx/f152z_error.log warn;
    
    # General settings
    client_max_body_size 10M;
    client_body_timeout 60s;
    client_header_timeout 60s;
    keepalive_timeout 65;
    send_timeout 60s;
    
    # Gzip compression
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/xml text/javascript application/json application/javascript application/xml+rss application/rss+xml application/atom+xml image/svg+xml text/x-js text/x-cross-domain-policy application/x-font-ttf application/x-font-opentype application/vnd.ms-fontobject image/x-icon;
    
    # Rate limiting
    limit_req zone=general burst=20 nodelay;

    location /static/ {
        # Путь к папке, которую мы примонтировали через именованный том.
        alias /app/static/;
        
        # Разрешаем браузерам кэшировать файлы на длительный срок.
        expires 1y;
        add_header Cache-Control "public";

        # Отключаем логирование для статики, чтобы не засорять логи.
        access_log off;
    }
    
    # API endpoints
    location /api {
        limit_req zone=api burst=50 nodelay;
        
        proxy_pass http://app_backend;
        proxy_http_version 1.1;
        
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Request-ID \$request_id;
        
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        proxy_buffering off;
        proxy_request_buffering off;
    }
    
    # WebSocket support
    location /socket.io {
        proxy_pass http://app_backend/socket.io;
        proxy_http_version 1.1;
        
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 86400s;
        
        proxy_buffering off;
    }
    
    # Main application
    location / {
        proxy_pass http://app_backend;
        proxy_http_version 1.1;
        
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Request-ID \$request_id;
        
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
        proxy_busy_buffers_size 8k;
        
        proxy_cache_bypass \$http_upgrade;
    }
    
    # Health check endpoint
    location /health {
        access_log off;
        return 200 "OK";
        add_header Content-Type text/plain;
    }
}
EOM

    nginx_config+="$nginx_config_end"
    
    atomic_write "nginx/nginx.conf" "$nginx_config" "600"
    
    print_color "green" "✓ Конфигурация Nginx создана"
}

# ============================================================================
# SSL Certificate Management
# ============================================================================

setup_ssl_certificates() {
    if [[ "$USE_LETSENCRYPT" == "true" ]]; then
        setup_letsencrypt
    else
        setup_self_signed_certificate
    fi
}

setup_self_signed_certificate() {
    local cert_dir="nginx/ssl"
    mkdir -p "$cert_dir"

    if [[ -f "${cert_dir}/fz152.key" ]] && [[ -f "${cert_dir}/fz152.crt" ]]; then
        print_color "yellow" "🔒 SSL сертификаты уже существуют. Пропуск генерации."
        return 0
    fi

    print_color "yellow" "🔐 Генерация самоподписанного SSL сертификата..."

    # Генерация приватного ключа
    openssl genrsa -out "${cert_dir}/fz152.key" 2048 2>/dev/null

    # Генерация сертификата с SAN
    openssl req -new -x509 \
        -key "${cert_dir}/fz152.key" \
        -out "${cert_dir}/fz152.crt" \
        -days 365 \
        -subj "/C=RU/ST=Moscow/L=Moscow/O=f152z/CN=${SERVER_NAME}" \
        -addext "subjectAltName=DNS:${SERVER_NAME},DNS:www.${SERVER_NAME}" 2>/dev/null

    # Установка прав
    secure_file_permissions "${cert_dir}/fz152.key" "600"
    secure_file_permissions "${cert_dir}/fz152.crt" "644"

    print_color "green" "✓ SSL сертификаты созданы"
    print_color "yellow" "⚠ Используется самоподписанный сертификат. Для production рекомендуется Let's Encrypt."
}

setup_letsencrypt() {
    print_color "green" "🔐 Настройка Let's Encrypt..."

    mkdir -p certbot/conf certbot/www

    if [[ -z "$LETSENCRYPT_EMAIL" ]]; then
        read_config_value "letsencrypt_email" "Введите email для Let's Encrypt" false validate_email
        LETSENCRYPT_EMAIL="$letsencrypt_email"
    fi

    # Сначала запускаем nginx с временным сертификатом
    setup_self_signed_certificate

    print_color "yellow" "Запуск временного Nginx для верификации домена..."
    if ! $COMPOSER up -d nginx; then
        error_exit "Не удалось запустить Nginx"
    fi

    sleep 5

    print_color "green" "Получение сертификата Let's Encrypt..."

    # Безопасное выполнение команды certbot без eval
    local certbot_args=(
        "run" "--rm"
        "-v" "$(pwd)/certbot/conf:/etc/letsencrypt"
        "-v" "$(pwd)/certbot/www:/var/www/certbot"
        "certbot/certbot" "certonly"
        "--webroot"
        "--webroot-path=/var/www/certbot"
        "--email" "$LETSENCRYPT_EMAIL"
        "--agree-tos"
        "--no-eff-email"
        "--force-renewal"
        "-d" "$SERVER_NAME"
    )

    if [[ "$SERVER_NAME" != "localhost" ]] && [[ ! "$SERVER_NAME" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        certbot_args+=("-d" "www.$SERVER_NAME")
    fi

    if docker "${certbot_args[@]}"; then
        print_color "green" "✓ Сертификат Let's Encrypt получен"

        # Перезапускаем nginx с новым сертификатом
        if ! $COMPOSER restart nginx; then
            print_color "yellow" "⚠ Не удалось перезапустить Nginx, но сертификат получен"
        fi
    else
        print_color "yellow" "⚠ Не удалось получить сертификат Let's Encrypt. Используется самоподписанный."
        USE_LETSENCRYPT="false"
    fi
}

# ============================================================================
# Docker Operations
# ============================================================================

pull_docker_images() {
    print_color "green" "Загрузка Docker образов..."

    if [[ "$DRY_RUN" == "true" ]]; then
        print_color "yellow" "[DRY RUN] Пропуск загрузки образов"
        return 0
    fi

    local pull_output
    pull_output=$($COMPOSER pull 2>&1)
    local exit_code=$?

    if [[ $exit_code -ne 0 ]]; then
        print_color "red" "Ошибка при загрузке образов:"
        echo "$pull_output"
        error_exit "Не удалось загрузить Docker образы"
    fi

    print_color "green" "✓ Docker образы загружены"
}

start_services() {
    print_color "green" "Запуск сервисов..."

    if [[ "$DRY_RUN" == "true" ]]; then
        print_color "yellow" "[DRY RUN] Пропуск запуска сервисов"
        return 0
    fi

    CLEANUP_NEEDED=true

    local start_output
    start_output=$($COMPOSER up -d --remove-orphans 2>&1)
    local exit_code=$?

    if [[ $exit_code -ne 0 ]]; then
        print_color "red" "Ошибка при запуске сервисов:"
        echo "$start_output"
        error_exit "Не удалось запустить сервисы"
    fi

    print_color "green" "✓ Сервисы запущены"
}

wait_for_service() {
    local service="$1"
    local check_command="$2"
    local max_attempts="${3:-30}"
    local delay="${4:-2}"

    print_color "blue" "Ожидание готовности $service..."

    local attempt=0
    while [ $attempt -lt $max_attempts ]; do
        if eval "$check_command" >/dev/null 2>&1; then
            printf "\r\033[K"
            print_color "green" "✓ $service готов"
            return 0
        fi

        attempt=$((attempt + 1))
        printf "\r\033[K\033[0;33m  Ожидание... %d/%d\033[0m" "$attempt" "$max_attempts"
        sleep "$delay"
    done

    echo
    error_exit "$service не готов после $max_attempts попыток"
}

wait_for_database() {
    wait_for_service "База данных" \
        "$COMPOSER exec -T postgres pg_isready -U flask_user -d flask_app" \
        30 2
}

wait_for_redis() {
    wait_for_service "Redis" \
        "$COMPOSER exec -T redis redis-cli ping" \
        30 2
}

wait_for_app() {
    # Более надежная проверка готовности приложения
    wait_for_service "Приложение" \
        "curl -ksSL https://${SERVER_NAME}/health -o /dev/null -w '%{http_code}' | grep -qE '^(200|301|302)$' || curl -ksSL http://localhost/health -o /dev/null -w '%{http_code}' | grep -qE '^(200|301|302)$' || $COMPOSER exec -T app curl -fs http://localhost:8000/health >/dev/null" \
        30 2
}

# ============================================================================
# Application Initialization
# ============================================================================

initialize_application() {
    print_color "green" "Инициализация приложения..."

    if [[ "$DRY_RUN" == "true" ]]; then
        print_color "yellow" "[DRY RUN] Пропуск инициализации приложения"
        return 0
    fi

    # Применение миграций
    print_color "blue" "Применение миграций базы данных..."

    local migration_output
    migration_output=$($COMPOSER exec -T app flask db upgrade 2>&1)
    local exit_code=$?

    if [[ $exit_code -ne 0 ]]; then
        print_color "red" "Ошибка при применении миграций:"
        echo "$migration_output"
        error_exit "Не удалось применить миграции"
    fi

    print_color "green" "✓ Миграции применены"

    # Создание администратора
    local admin_flag_file=".admin_created"

    if [[ -f "$admin_flag_file" ]]; then
        local existing_admin_email
        existing_admin_email=$(cat "$admin_flag_file")
        print_color "yellow" "ℹ Администратор ($existing_admin_email) уже создан. Пропуск."
    else
        print_color "blue" "Создание учетной записи администратора..."
        
        # Убедимся, что переменная с паролем существует
        if [[ -z "${admin_password:-}" ]]; then
            error_exit "Пароль администратора не задан. Невозможно создать пользователя."
        fi

        local create_admin_output
        local admin_exit_code=0
        
        # Выполняем команду и сохраняем ее вывод и код возврата.
        # Конструкция 'команда || код=$?' позволяет обойти 'set -e'.
        create_admin_output=$($COMPOSER exec -T app flask create-admin "$admin_email" "$admin_password" 2>&1) || admin_exit_code=$?

        # Теперь анализируем результат
        if [[ $admin_exit_code -eq 0 ]]; then
            print_color "green" "✓ Администратор создан"
            echo "$admin_email" > "$admin_flag_file"
            secure_file_permissions "$admin_flag_file" "600"
        else
            # Проверяем, не была ли ошибка связана с тем, что пользователь уже существует
            if echo "$create_admin_output" | grep -q -i "already exists"; then
                print_color "yellow" "ℹ Администратор '$admin_email' уже существует в базе данных."
                echo "$admin_email" > "$admin_flag_file"
                secure_file_permissions "$admin_flag_file" "600"
            else
                # Если это другая, непредвиденная ошибка
                print_color "red" "Критическая ошибка при создании администратора:"
                echo "$create_admin_output"
                error_exit "Не удалось создать администратора. Проверьте логи."
            fi
        fi
    fi

    # Создание API ключей
    create_api_keys


    # Выполнение команды flask collect
    print_color "blue" "Сбор статических файлов ('flask collect')..."
    if ! $COMPOSER exec -T app flask collect 2>&1 | tee -a "$LOG_FILE"; then
        error_exit "Не удалось выполнить команду 'flask collect'"
    fi
    print_color "green" "✓ Команда 'flask collect' успешно выполнена"
    
    # Перезапуск сервисов для применения новых переменных окружения (например, API ключа)
    print_color "blue" "Перезапуск сервисов для применения конфигурации..."
    if ! $COMPOSER restart app nginx 2>&1 | tee -a "$LOG_FILE"; then
        # Не прерываем выполнение, так как сервисы уже запущены
        print_color "yellow" "Предупреждение: не удалось корректно перезапустить сервисы. Может потребоваться ручной перезапуск ('make restart')."
    else
        print_color "green" "✓ Сервисы перезапущены"
    fi

}


create_api_keys() {
    print_color "blue" "Создание API ключей..."

    # Проверяем, есть ли ключ уже в .env файле
    if grep -q "API_KEY_FRONTEND_CLIENT" "$ENV_FILE"; then
        print_color "yellow" "ℹ API ключ для фронтенда уже существует в $ENV_FILE. Пропуск."
        return 0
    fi

    # Frontend API key
    local frontend_key_output
    # ИСПРАВЛЕНИЕ: Добавлен флаг --endpoints
    frontend_key_output=$($COMPOSER exec -T app flask create-apikey "API_KEY_FRONTEND_CLIENT" --endpoints "api.log_event,api.save_results" 2>&1)

    local frontend_api_key
    frontend_api_key=$(echo "$frontend_key_output" | grep -oP 'Ключ:\s*\K\S+' || echo "")

    if [[ -n "$frontend_api_key" ]]; then
        # Добавляем ключ в .env файл
        {
            echo ""
            echo "# Frontend API Key (auto-generated)"
            echo "API_KEY_FRONTEND_CLIENT=$frontend_api_key"
        } >> "$ENV_FILE"

        secure_file_permissions "$ENV_FILE" "600"

        print_color "green" "✓ API ключ для фронтенда создан и сохранен в $ENV_FILE"
        print_color "yellow" "⚠ Сохраните этот ключ в безопасном месте!"
    else
        print_color "yellow" "⚠ Не удалось создать API ключ"
    fi
}

# ============================================================================
# Utility Scripts Creation
# ============================================================================

create_utility_scripts() {
    print_color "green" "Создание вспомогательных скриптов..."

    create_update_script
    create_backup_script
    create_makefile
    create_monitoring_script

    print_color "green" "✓ Вспомогательные скрипты созданы"
}

create_update_script() {
    local script_content
    script_content=$(cat <<'EOF'
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
EOF
)

    atomic_write "update.sh" "$script_content" "755"
}

create_backup_script() {
    local script_content
    script_content=$(cat <<'EOF'
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
EOF
)

    atomic_write "backup.sh" "$script_content" "755"
}

create_makefile() {
    print_color "green" "Создание Makefile для управления сервисами..."

 cat <<'EOF' > Makefile
# f152z Makefile
# Версия: 3.3 (Исправлен парсинг вывода ключей)
# Версия с русскоязычными описаниями для команды 'help'.
# --- Базовая настройка ---
# Явно указываем BASH, чтобы избежать проблем с синтаксисом в скриптах.
SHELL := /bin/bash
.DEFAULT_GOAL := help

# --- Переменные конфигурации ---
ENV_FILE ?= prod.env
BACKUP_DIR ?= backups
LOG_DIR ?= .logs

# --- Определение команды Docker Compose (Исправленная версия) ---
# Надёжное определение команды Docker Compose с приоритетом для плагина (V2).
# Сначала пытаемся найти плагин 'docker compose'.
COMPOSE_V2 := $(shell docker compose version &>/dev/null && echo "docker compose")
# Затем, как резервный вариант, ищем 'docker-compose' (V1).
COMPOSE_V1 := $(shell command -v docker-compose 2>/dev/null)

# Используем 'docker compose' (V2), если он доступен, иначе 'docker-compose' (V1).
COMPOSE_CMD := $(or $(COMPOSE_V2),$(COMPOSE_V1))

# Если ни одна команда не найдена, прерываем с ошибкой.
ifeq ($(COMPOSE_CMD),)
	$(error "Не удалось найти 'docker compose' или 'docker-compose'. Проверьте вашу установку Docker.")
endif

COMPOSE = $(COMPOSE_CMD) --env-file $(ENV_FILE)


# --- Цвета для вывода ---
RED    :=  \033[0;31m
GREEN  :=  \033[0;32m
YELLOW :=  \033[0;33m
BLUE   :=  \033[0;34m
NC     :=  \033[0m

# ==============================================================================
# СПРАВКА - Динамически генерирует справку из комментариев
# ==============================================================================
.PHONY: help
help: ## 📖 Показать это справочное сообщение
	@echo -e "$(BLUE)Команды для управления проектом f152z$(NC)"
	@echo "---------------------------------"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	sort | awk 'BEGIN {FS = ":.*?## "}; {printf "$(GREEN)%-20s$(NC) %s\n", $$1, $$2}'
	@echo "---------------------------------"
	@echo ""
	@echo -e "$(YELLOW)Примеры использования:$(NC)"
	@echo "  make up       - Запустить все сервисы"
	@echo "  make logs     - Просмотреть логи"
	@echo "  make backup   - Создать резервную копию"

# ==============================================================================
# УПРАВЛЕНИЕ СЕРВИСАМИ
# ==============================================================================
.PHONY: up
up: ## 🚀 Запустить все сервисы в фоновом режиме
	@echo -e "$(BLUE)Запускаю сервисы...$(NC)"
	@$(COMPOSE) up -d
	@echo -e "$(GREEN)✓ Сервисы успешно запущены.$(NC)"

.PHONY: down
down: ## 🛑 Остановить все сервисы
	@echo -e "$(BLUE)Останавливаю сервисы...$(NC)"
	@$(COMPOSE) down
	@echo -e "$(GREEN)✓ Сервисы остановлены.$(NC)"

.PHONY: restart
restart: ## 🔄 Перезапустить все сервисы (down и up)
	@$(MAKE) down
	@$(MAKE) up

.PHONY: status
status: ## 📊 Показать статус сервисов
	@$(COMPOSE) ps

# ==============================================================================
# ЛОГИ
# ==============================================================================
.PHONY: logs
logs: ## 📜 Показать и отслеживать логи всех сервисов
	@$(COMPOSE) logs -f

.PHONY: logs-app
logs-app: ## 📜 Показать и отслеживать логи сервиса 'app'
	@$(COMPOSE) logs -f app

.PHONY: logs-nginx
logs-nginx: ## 📜 Показать и отслеживать логи сервиса 'nginx'
	@$(COMPOSE) logs -f nginx

# ==============================================================================
# УПРАВЛЕНИЕ ДАННЫМИ
# ==============================================================================
.PHONY: backup
backup: ## 💾 Создать сжатую резервную копию базы данных
	@echo -e "$(BLUE)Создаю резервную копию базы данных...$(NC)"
	@mkdir -p $(BACKUP_DIR)
	@TIMESTAMP=$$(date +%Y-%m-%d_%H-%M-%S); \
	FILENAME="$(BACKUP_DIR)/backup-$${TIMESTAMP}.sql.gz"; \
	$(COMPOSE) exec -T postgres pg_dump -U flask_user -d flask_app | gzip > $$FILENAME; \
	echo -e "$(GREEN)✓ Резервная копия успешно создана:$(NC) $$FILENAME"

.PHONY: restore
restore: ## 📥 Восстановить базу данных из выбранной копии
	@echo -e "$(YELLOW)Доступные резервные копии:$(NC)"
	@ls -1 $(BACKUP_DIR)/*.sql.gz 2>/dev/null || echo "Резервные копии не найдены."
	@echo ""
	@read -p "Введите полное имя файла для восстановления: " backup_file; \
	if [ -f "$$backup_file" ]; then \
		echo -e "$(BLUE)Восстанавливаю из $$backup_file...$(NC)"; \
		gunzip < "$$backup_file" | $(COMPOSE) exec -T postgres psql -U flask_user -d flask_app; \
		echo -e "$(GREEN)✓ Восстановление успешно завершено.$(NC)"; \
	else \
		echo -e "$(RED)✗ Ошибка: Файл резервной копии не найден.$(NC)"; \
	fi

.PHONY: migrate
migrate: ## 🧬 Выполнить миграции базы данных
	@echo -e "$(BLUE)Выполняю миграции базы данных...$(NC)"
	@$(COMPOSE) exec app flask db upgrade
	@echo -e "$(GREEN)✓ Миграции успешно выполнены.$(NC)"


# ==============================================================================
# УПРАВЛЕНИЕ API-КЛЮЧАМИ
# ==============================================================================
.PHONY: create-apikey
create-apikey: ## 🔑 Создать API-ключ с определенными правами
	@read -p "Введите имя для API-ключа (например, mobile-app-readonly): " key_name; \
	read -p "Введите эндпоинты через запятую (например, /api/v1/users,/api/v1/posts): " endpoints; \
	if [ -z "$$key_name" ] || [ -z "$$endpoints" ]; then \
		echo -e "$(RED)✗ Ошибка: Имя ключа и эндпоинты не могут быть пустыми.$(NC)"; \
		exit 1; \
	fi; \
	echo -e "$(BLUE)Генерирую API-ключ...$(NC)"; \
	API_KEY_OUTPUT=$$($(COMPOSE) exec -T app flask create-apikey "$$key_name" --endpoints "$$endpoints"); \
	API_KEY=$$(echo "$$API_KEY_OUTPUT" | grep 'Ключ:' | awk '{print $$2}'); \
	if [ -n "$$API_KEY" ]; then \
		VAR_NAME=$$(echo "$$key_name" | tr '[:lower:]' '[:upper:]' | tr '-' '_')_API_KEY; \
		echo -e "\n# API-ключ для $$key_name\n$$VAR_NAME=$$API_KEY" >> $(ENV_FILE); \
		echo -e "$(GREEN)✓ API-ключ создан и сохранен в $(ENV_FILE):$(NC)"; \
		echo -e "Переменная: $(YELLOW)$$VAR_NAME$(NC)"; \
		echo -e "Ключ:      $(YELLOW)$$API_KEY$(NC)"; \
	else \
		echo -e "$(RED)✗ Ошибка: Не удалось сгенерировать API-ключ.$(NC)"; \
	fi

.PHONY: create-admin-apikey
create-admin-apikey: ## 👑 Создать ADMIN API-ключ с полным доступом
	@read -p "Введите имя для ADMIN ключа (например, admin-script): " key_name; \
	if [ -z "$$key_name" ]; then \
		echo -e "$(RED)✗ Ошибка: Имя ключа не может быть пустым.$(NC)"; \
		exit 1; \
	fi; \
	echo -e "$(BLUE)Генерирую ADMIN API-ключ...$(NC)"; \
	API_KEY_OUTPUT=$$($(COMPOSE) exec -T app flask create-apikey "$$key_name" --admin); \
	API_KEY=$$(echo "$$API_KEY_OUTPUT" | grep 'Ключ:' | awk '{print $$2}'); \
	if [ -n "$$API_KEY" ]; then \
		VAR_NAME=$$(echo "$$key_name" | tr '[:lower:]' '[:upper:]' | tr '-' '_')_ADMIN_API_KEY; \
		echo -e "\n# ADMIN API-ключ для $$key_name\n$$VAR_NAME=$$API_KEY" >> $(ENV_FILE); \
		echo -e "$(GREEN)✓ ADMIN API-ключ создан и сохранен в $(ENV_FILE):$(NC)"; \
		echo -e "Переменная: $(YELLOW)$$VAR_NAME$(NC)"; \
		echo -e "Ключ:      $(YELLOW)$$API_KEY$(NC)"; \
	else \
		echo -e "$(RED)✗ Ошибка: Не удалось сгенерировать ADMIN API-ключ.$(NC)"; \
	fi


# ==============================================================================
# ОТЛАДКА И ДИАГНОСТИКА
# ==============================================================================
.PHONY: shell
shell: ## 💻 Открыть командную оболочку (bash) в контейнере 'app'
	@$(COMPOSE) exec app /bin/bash

.PHONY: shell-db
shell-db: ## 🗄️ Открыть командную оболочку (psql) для базы данных
	@$(COMPOSE) exec postgres psql -U flask_user -d flask_app

.PHONY: shell-redis
shell-redis: ## ⚡ Открыть интерфейс командной строки Redis (redis-cli)
	@$(COMPOSE) exec redis redis-cli

.PHONY: test
test: ## ✅ Запустить тесты приложения (pytest)
	@echo -e "$(BLUE)Запускаю тесты...$(NC)"
	@$(COMPOSE) exec app pytest

.PHONY: info
info: ## ℹ️ Показать подробную информацию о развертывании
	@echo -e "$(BLUE)Информация о развертывании f152z$(NC)"
	@echo "---------------------------"
	@echo -e "Файл окружения: $(GREEN)$(ENV_FILE)$(NC)"
	@echo -e "Команда Compose:  $(GREEN)$(COMPOSE_CMD)$(NC)"
	@echo ""
	@echo -e "$(BLUE)Статус сервисов:$(NC)"
	@$(COMPOSE) ps --format "table {{.Name}}\t{{.State}}\t{{.Ports}}"

.PHONY: validate
validate: ## ✔️ Проверить конфигурацию docker-compose
	@echo -e "$(BLUE)Проверяю конфигурацию docker-compose...$(NC)"
	@$(COMPOSE) config --quiet && echo -e "$(GREEN)✓ Конфигурация корректна.$(NC)" || echo -e "$(RED)✗ Конфигурация содержит ошибки.$(NC)"


# ==============================================================================
# ОБСЛУЖИВАНИЕ
# ==============================================================================
.PHONY: update
update: ## ⬆️ Обновить приложение (скачать код и перезапустить)
	@echo -e "$(BLUE)Обновляю приложение...$(NC)"
	@bash update.sh
	@echo -e "$(GREEN)✓ Процесс обновления завершен.$(NC)"

.PHONY: monitor
monitor: ## 📈 Показать использование ресурсов (требует monitor.sh)
	@bash monitor.sh

.PHONY: clean-backups
clean-backups: ## 🗑️ Удалить резервные копии старше 30 дней
	@echo -e "$(BLUE)Очищаю старые резервные копии...$(NC)"
	@find $(BACKUP_DIR) -type f -name "*.sql.gz" -mtime +30 -delete
	@echo -e "$(GREEN)✓ Старые резервные копии удалены.$(NC)"

.PHONY: clean-logs
clean-logs: ## 🗑️ Удалить логи старше 30 дней
	@echo -e "$(BLUE)Очищаю старые логи...$(NC)"
	@find $(LOG_DIR) -type f -name "*.log" -mtime +30 -delete
	@echo -e "$(GREEN)✓ Старые логи удалены.$(NC)"

.PHONY: prune
prune: ## 🧹 Удалить неиспользуемые ресурсы Docker
	@echo -e "$(YELLOW)Эта команда удалит все остановленные контейнеры, неиспользуемые сети и образы.$(NC)"
	@docker system prune

.PHONY: destroy
destroy: ## 🔥 ОПАСНО: Остановить сервисы и удалить ВСЕ данные
	@echo -e "$(RED)!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!$(NC)"
	@echo -e "$(RED)!! ВНИМАНИЕ: ВЫ СОБИРАЕТЕСЬ НАВСЕГДА УДАЛИТЬ ВСЕ ДАННЫЕ !!$(NC)"
	@echo -e "$(RED)!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!$(NC)"
	@read -p "Это действие необратимо. Введите 'YES' для подтверждения: " confirm; \
	if [ "$$confirm" = "YES" ]; then \
		echo -e "$(BLUE)Уничтожаю все данные...$(NC)"; \
		$(COMPOSE) down -v; \
		echo -e "$(GREEN)✓ Все данные сервисов были уничтожены.$(NC)"; \
	else \
		echo -e "$(YELLOW)Отменено.$(NC)"; \
	fi
EOF
}

create_monitoring_script() {
    local script_content
    script_content=$(cat <<'EOF'
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
    $COMPOSER ps --format "table {{.Name}}\t{{.State}}\t{{.Ports}}"
    echo ""

    # Использование ресурсов
    echo "💾 Использование ресурсов:"
    docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}" $($COMPOSER ps -q) 2>/dev/null || true
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
EOF
)

    atomic_write "monitor.sh" "$script_content" "755"
}

# ============================================================================
# Summary and Completion
# ============================================================================

show_deployment_summary() {
    local public_ip
    public_ip=$(curl -s https://api.ipify.org 2>/dev/null || echo "не определен")

    print_header "РАЗВЕРТЫВАНИЕ ЗАВЕРШЕНО"

    print_color "green" "✅ Система f152z успешно развернута!"
    echo ""

    print_color "cyan" "📋 Информация о развертывании:"
    print_color "cyan" "  • URL: https://${SERVER_NAME}"
    print_color "cyan" "  • IP-адрес: ${public_ip}"
    print_color "cyan" "  • Администратор: ${ADMIN_EMAIL:-см. .admin_created}"

    if grep -q "API_KEY_FRONTEND_CLIENT" "$ENV_FILE" 2>/dev/null; then
        print_color "cyan" "  • API ключ: сохранен в $ENV_FILE"
    fi

    echo ""
    print_color "cyan" "🛠 Управление системой:"
    print_color "cyan" "  • Просмотр команд: make help"
    print_color "cyan" "  • Статус сервисов: make status"
    print_color "cyan" "  • Просмотр логов: make logs"
    print_color "cyan" "  • Создание бэкапа: make backup"
    print_color "cyan" "  • Мониторинг: make monitor"

    echo ""

    if [[ "$USE_LETSENCRYPT" == "true" ]]; then
        print_color "green" "🔒 Используется сертификат Let's Encrypt"
    else
        print_color "yellow" "⚠ Используется самоподписанный SSL сертификат"
        print_color "yellow" "  Для production рекомендуется настроить Let's Encrypt:"
        print_color "yellow" "  ./$(basename "$0") --start --use-letsencrypt"
    fi

    echo ""
    print_color "magenta" "📚 Документация и поддержка:"
    print_color "magenta" "  • README.md - основная документация"
    print_color "magenta" "  • ${LOG_DIR}/ - директория с логами"
    print_color "magenta" "  • ${BACKUP_DIR}/ - директория с резервными копиями"

    DEPLOYMENT_SUCCESSFUL=true
}

# ============================================================================
# Main Function
# ============================================================================

main() {
    # Настройка логирования
    setup_logging

    # Заголовок
    print_header "f152z Deployment v${SCRIPT_VERSION}"

    if [[ "$DRY_RUN" == "true" ]]; then
        print_color "yellow" "🔧 Режим DRY RUN - изменения не будут применены"
        echo ""
    fi

    local total_steps=10
    local current_step=0

    # Шаг 1: Проверка системы
    ((current_step++))
    print_step $current_step $total_steps "Проверка системы"
    detect_os
    if ! check_required_commands; then
        error_exit "Отсутствуют необходимые команды"
    fi
    check_and_install_make
    if ! check_sudo; then
        error_exit "Требуются права администратора"
    fi

    # Шаг 2: Проверка Docker
    ((current_step++))
    print_step $current_step $total_steps "Проверка Docker"
    if ! check_docker_compose || ! check_docker_version; then
        if [[ "$AUTO_INSTALL_DOCKER" == "true" ]]; then
            install_docker # Вызываем новую функцию установки
        else
            install_docker_instructions
            error_exit "Docker не найден или версия устарела. Запустите скрипт с флагом --install-docker для автоматической установки."
        fi
    fi

    # Шаг 3: Настройка прав Docker
    ((current_step++))
    print_step $current_step $total_steps "Настройка прав Docker"
    setup_docker_permissions

    # Шаг 4: Создание конфигурации
    ((current_step++))
    print_step $current_step $total_steps "Создание конфигурации"
    create_env_file

    # Шаг 5: Генерация docker-compose.yml
    ((current_step++))
    print_step $current_step $total_steps "Генерация файлов развертывания"
    generate_docker_compose
    generate_nginx_config

    # Шаг 6: Настройка SSL
    ((current_step++))
    print_step $current_step $total_steps "Настройка SSL сертификатов"
    setup_ssl_certificates

    # Шаг 7: Загрузка образов
    ((current_step++))
    print_step $current_step $total_steps "Загрузка Docker образов"
    pull_docker_images

    # Шаг 8: Запуск сервисов
    ((current_step++))
    print_step $current_step $total_steps "Запуск сервисов"
    start_services
    wait_for_database
    wait_for_redis

    # Шаг 9: Инициализация приложения
    ((current_step++))
    print_step $current_step $total_steps "Инициализация приложения"
    initialize_application
    wait_for_app

    # Шаг 10: Создание утилит
    ((current_step++))
    print_step $current_step $total_steps "Создание вспомогательных скриптов"
    create_utility_scripts

    # Завершение
    show_deployment_summary
}

# ============================================================================
# Script Entry Point
# ============================================================================

# Обработка аргументов командной строки
if [[ $# -eq 0 && "${F152Z_INTERACTIVE:-true}" == "true" ]]; then
    show_usage_and_exit
fi

while [[ $# -gt 0 ]]; do
    case $1 in
        --start)
            PROCEED_WITH_DEPLOYMENT=true
            shift
            ;;
        --non-interactive)
            INTERACTIVE_MODE="false"
            PROCEED_WITH_DEPLOYMENT=true
            shift
            ;;
        --dry-run)
            DRY_RUN="true"
            PROCEED_WITH_DEPLOYMENT=true
            shift
            ;;
        --use-letsencrypt)
            USE_LETSENCRYPT="true"
            PROCEED_WITH_DEPLOYMENT=true
            shift
            ;;
        --install-docker) # <-- НАЧАЛО НОВОГО БЛОКА
            AUTO_INSTALL_DOCKER="true"
            shift
            ;; # <-- КОНЕЦ НОВОГО БЛОКА
        --help)
            echo "Использование: $0 [OPTIONS]"
            echo ""
            echo "Опции:"
            echo "  --non-interactive    Неинтерактивный режим (для CI/CD)"
            echo "  --dry-run           Тестовый запуск без применения изменений"
            echo "  --use-letsencrypt   Использовать Let's Encrypt для SSL"
            echo "  --help              Показать эту справку"
            echo ""
            echo "Переменные окружения:"
            echo "  F152Z_INTERACTIVE=false       Неинтерактивный режим"
            echo "  F152Z_DB_PASSWORD             Пароль базы данных"
            echo "  F152Z_SERVER_NAME             Доменное имя"
            echo "  F152Z_CORS_ORIGINS            CORS домены"
            echo "  F152Z_ADMIN_EMAIL             Email администратора"
            echo "  F152Z_ADMIN_PASSWORD          Пароль администратора"
            echo "  F152Z_USE_LETSENCRYPT=true    Использовать Let's Encrypt"
            echo "  F152Z_LETSENCRYPT_EMAIL       Email для Let's Encrypt"
            exit 0
            ;;
        *)
            echo "Неизвестная опция: $1"
            echo "Используйте --help для справки"
            exit 1
            ;;
    esac
done

# Запуск основной функции только если был передан флаг, инициирующий развертывание
if [[ "$PROCEED_WITH_DEPLOYMENT" == "true" ]]; then
    main "$@"
elif [[ "$USE_LETSENCRYPT" == "true" ]]; then
    # Позволяет запустить с одним флагом --use-letsencrypt без --start
    main "$@"
else
    # Если переданы флаги, не начинающие развертывание (например, только --use-letsencrypt),
    # но не --start, показываем справку.
    if [[ "$INTERACTIVE_MODE" == "true" ]]; then
        show_usage_and_exit
    fi
fi
