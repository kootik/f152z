# Файл: tests/test_models.py
import pytest
# --- ИЗМЕНЕНИЕ: Добавляем UTC в импорт ---
from datetime import datetime, timedelta, UTC
from app.models import User, ResultMetadata, ProctoringEvent
from tests.fixtures.sample_data import UserFactory, ResultMetadataFactory, EventFactory

class TestUserModel:
    def test_create_user(self, db_session):
        user = UserFactory(email='new@example.com')
        assert user.id is not None
        assert user.email == 'new@example.com'
    
    def test_user_relationships(self, db_session):
        user = UserFactory()
        ResultMetadataFactory(user=user)
        ResultMetadataFactory(user=user)
        assert user.results.count() == 2

class TestResultMetadataModel:
    def test_result_duration(self, db_session):
        # Теперь эта строка будет работать, так как UTC импортирован
        start = datetime.now(UTC)
        end = start + timedelta(minutes=15)
        result = ResultMetadataFactory(start_time=start, end_time=end)
        
        assert result.duration_seconds == 900
        
    def test_result_passed_property(self, db_session):
        passed_result = ResultMetadataFactory(score=95)
        failed_result = ResultMetadataFactory(score=75)
        
        assert passed_result.passed is True
        assert failed_result.passed is False


class TestEventModel:
    def test_create_event(self, db_session):
        event = EventFactory(event_type='screenshot_attempt', details={'filename': 'scr.png'})
        assert event.id is not None
        assert event.event_type == 'screenshot_attempt'
        assert event.details['filename'] == 'scr.png'
