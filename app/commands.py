# app/commands.py
import os
import shutil

import click
from flask import current_app
from flask.cli import with_appcontext

from app.extensions import cache, db
from app.models import ApiKey, SystemSetting, User


@click.command("init-settings")
@with_appcontext
def init_settings_command():
    """Инициализирует системные настройки значениями по умолчанию."""

    # Ключи и значения по умолчанию из вашего HTML
    settings = [
        ("ORG_NAME", "Министерство природных ресурсов Краснодарского края"),
        ("ORG_ADDRESS_LINE_1", "350020, г. Краснодар, ул. Северная, д. 275/1"),
        (
            "ORG_CONTACTS",
            "Тел.: +7 (861) 279-00-49, E-mail: mprkk@krasnodar.ru, Сайт: mpr.krasnodar.ru",
        ),
        ("SIGNATORY_1_TITLE", "Начальник отдела ИТОиЗИ"),
        ("SIGNATORY_1_NAME", "Кучуров Е.В."),
        ("SIGNATORY_2_TITLE", "Главный консультант отдела ИТОиЗИ"),
        ("SIGNATORY_2_NAME", "Каныгин А.С."),
    ]

    count = 0
    for key, value in settings:
        setting = db.session.get(SystemSetting, key)
        if not setting:
            setting = SystemSetting(key=key, value=value)
            db.session.add(setting)
            count += 1
    if count > 0:
        db.session.commit()
        click.echo(f"Успешно инициализировано {count} новых системных настроек.")
    else:
        click.echo("Все системные настройки уже существуют. Пропускаем.")


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


@click.command("collect")
@with_appcontext
def collect_static_command():
    """Собирает статические файлы в директорию STATIC_ROOT."""
    static_folder = current_app.static_folder
    # Путь назначения - это тот же путь, что и в Docker-томе (/app/static)
    # Мы берем его из конфигурации Flask для гибкости.
    destination = current_app.config.get("STATIC_ROOT", static_folder)

    if not os.path.exists(destination):
        os.makedirs(destination)

    # Очищаем старую статику перед копированием новой
    for item in os.listdir(destination):
        item_path = os.path.join(destination, item)
        if os.path.isdir(item_path):
            shutil.rmtree(item_path)
        else:
            os.remove(item_path)

    click.echo(f"Очищена директория: {destination}")

    # Копируем файлы
    shutil.copytree(static_folder, destination, dirs_exist_ok=True)
    click.echo(f"Статические файлы скопированы из {static_folder} в {destination}")


def register_commands(app):
    """Регистрирует CLI-команды в приложении."""
    app.cli.add_command(create_admin)
    app.cli.add_command(create_apikey)
    app.cli.add_command(revoke_apikey)
    app.cli.add_command(collect_static_command)
    app.cli.add_command(init_settings_command)
