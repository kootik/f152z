# Файл: tests/fixtures/sample_data.py
from datetime import UTC, datetime

import factory
from faker import Faker

from app.extensions import db
from app.models import ApiKey, ProctoringEvent, ResultMetadata, User

fake = Faker()


# --- Ваша фабрика UserFactory (без изменений) ---
class UserFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = User
        sqlalchemy_session = db.session
        sqlalchemy_session_persistence = "commit"

    id = factory.Sequence(lambda n: n)
    firstname = factory.Faker("first_name")
    lastname = factory.Faker("last_name")
    email = factory.Faker("email")
    is_admin = False
    persistent_id = factory.Faker("uuid4")

    @factory.lazy_attribute
    def password_hash(self):
        return "fake_password_hash"


# --- Ваша фабрика ApiKeyFactory (без изменений) ---
class ApiKeyFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = ApiKey
        sqlalchemy_session = db.session
        sqlalchemy_session_persistence = "commit"

    id = factory.Sequence(lambda n: n)
    key = factory.LazyFunction(ApiKey.generate_key)
    name = factory.Faker("company")
    is_active = True
    is_admin = False


# --- НОВАЯ АДАПТИРОВАННАЯ ФАБРИКА ---
# Вместо SessionFactory создаем ResultMetadataFactory
class ResultMetadataFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = ResultMetadata
        sqlalchemy_session = db.session
        sqlalchemy_session_persistence = "commit"

    session_id = factory.Faker("uuid4")
    start_time = factory.LazyFunction(lambda: datetime.now(UTC))
    test_type = "main_test"
    user = factory.SubFactory(UserFactory)


# --- НОВАЯ АДАПТИРОВАННАЯ ФАБРИКА ---
# EventFactory теперь ссылается на 'result', а не 'session'
class EventFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = ProctoringEvent
        sqlalchemy_session = db.session
        sqlalchemy_session_persistence = "commit"

    event_type = "focus_loss"
    event_timestamp = factory.LazyFunction(lambda: datetime.now(UTC))
    details = factory.LazyFunction(lambda: {"duration": fake.random_int(1, 10)})
    # Связь называется 'result' в вашей модели ResultMetadata
    result = factory.SubFactory(ResultMetadataFactory)
