# Файл: tests/test_models.py
from datetime import datetime, timedelta, timezone

import pytest

from app.models import ProctoringEvent, ResultMetadata, User
from tests.fixtures.sample_data import EventFactory, ResultMetadataFactory, UserFactory


class TestUserModel:
    def test_create_user(self, db_session):
        user = UserFactory(email="new@example.com")
        assert user.id is not None
        assert user.email == "new@example.com"

    def test_user_relationships(self, db_session):
        user = UserFactory()
        ResultMetadataFactory(user=user)
        ResultMetadataFactory(user=user)
        assert user.results.count() == 2


class TestResultMetadataModel:
    def test_result_duration(self, db_session):
        start = datetime.now(timezone.utc)
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
        event = EventFactory(
            event_type="screenshot_attempt", details={"filename": "scr.png"}
        )
        assert event.id is not None
        assert event.event_type == "screenshot_attempt"
        assert event.details["filename"] == "scr.png"
