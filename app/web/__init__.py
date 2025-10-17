# app/web/__init__.py

from flask import Blueprint

# Создаем Blueprint для веб-страниц (HTML-страниц)
# Важно указать, где находятся шаблоны для этого Blueprint'а

web_bp = Blueprint("web", __name__)
# Импортируем файл с маршрутами ПОСЛЕ создания Blueprint'а
from . import routes
