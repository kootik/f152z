# app/api/websocket.py
import json

import redis  # Импортируем redis здесь
from flask_socketio import emit, join_room, leave_room

from app.extensions import redis_client, socketio
from app.metrics import ACTIVE_WEBSOCKET_CONNECTIONS


@socketio.on("connect")
def handle_connect():
    """Handle client connection."""
    # Увеличиваем значение датчика при подключении
    ACTIVE_WEBSOCKET_CONNECTIONS.inc()
    emit("connected", {"data": "Connected to server"})


@socketio.on("disconnect")
def handle_disconnect():
    """Handle client disconnection."""
    # Уменьшаем значение датчика при отключении
    ACTIVE_WEBSOCKET_CONNECTIONS.dec()
    pass


@socketio.on("join")
def handle_join(data):
    """Join a room for targeted updates."""
    room = data.get("room", "general")
    join_room(room)
    emit("joined", {"room": room}, room=room)


@socketio.on("leave")
def handle_leave(data):
    """Leave a room."""
    room = data.get("room", "general")
    leave_room(room)
    emit("left", {"room": room}, room=room)


def notify_clients_of_update(update_type="update_needed", data=None):
    """
    Уведомляет всех клиентов об обновлении через Redis pub/sub.
    Эта функция ТОЛЬКО публикует сообщение.
    """
    message = {"type": update_type, "data": data or {}}
    # Публикуем в Redis. Подписчики во всех процессах получат это сообщение.
    redis_client.publish("updates", json.dumps(message))


def redis_subscriber(app):
    """
    Подписчик Redis для межпроцессных WebSocket-событий.
    Эта функция запускается в фоновом потоке и требует явного контекста приложения.

    Args:
        app: Экземпляр приложения Flask.
    """
    # Создаем контекст приложения вручную.
    # Теперь внутри этого блока `with` мы можем безопасно использовать
    # `current_app` или напрямую `app.config`.
    with app.app_context():
        # Создаем новое подключение к Redis специально для этого потока.
        # Нельзя переиспользовать `redis_client` из extensions, так как он
        # может быть привязан к другому потоку.
        r = redis.from_url(app.config["REDIS_URL"])
        pubsub = r.pubsub()
        pubsub.subscribe("updates")

        print("Redis subscriber thread started...")  # Логирование для отладки

        for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    # `socketio.emit` нужно вызывать вне контекста `with`,
                    # но так как мы находимся в фоновом потоке, запущенном
                    # самим SocketIO, он знает, как правильно обработать этот вызов.
                    socketio.emit(
                        data["type"],
                        data.get("data", {}),
                        broadcast=True,
                        namespace="/",
                    )
                except json.JSONDecodeError:
                    app.logger.warning("Could not decode JSON from Redis pub/sub.")
                except Exception as e:
                    app.logger.error(f"Error in Redis subscriber: {e}")
