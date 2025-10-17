# app/commands.py

import click
from flask.cli import with_appcontext

from app.extensions import cache, db
from app.models import ApiKey, User


@click.command("create-admin")
@with_appcontext
@click.argument("email")
@click.argument("password")
@click.option("--firstname", default="Admin")
@click.option("--lastname", default="User")
@click.option("--position", default="System Administrator")
def create_admin(email, password, firstname, lastname, position):
    """Создает нового пользователя с правами администратора."""
    if User.query.filter_by(email=email).first():
        click.echo("Пользователь с таким email уже существует.")
        return

    admin = User(
        email=email,
        firstname=firstname,
        lastname=lastname,
        position=position,
        is_admin=True,
        # Добавляем временный persistent_id, т.к. поле обязательное
        persistent_id=f"admin_{email}",
    )
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    click.echo(f"Администратор {email} успешно создан.")


@click.command("create-apikey")
@with_appcontext
@click.argument("name")
@click.option("--description", help="Описание ключа.")
@click.option(
    "--endpoints",
    help='Разрешенные эндпоинты через запятую (напр., "api.log_event,api.save_results").',
)
@click.option("--admin", is_flag=True, help="Дает ключу права администратора.")
def create_apikey(name, description, endpoints, admin):
    """Генерирует новый API-ключ с дополнительными параметрами."""
    key_value = ApiKey.generate_key()
    if admin:
        endpoints = "*"  # Админ-ключ имеет доступ ко всему
    api_key = ApiKey(
        key=key_value,
        name=name,
        description=description,
        allowed_endpoints=endpoints.split(",") if endpoints else None,
        is_admin=admin,
    )
    db.session.add(api_key)
    db.session.commit()

    click.echo("API-ключ успешно создан:")
    click.echo(f"  Имя: {name}")
    click.echo(f"  Ключ: {key_value}")
    if admin:
        click.echo("  Доступ: * (АДМИНИСТРАТОР)")
    elif endpoints:
        click.echo(f"  Доступ: {endpoints}")
    else:
        click.echo("  Доступ: * (Все эндпоинты)")
    click.echo("Сохраните этот ключ! Он больше не будет показан.")


@click.command("revoke-apikey")
@with_appcontext
@click.argument("key")
def revoke_apikey(key):
    """Отзывает (деактивирует) API-ключ."""
    api_key = ApiKey.query.filter_by(key=key).first()
    if not api_key:
        click.echo("API-ключ не найден!")
        return
    api_key.is_active = False
    db.session.commit()
    # Очищаем кэш для этого ключа
    cache.delete(f"api_key:{key}")
    click.echo(f"API-ключ '{api_key.name}' был отозван.")


def register_commands(app):
    """Регистрирует CLI-команды в приложении."""
    app.cli.add_command(create_admin)
    app.cli.add_command(create_apikey)
    app.cli.add_command(revoke_apikey)
