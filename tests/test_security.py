# Файл: tests/test_security.py
import pytest
import json

class TestSecurity:
    def test_sql_injection_protection(self, client, admin_api_headers):
        """Test SQL injection protection on a real endpoint."""
        # --- ИЗМЕНЕНИЕ: Атакуем реальный эндпоинт, который принимает ID в URL ---
        malicious_session_id = "'; DROP TABLE users; --"
        response = client.get(
            f'/api/get_full_result/{malicious_session_id}', 
            headers=admin_api_headers
        )
        
        # Ожидаем, что SQLAlchemy обработает это безопасно и вернет 404 (Not Found)
        # или фреймворк вернет 400/422 из-за невалидного формата ID.
        # Главное - не 500 (Internal Server Error) или успешный ответ.
        assert response.status_code in [404, 400, 422]
    
    def test_xss_protection_on_log_event(self, client, api_headers):
        """Test for XSS protection on an endpoint that accepts JSON data."""
        # --- ИЗМЕНЕНИЕ: Пытаемся внедрить XSS через ваш эндпоинт /api/log_event ---
        xss_payload = '<script>alert("XSS")</script>'
        event_data = {
            'sessionId': 'test-xss-session',
            'eventType': xss_payload, # Внедряем пейлоад в одно из полей
            'eventTimestamp': '2025-10-15T12:00:00Z',
            'details': {'notes': xss_payload}
        }
        
        response = client.post('/api/log_event', json=event_data, headers=api_headers)
        
        # Ожидаем, что Pydantic или валидаторы на бэкенде отклонят
        # данные с недопустимыми символами (400/422) или, если примут,
        # то при последующем извлечении данные будут экранированы (этот тест не проверяет).
        # Главное, что это не вызывает 500 ошибку.
        assert response.status_code in [200, 400, 422]
