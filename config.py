# config.py
import os
from datetime import timedelta


# =============================================================================
# 1. БАЗОВЫЙ КЛАСС CONFIG (ДОЛЖЕН БЫТЬ ПЕРВЫМ)
# =============================================================================
class Config:
    """Base configuration."""

    # Flask
    # ВАЖНО: В production всегда должен быть установлен через переменную окружения!
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.urandom(32).hex()
    SERVER_NAME = os.environ.get("SERVER_NAME")

    # Путь, куда команда 'flask collect' будет складывать все статические файлы.
    # Этот путь должен совпадать с тем, что указан в docker-compose.yml для volume.
    STATIC_ROOT = "/app/static"

    # Database
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get("DATABASE_URI") or "postgresql://user:pass@localhost/dbname"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": 10,
        "pool_recycle": 3600,
        "pool_pre_ping": True,
        "max_overflow": 20,
    }

    # Security settings
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    WTF_CSRF_TIME_LIMIT = None
    WTF_CSRF_SSL_STRICT = True
    WTF_CSRF_TRUSTED_ORIGINS = os.environ.get("CORS_ORIGINS", "").split(",")
    # File uploads
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB

    # CORS
    CORS_ORIGINS = (
        os.environ.get("CORS_ORIGINS", "").split(",")
        if os.environ.get("CORS_ORIGINS")
        else []
    )

    # Cache
    CACHE_TYPE = "RedisCache"
    CACHE_REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    CACHE_DEFAULT_TIMEOUT = 300
    CACHE_KEY_PREFIX = "flask_cache_"

    # Rate limiting
    RATELIMIT_STORAGE_URI = os.environ.get("REDIS_URL", "redis://redis:6379/1")

    # WebSocket
    SOCKETIO_MESSAGE_QUEUE = os.environ.get("REDIS_URL", "redis://redis:6379/2")

    # Redis client
    REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/3")

    # Application specific
    PASSING_SCORE_THRESHOLD = 80
    MAX_RESULTS_PER_PAGE = 1000
    MAX_SESSION_ID_LENGTH = 128
    MAX_EVENT_TYPE_LENGTH = 64
    API_KEY_FRONTEND_CLIENT = os.environ.get("API_KEY_FRONTEND_CLIENT")
    # Proxy
    USE_PROXY_FIX = False
    PROXY_FIX_X_FOR = 1
    PROXY_FIX_X_PROTO = 1
    PROXY_FIX_X_HOST = 1
    PROXY_FIX_X_PORT = 1


# =============================================================================
# 2. КОНКРЕТНЫЕ КОНФИГУРАЦИИ (НАСЛЕДУЮТСЯ ОТ БАЗОВОГО)
# =============================================================================


class DevelopmentConfig(Config):
    """Development configuration."""

    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get("DEV_DATABASE_URI") or "sqlite:///dev.db"
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_SSL_STRICT = False

    # Simple cache for development
    CACHE_TYPE = "SimpleCache"

    # In-memory rate limiting for development
    # Используем `_URI`, чтобы имя было консистентным с базовым классом
    RATELIMIT_STORAGE_URI = "memory://"


class TestingConfig(Config):
    """Testing configuration."""

    TESTING = True
    DEBUG = True

    # Use in-memory SQLite for tests
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    # Эта строка исправляет ошибку TypeError в тестах
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False

    # Simple cache for testing
    CACHE_TYPE = "NullCache"

    # Disable rate limiting for tests
    RATELIMIT_ENABLED = False


class ProductionConfig(Config):
    """Production configuration."""

    DEBUG = False

    # Используем .get() для предотвращения падения при импорте
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URI")
    # Временно отключаем строгую проверку Referer/Origin для отладки
    WTF_CSRF_SSL_STRICT = True

    # Production security
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=1)

    # Enable proxy fix for production
    USE_PROXY_FIX = True

    # Production logging
    LOG_LEVEL = "INFO"

    # Performance-tuned engine options for production
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": int(os.environ.get("DB_POOL_SIZE", 20)),
        "pool_recycle": 1800,
        "pool_pre_ping": True,
        "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", 40)),
        "connect_args": {"connect_timeout": 10, "application_name": "flask_app"},
    }


# =============================================================================
# 3. СЛОВАРЬ ДЛЯ ДОСТУПА К КОНФИГУРАЦИЯМ
# =============================================================================
config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
