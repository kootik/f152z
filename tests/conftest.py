# tests/conftest.py

import pytest

from app import create_app
from app.extensions import db as _db  # Use _db to avoid conflicts
from app.models import ApiKey, User
from tests.fixtures.sample_data import ApiKeyFactory, UserFactory


@pytest.fixture(scope="session")
def app():
    """
    Creates a test Flask app instance for the entire test session.
    It seeds the database with essential data once per session.
    """
    app = create_app("testing")
    with app.app_context():
        _db.create_all()

        # Create base users and API keys available for all tests
        UserFactory(email="admin@test.com", is_admin=True, id=1)
        UserFactory(email="user@test.com", is_admin=False, id=2)
        ApiKeyFactory(key="admin-api-key", name="Admin Test Key", is_admin=True)
        ApiKeyFactory(key="client-api-key", name="Client Test Key", is_admin=False)
        UserFactory.reset_sequence(3)

        yield app

        _db.drop_all()


@pytest.fixture(scope="function")
def db_session(app):
    """
    Provides a clean DB session for each test by rolling back changes afterwards.
    This is the simple, correct, and performant way to ensure test isolation.
    """
    with app.app_context():
        # Yield the session so the test can use it
        yield _db.session

        # After the test is done, rollback any changes to ensure the DB is clean
        _db.session.rollback()
        _db.session.remove()


@pytest.fixture(scope="function")
def client(app):
    """Creates a standard, unauthenticated test client."""
    return app.test_client()


@pytest.fixture(scope="function")
def authenticated_client(app, db_session):
    """
    Creates a test client with a pre-authenticated admin user session.
    """
    admin_user = User.query.filter_by(email="admin@test.com").first()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_user.id)
        sess["_fresh"] = True
    return client


@pytest.fixture(scope="function")
def api_headers():
    """Returns headers with a standard, non-admin API key."""
    return {"X-API-Key": "client-api-key", "Content-Type": "application/json"}


@pytest.fixture(scope="function")
def admin_api_headers():
    """Returns headers with a powerful admin API key."""
    return {"X-API-Key": "admin-api-key", "Content-Type": "application/json"}

@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    with patch('app.extensions.redis_client') as mock:
        yield mock
