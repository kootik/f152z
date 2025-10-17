# Файл: tests/test_api_routes.py
import json
# --- ИЗМЕНЕНИЕ: Добавляем UTC и timezone для корректной работы с датами ---
from datetime import datetime, UTC, timezone
from tests.fixtures.sample_data import ResultMetadataFactory

# --- ВАШИ ТЕСТЫ АУТЕНТИФИКАЦИИ (без изменений) ---

def test_get_certificates_unauthorized(client):
    response = client.get('/api/get_certificates')
    assert response.status_code == 403
    assert json.loads(response.data)['message'] == 'Admin privileges required'

def test_get_certificates_as_admin_session(authenticated_client):
    response = authenticated_client.get('/api/get_certificates')
    assert response.status_code == 200
    assert isinstance(json.loads(response.data), list)

def test_get_certificates_as_admin_apikey(client, admin_api_headers):
    response = client.get('/api/get_certificates', headers=admin_api_headers)
    assert response.status_code == 200
    assert isinstance(json.loads(response.data), list)

def test_save_results_no_api_key(client):
    response = client.post('/api/save_results', data=json.dumps({}))
    assert response.status_code == 401

def test_save_results_with_valid_api_key(client, api_headers):
    response = client.post('/api/save_results', data=json.dumps({'test': 'data'}), headers=api_headers)
    assert response.status_code == 400

# --- ТЕСТЫ ИЗ ПЛАНА ---

def test_api_health_check(client):
    """Тест эндпоинта /health."""
    response = client.get('/health')
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'healthy'
    assert 'timestamp' in data

def test_log_event(client, db_session):
    """Тест эндпоинта /api/log_event."""
    event_data = {
        'sessionId': 'test-session-123',
        'eventType': 'focus_loss',
        # --- ИЗМЕНЕНИЕ: Используем правильный вызов datetime.now(UTC) ---
        'eventTimestamp': datetime.now(UTC).isoformat() + 'Z',
        'details': {'duration': 5}
    }
    response = client.post('/api/log_event', json=event_data, headers={'X-API-Key': 'client-api-key'})
    assert response.status_code == 200
    assert json.loads(response.data)['status'] == 'success'

def test_get_events_for_session(client, db_session, admin_api_headers):
    """Тест эндпоинта /api/get_events/<session_id>."""
    result = ResultMetadataFactory()
    response = client.get(f'/api/get_events/{result.session_id}', headers=admin_api_headers)
    assert response.status_code == 200
    assert isinstance(json.loads(response.data), list)

def test_get_full_result_not_found(client, admin_api_headers):
    """Тест получения несуществующего результата."""
    response = client.get('/api/get_full_result/non-existent-id', headers=admin_api_headers)
    assert response.status_code == 404
