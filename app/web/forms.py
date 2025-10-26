# app/web/forms.py

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length


class LoginForm(FlaskForm):
    """
    Форма для входа пользователей.

    Включает валидацию на стороне сервера и атрибуты
    для более удобного рендеринга в HTML.
    """

    # --- Поле Email ---
    # Используем аннотацию типа StringField для ясности кода.
    email: StringField = StringField(
        "Email",
        validators=[
            DataRequired(message="Email не может быть пустым."),
            Email(message="Введите корректный email адрес."),
        ],
        render_kw={"placeholder": "user@example.com", "autocomplete": "email"},
    )

    # --- Поле Пароля ---
    password: PasswordField = PasswordField(
        "Пароль",
        validators=[
            DataRequired(message="Пароль не может быть пустым."),
            Length(min=8, message="Пароль должен быть не менее 8 символов."),
        ],
        render_kw={"placeholder": "••••••••", "autocomplete": "current-password"},
    )

    # --- Чекбокс "Запомнить меня" ---
    remember_me: BooleanField = BooleanField("Запомнить меня")

    # --- Кнопка Отправки ---
    submit: SubmitField = SubmitField("Войти")
