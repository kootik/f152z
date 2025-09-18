# file: tests/test_app.py

import pytest
from app import app as flask_app

@pytest.fixture
def app():
    """Create and configure a new app instance for each test."""
    yield flask_app

@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()

def test_index_route(client):
    """
    Тест для проверки, что главная страница (/) загружается успешно.
    """
    response = client.get('/')
    assert response.status_code == 200
	assert "Основы информационной безопасности".encode('utf-8') in response.data
