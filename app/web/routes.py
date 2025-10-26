# app/web/routes.py

import os
from datetime import UTC, datetime

from flask import (
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db
from app.models import User

from . import web_bp
from .forms import LoginForm

# =============================================================================
# ОСНОВНЫЕ МАРШРУТЫ ДЛЯ ОТОБРАЖЕНИЯ СТРАНИЦ
# =============================================================================


@web_bp.route("/")
def index():
    """Отдает главную страницу с тестом."""
    return render_template("index.html")


@web_bp.route("/117study")
def study_117():  # <-- Имя функции стало чище
    """
    Отдает HTML-страницу для обучения по 117-ФЗ,
    передавая в нее API-ключ для фронтенда.
    """
    frontend_api_key = current_app.config.get("API_KEY_FRONTEND_CLIENT")
    return render_template("study-117.html", frontend_api_key=frontend_api_key)


@web_bp.route("/study")
def study():
    """Отдает HTML-страницу для общего обучения."""
    frontend_api_key = current_app.config.get("API_KEY_FRONTEND_CLIENT")
    return render_template("study.html", frontend_api_key=frontend_api_key)


@web_bp.route("/results")
@login_required
def results():
    """Отдает HTML-страницу для отображения результатов (требует аутентификации)."""
    frontend_api_key = current_app.config.get("API_KEY_FRONTEND_CLIENT")
    return render_template("results.html", frontend_api_key=frontend_api_key)


@web_bp.route("/152test")
def test_152():  # <-- Имя функции стало чище
    """Отдает HTML-страницу для тестирования ПД-152."""
    frontend_api_key = current_app.config.get("API_KEY_FRONTEND_CLIENT")
    return render_template("152-test.html", frontend_api_key=frontend_api_key)


@web_bp.route("/117infographic")
def infographic_117():  # <-- Имя функции стало чище
    """Отдает HTML-страницу с инфографикой для 117-ФЗ."""
    frontend_api_key = current_app.config.get("API_KEY_FRONTEND_CLIENT")
    return render_template("infographic-117.html", frontend_api_key=frontend_api_key)


@web_bp.route("/152info")
def info_152():  # <-- Имя функции стало чище
    """Отдает HTML-страницу с информацией по ПД-152."""
    frontend_api_key = current_app.config.get("API_KEY_FRONTEND_CLIENT")
    return render_template("152info.html", frontend_api_key=frontend_api_key)


@web_bp.route("/117test")
def test_117():  # <-- Имя функции стало чище
    """Отдает HTML-страницу для тестирования по 117-ФЗ."""
    frontend_api_key = current_app.config.get("API_KEY_FRONTEND_CLIENT")
    return render_template("117-test.html", frontend_api_key=frontend_api_key)


@web_bp.route("/study-152")
def study_152():  # <-- Имя функции стало чище
    """Отдает HTML-страницу для общего обучения."""
    frontend_api_key = current_app.config.get("API_KEY_FRONTEND_CLIENT")
    return render_template("study-152.html", frontend_api_key=frontend_api_key)


@web_bp.route("/index-start")
def index_start():  # <-- Имя функции стало чище
    """Отдает стартовую главную страницу."""
    return render_template("index-start.html")


# =============================================================================
# АУТЕНТИФИКАЦИЯ АДМИНИСТРАТОРОВ
# =============================================================================


@web_bp.route("/login", methods=["GET", "POST"])
def login():
    """Обрабатывает аутентификацию пользователей."""
    if current_user.is_authenticated:
        return redirect(url_for("web.results"))  # Стало: web.results

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()

        if user is None or not user.check_password(form.password.data):
            flash("Неверный email или пароль", "danger")
            return redirect(url_for("web.login"))

        login_user(user, remember=form.remember_me.data)

        next_page = request.args.get("next") or url_for(
            "web.results"
        )  # Стало: web.results
        return redirect(next_page)

    return render_template("login.html", form=form)


@web_bp.route("/logout")
@login_required
def logout():
    """Выход пользователя из системы."""
    logout_user()
    return redirect(url_for("web.login"))


# =============================================================================
# СЛУЖЕБНЫЕ МАРШРУТЫ
# =============================================================================


@web_bp.route("/favicon.ico")
def favicon():
    """Отдает favicon.ico."""
    # Используем current_app.root_path, чтобы путь был правильным
    return send_from_directory(
        os.path.join(current_app.root_path, "static"),
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


@web_bp.route("/health")
def health_check():
    """
    Проверка работоспособности приложения. Используется Docker HEALTHCHECK.
    """
    return (
        jsonify({"status": "healthy", "timestamp": datetime.now(UTC).isoformat()}),
        200,
    )
