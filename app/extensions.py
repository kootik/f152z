from flask_caching import Cache
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_redis import FlaskRedis
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

# Database
db = SQLAlchemy()

# CSRF Protection
csrf = CSRFProtect()

# CORS
cors = CORS()

# Инициализация Flask-Login
login_manager = LoginManager()
login_manager.login_view = (
    "web.login"  # Указываем на эндпоинт страницы входа (мы создадим его позже)
)
login_manager.login_message = (
    "Пожалуйста, войдите, чтобы получить доступ к этой странице."
)
login_manager.login_message_category = "info"

# Caching
cache = Cache()

# Rate Limiting
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
)

# WebSocket
socketio = SocketIO()

# Redis
redis_client = FlaskRedis()
