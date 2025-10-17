# app/auth/decorators.py

from functools import wraps

from flask import current_app, jsonify, request
from flask_login import current_user

from app.extensions import cache, db
from app.models import ApiKey


def api_key_required(f):
    """
    Продвинутый декоратор для защиты роутов с кэшированием и проверкой прав.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key_value = request.headers.get("X-API-Key")

        if not api_key_value:
            return jsonify({"status": "error", "message": "API key is required."}), 401

        # 1. Проверяем кэш в первую очередь
        cache_key = f"api_key:{api_key_value}"
        key_obj = cache.get(cache_key)

        if not key_obj:
            # 2. Если в кэше нет, идем в базу данных
            key_obj = ApiKey.query.filter_by(key=api_key_value, is_active=True).first()
            if key_obj:
                # 3. Если нашли, сохраняем в кэш на 5 минут
                cache.set(cache_key, key_obj, timeout=300)

        if not key_obj:
            return (
                jsonify({"status": "error", "message": "Invalid or inactive API key."}),
                401,
            )

        # 4. Проверяем права доступа к эндпоинту
        if not key_obj.is_allowed_endpoint(request.endpoint):
            current_app.logger.warning(
                f"API key '{key_obj.name}' attempted to access restricted endpoint '{request.endpoint}'"
            )
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Access to this endpoint is not allowed.",
                    }
                ),
                403,
            )

        # 5. Обновляем статистику
        # Чтобы избежать гонки состояний, используем F-выражение для инкремента
        ApiKey.query.filter_by(id=key_obj.id).update(
            {"last_used": db.func.now(), "usage_count": ApiKey.usage_count + 1}
        )
        db.session.commit()

        # Сохраняем объект ключа в запросе для возможного использования в
        request.api_key = key_obj
        return f(*args, **kwargs)

    return decorated_function


def admin_required(f):
    """
    Декоратор, требующий прав администратора.
    Проверяет ИЛИ сессию Flask-Login, ИЛИ API-ключ с флагом is_admin.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 1. Проверяем аутентификацию по сессии (для админ-панели)
        if current_user.is_authenticated and current_user.is_admin:
            return f(*args, **kwargs)
        # 2. Если сессии нет, проверяем аутентификацию по API-ключу
        api_key_value = request.headers.get("X-API-Key")
        if api_key_value:
            cache_key = f"api_key:{api_key_value}"
            key_obj = cache.get(cache_key)
            if not key_obj:
                key_obj = ApiKey.query.filter_by(
                    key=api_key_value, is_active=True
                ).first()
                if key_obj:
                    cache.set(cache_key, key_obj, timeout=300)
            # Проверяем, что ключ существует, активен И имеет флаг админа
            if key_obj and key_obj.is_admin:
                request.api_key = key_obj
                return f(*args, **kwargs)
        return jsonify({"status": "error", "message": "Admin privileges required"}), 403

    return decorated_function
