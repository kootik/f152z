# app/models.py
import secrets
from datetime import UTC, datetime

from flask_login import UserMixin
from sqlalchemy import Index
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


class User(db.Model, UserMixin):
    """Модель пользователя с полями для аутентификации."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    lastname = db.Column(db.String(100), nullable=False)  # Обязательное поле
    firstname = db.Column(db.String(100), nullable=False)
    middlename = db.Column(db.String(100))
    position = db.Column(db.String(200))
    persistent_id = db.Column(db.String(255), unique=True, index=True)

    # --- НОВЫЕ ПОЛЯ ДЛЯ АУТЕНТИФИКАЦИИ ---
    email = db.Column(db.String(120), unique=True, index=True, nullable=True)
    password_hash = db.Column(db.String(256))
    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    # ------------------------------------

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    results = db.relationship("ResultMetadata", backref="user", lazy="dynamic")

    def set_password(self, password):
        """Хэширует и устанавливает пароль пользователя."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Проверяет, соответствует ли предоставленный пароль хэшу."""
        return check_password_hash(self.password_hash or "", password)

    @property
    def full_name(self):
        parts = [self.lastname, self.firstname, self.middlename]
        return " ".join(filter(None, parts))

    def __repr__(self):
        return f"<User {self.id}: {self.full_name}>"


# --- НОВАЯ МОДЕЛЬ ДЛЯ API-КЛЮЧЕЙ ---
class ApiKey(db.Model):
    __tablename__ = "api_keys"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(128), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    last_used = db.Column(db.DateTime)

    # Новые поля
    usage_count = db.Column(db.Integer, default=0)
    rate_limit = db.Column(db.Integer, default=1000)  # requests per hour
    allowed_endpoints = db.Column(db.JSON)  # Список разрешенных эндпоинтов
    is_admin = db.Column(db.Boolean, default=False)  # <--- Важное дополнение

    @staticmethod
    def generate_key():
        """Генерирует безопасный случайный API-ключ."""
        return secrets.token_hex(32)  # Наш 64-символьный ключ

    def is_allowed_endpoint(self, endpoint):
        """Проверяет, есть ли у ключа доступ к эндпоинту."""
        if not self.allowed_endpoints:
            return True  # Если ограничений нет, разрешаем все

        for pattern in self.allowed_endpoints:
            # Позволяет использовать '*' как wildcard, например "api.*"
            if pattern == "*" or endpoint.startswith(pattern):
                return True
        return False

    def record_usage(self):
        """Записывает факт использования ключа."""
        self.last_used = db.func.now()
        self.usage_count = ApiKey.usage_count + 1


class Fingerprint(db.Model):
    """Browser fingerprint model."""

    __tablename__ = "fingerprints"

    fingerprint_hash = db.Column(db.String(64), primary_key=True)
    user_agent = db.Column(db.Text, nullable=True)
    platform = db.Column(db.String(100), nullable=True)
    webgl_renderer = db.Column(db.String(200), nullable=True)
    first_seen = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    last_seen = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    results = db.relationship("ResultMetadata", backref="fingerprint", lazy="dynamic")

    __table_args__ = (Index("idx_fingerprint_last_seen", "last_seen"),)


class ResultMetadata(db.Model):
    """Test result metadata model."""

    __tablename__ = "result_metadata"

    session_id = db.Column(db.String(128), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    fingerprint_hash = db.Column(
        db.String(64), db.ForeignKey("fingerprints.fingerprint_hash"), nullable=True
    )
    test_type = db.Column(db.String(50), nullable=True, index=True)
    score = db.Column(db.Integer, nullable=True)
    start_time = db.Column(db.DateTime, nullable=True, index=True)
    end_time = db.Column(db.DateTime, nullable=True)
    raw_data = db.Column(db.JSON, nullable=True)  # <--- ИСПРАВЛЕНО
    document_number = db.Column(db.String(20), nullable=True, unique=True)
    client_ip = db.Column(db.String(45), nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    # Relationships
    certificate = db.relationship("Certificate", backref="result", uselist=False)
    events = db.relationship("ProctoringEvent", backref="result", lazy="dynamic")

    __table_args__ = (
        Index("idx_result_user_score", "user_id", "score"),
        Index("idx_result_created", "created_at"),
    )

    @property
    def duration_seconds(self):
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0

    @property
    def passed(self):
        # Рекомендую использовать порог из конфига для консистентности
        passing_score = 80  # или current_app.config.get('PASSING_SCORE_THRESHOLD', 80)
        return self.score >= passing_score if self.score is not None else False


class ProctoringEvent(db.Model):
    """Proctoring event model."""

    __tablename__ = "proctoring_events"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.String(128),
        db.ForeignKey("result_metadata.session_id"),
        nullable=False,
        index=True,
    )
    event_type = db.Column(db.String(64), nullable=False, index=True)
    event_timestamp = db.Column(db.DateTime, nullable=False, index=True)
    details = db.Column(db.JSON, nullable=True)
    persistent_id = db.Column(db.String(255), nullable=True, index=True)
    client_ip = db.Column(db.String(45), nullable=True)
    page = db.Column(db.String(100), nullable=True, index=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "event_timestamp": (
                self.event_timestamp.isoformat() if self.event_timestamp else None
            ),
            "details": self.details,
            "persistent_id": self.persistent_id,
            "client_ip": self.client_ip,
            "page": self.page,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    __table_args__ = (
        Index("idx_event_session_type", "session_id", "event_type"),
        Index("idx_event_timestamp", "event_timestamp"),
        Index("idx_event_page_type", "page", "event_type"),
    )


class Certificate(db.Model):
    """Certificate model."""

    __tablename__ = "certificates"

    document_number = db.Column(db.String(20), primary_key=True)
    user_fullname = db.Column(db.String(300), nullable=False)
    user_position = db.Column(db.String(200), nullable=False)
    test_type = db.Column(db.String(50), nullable=False)
    issue_date = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    score_percentage = db.Column(db.Integer, nullable=False)
    session_id = db.Column(
        db.String(128),
        db.ForeignKey("result_metadata.session_id"),
        nullable=False,
        unique=True,
    )
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    def to_dict(self):
        return {
            "document_number": self.document_number,
            "user_fullname": self.user_fullname,
            "user_position": self.user_position,
            "test_type": self.test_type,
            "issue_date": self.issue_date.isoformat() if self.issue_date else None,
            "score_percentage": self.score_percentage,
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    __table_args__ = (
        Index("idx_cert_issue_date", "issue_date"),
        Index("idx_cert_user", "user_fullname"),
    )


class DocumentCounter(db.Model):
    """Document counter for sequential numbering."""

    __tablename__ = "document_counters"

    period = db.Column(db.String(10), primary_key=True)  # Format: YY/MM
    last_sequence_number = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (Index("idx_counter_updated", "updated_at"),)


# --- 👇 НОВАЯ МОДЕЛЬ ДЛЯ НАСТРОЕК 👇 ---
class SystemSetting(db.Model):
    """
    Хранилище Key-Value для системных настроек,
    которые можно редактировать через админ-панель.
    """

    __tablename__ = "system_settings"

    # Ключ (например, 'ORG_NAME', 'SIGNATORY_1_NAME')
    key = db.Column(db.String(128), primary_key=True)

    # Значение (например, 'Министерство...')
    value = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<SystemSetting {self.key}>"
