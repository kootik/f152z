import logging
import os
import sys

from flask import Flask, request
from flask_migrate import Migrate
from flask_wtf.csrf import generate_csrf
from prometheus_flask_exporter import PrometheusMetrics
from werkzeug.middleware.proxy_fix import ProxyFix

from app.api.websocket import redis_subscriber
from app.extensions import (cache, cors, csrf, db, limiter, login_manager,
                            redis_client, socketio)
from config import config_by_name

from .api.websocket import redis_subscriber
from .utils.logging import setup_structured_logging


def create_app(config_name=None):
    """Application factory pattern implementation."""
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")

    app = Flask(__name__)

    app.config.from_object(config_by_name[config_name])

    setup_structured_logging(app)
    init_extensions(app)

    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User

        return db.session.get(User, int(user_id))

    PrometheusMetrics(app)

    socketio.start_background_task(target=redis_subscriber, app=app)

    register_blueprints(app)

    register_error_handlers(app)
    from app import commands

    commands.register_commands(app)

    @app.after_request
    def inject_csrf_token(response):
        """
        Отправляет cookie с CSRF-токеном после каждого запроса.
        Это позволяет JavaScript-клиентам (например, api.js) его считывать.
        """
        response.set_cookie(
            "csrf_token",
            generate_csrf(),
            secure=app.config.get("SESSION_COOKIE_SECURE", True),
            samesite=app.config.get("SESSION_COOKIE_SAMESITE", "Strict"),
            httponly=False,
        )
        return response

    if app.config.get("USE_PROXY_FIX"):
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=app.config.get("PROXY_FIX_X_FOR", 1),
            x_proto=app.config.get("PROXY_FIX_X_PROTO", 1),
            x_host=app.config.get("PROXY_FIX_X_HOST", 1),
            x_port=app.config.get("PROXY_FIX_X_PORT", 1),
        )

    with app.app_context():
        print("--- СПИСОК ЗАРЕГИСТРИРОВАННЫХ URL ---")
        rules = []
        for rule in app.url_map.iter_rules():
            rules.append(
                {
                    "Endpoint": rule.endpoint,
                    "Methods": ",".join(rule.methods),
                    "URL": str(rule),
                }
            )
        for r in sorted(rules, key=lambda x: x["URL"]):
            print(
                f"URL: {r['URL']:<40} Endpoint: {r['Endpoint']:<30} Methods: {r['Methods']}"
            )
        print("------------------------------------")

    return app


def register_error_handlers(app):
    from flask import jsonify

    @app.errorhandler(413)
    def request_entity_too_large(error):
        return jsonify({"status": "error", "message": "Request entity too large"}), 413

    @app.errorhandler(429)
    def ratelimit_handler(error):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"Rate limit exceeded. {error.description}",
                }
            ),
            429,
        )

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        current_app.logger.error(f"Internal server error: {error}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500


def init_extensions(app):
    """Initialize Flask extensions."""
    db.init_app(app)

    Migrate(app, db, directory="migrations")

    csrf.init_app(app)

    cors.init_app(
        app, origins=app.config.get("CORS_ORIGINS", []), supports_credentials=True
    )

    cache.init_app(app)

    limiter.init_app(app)

    socketio.init_app(
        app,
        cors_allowed_origins=app.config.get("CORS_ORIGINS", []),
        message_queue=app.config.get("SOCKETIO_MESSAGE_QUEUE"),
        async_mode="eventlet",
    )

    if app.config.get("REDIS_URL"):
        redis_client.init_app(app)


def register_blueprints(app):
    """Register application blueprints."""
    from app.api import api_bp
    from app.web import web_bp

    app.register_blueprint(api_bp, url_prefix="/api")

    app.register_blueprint(web_bp)


def register_error_handlers(app):
    """Register global error handlers."""
    from flask import jsonify

    @app.errorhandler(413)
    def request_entity_too_large(error):
        return jsonify({"status": "error", "message": "Request entity too large"}), 413

    @app.errorhandler(429)
    def ratelimit_handler(error):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Rate limit exceeded",
                    "retry_after": error.description,
                }
            ),
            429,
        )

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        app.logger.error(f"Internal server error: {error}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({"status": "error", "message": "Resource not found"}), 404
