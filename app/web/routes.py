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


@web_bp.route("/")
def index():
    """Отдает главную страницу с тестом."""
    return render_template("index.html")


@web_bp.route("/index2")
def show_index2_page():
    """Отдает альтернативную главную страницу."""
    return render_template("index2.html")


@web_bp.route("/results")
@login_required
def show_results_page():
    """Отдает HTML-страницу для отображения результатов."""
    return render_template("display_results.html")


@web_bp.route("/152test")
def show_152test_page():
    """Отдает HTML-страницу для тестирования ПД-152."""
    return render_template("studytest.html")


@web_bp.route("/117infographic")
def show_117infographic_page():
    """Отдает HTML-страницу с инфографикой для 117-ФЗ."""
    return render_template("infographic-117.html")


@web_bp.route("/117study")
def show_117study_page():
    """Отдает HTML-страницу для обучения по 117-ФЗ."""
    return render_template("study-117.html")


@web_bp.route("/152info")
def show_152info_page():
    """Отдает HTML-страницу с информацией по ПД-152."""
    return render_template("152info.html")


@web_bp.route("/117test")
def show_117test_page():
    """Отдает HTML-страницу для тестирования по 117-ФЗ."""
    return render_template("117-test.html")


@web_bp.route("/study")
def show_study_page():
    """Отдает HTML-страницу для общего обучения."""
    return render_template("study.html")


@web_bp.route("/index-start")
def show_index_start_page():
    """Отдает стартовую главную страницу."""
    return render_template("index-start.html")


@web_bp.route("/login", methods=["GET", "POST"])
def login():
    # Если пользователь уже вошел, перенаправляем его на главную
    if current_user.is_authenticated:
        return redirect(url_for("web.show_results_page"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()

        if user is None or not user.check_password(form.password.data):
            flash("Неверный email или пароль", "danger")
            return redirect(url_for("web.login"))

        login_user(user, remember=form.remember_me.data)

        # Перенаправление на страницу, которую пользователь хотел посетить, или на /results
        next_page = request.args.get("next")
        if not next_page:
            next_page = url_for("web.show_results_page")
        return redirect(next_page)

    return render_template("login.html", form=form)


@web_bp.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("web.login"))


# =============================================================================
# МАРШРУТЫ ДЛЯ СТАТИЧЕСКИХ ФАЙЛОВ И РЕСУРСОВ
# В production-среде с Nginx эти маршруты могут не использоваться,
# так как Nginx будет отдавать статику напрямую. Но для разработки они нужны.
# =============================================================================


@web_bp.route("/questions_data-117.js")
def serve_questions_data117():
    """Отдает файл questions_data-117.js из статической папки."""
    return send_from_directory(current_app.static_folder, "questions_data-117.js")


@web_bp.route("/questions_data.js")
def serve_questions_data():
    """Отдает файл questions_data.js из статической папки."""
    return send_from_directory(current_app.static_folder, "questions_data.js")


@web_bp.route("/jspdf.umd.min.js")
def serve_jspdf():
    """Отдает файл jspdf.umd.min.js из статической папки."""
    return send_from_directory(current_app.static_folder, "jspdf.umd.min.js")


@web_bp.route("/jspdf.umd.min.js.map")
def serve_jspdf_map():
    """Отдает файл jspdf.umd.min.js.map из статической папки."""
    return send_from_directory(current_app.static_folder, "jspdf.umd.min.js.map")


@web_bp.route("/html2canvas.min.js")
def serve_html2canvas():
    """Отдает файл html2canvas.min.js из статической папки."""
    return send_from_directory(current_app.static_folder, "html2canvas.min.js")


@web_bp.route("/FKGroteskNeue.woff2")
def serve_FKGroteskNeue():
    """Отдает файл FKGroteskNeue.woff2 из статической папки."""
    return send_from_directory(current_app.static_folder, "FKGroteskNeue.woff2")


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
    """Простая проверка работоспособности приложения."""
    return (
        jsonify({"status": "healthy", "timestamp": datetime.now(UTC).isoformat()}),
        200,
    )
