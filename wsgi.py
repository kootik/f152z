import os

from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

from app import create_app

# Создаем приложение, используя переменную окружения FLASK_ENV
app = create_app(os.getenv("FLASK_ENV") or "development")

if __name__ == "__main__":
    # Используем SocketIO для запуска, чтобы WebSocket работал
    from app.extensions import socketio

    socketio.run(app)
