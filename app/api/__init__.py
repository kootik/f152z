# app/api/__init__.py

from flask import Blueprint

# Создаем единственный, правильный объект Blueprint
api_bp = Blueprint("api", __name__)

# Импортируем маршруты, которые будут использовать этот api_bp
from . import routes
