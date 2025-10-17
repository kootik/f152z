# app/utils/logging.py
import logging
import time
import uuid

from flask import g, request
from pythonjsonlogger.json import JsonFormatter


class RequestIdJsonFormatter(JsonFormatter):
    """Custom formatter that adds request_id to all log records."""

    def add_fields(self, log_record, record, message_dict):
        super(RequestIdJsonFormatter, self).add_fields(log_record, record, message_dict)
        if g and hasattr(g, "request_id"):
            log_record["request_id"] = g.request_id


def setup_structured_logging(app):
    """Setup structured logging with Request ID and timings."""

    logHandler = logging.StreamHandler()
    formatter = RequestIdJsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logHandler.setFormatter(formatter)

    app.logger.handlers.clear()
    app.logger.addHandler(logHandler)
    app.logger.setLevel(logging.INFO if not app.debug else logging.DEBUG)

    @app.before_request
    def before_request_logging():
        """Logs the start of the request and generates a request_id."""
        g.request_id = str(uuid.uuid4())
        g.start_time = time.monotonic()

        app.logger.info(
            "Request started",
            extra={
                "request_info": {
                    "method": request.method,
                    "url": request.url,
                    "path": request.path,
                    "ip": request.remote_addr,
                    "user_agent": request.headers.get("User-Agent"),
                }
            },
        )

    @app.after_request
    def after_request_logging(response):
        """Logs the end of the request, status, and duration."""

        # <--- ИЗМЕНЕНИЕ ЗДЕСЬ: Безопасный доступ к g.start_time --->
        # Используем getattr, чтобы избежать ошибки, если start_time не был установлен
        start_time = getattr(g, "start_time", None)
        duration_ms = -1  # Значение по умолчанию, если время начала неизвестно

        if start_time is not None:
            duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        # -----------------------------------------------------------------

        app.logger.info(
            "Request finished",
            extra={
                "response_info": {
                    "status_code": response.status_code,
                    "mimetype": response.mimetype,
                    "content_length": response.content_length,
                    "duration_ms": duration_ms,
                }
            },
        )
        if hasattr(g, "request_id"):
            response.headers["X-Request-ID"] = g.request_id
        return response

    @app.teardown_request
    def teardown_request_logging(exception=None):
        """Logs if an exception occurred during the request."""
        if exception:
            app.logger.error("Unhandled exception during request", exc_info=exception)
