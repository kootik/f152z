# app/utils/document.py


from datetime import datetime, timezone

from app.models import DocumentCounter


def generate_document_number(db_session):
    """
    Генерирует следующий порядковый номер документа в формате ГГ/ММ-XXXX.
    Использует сессию SQLAlchemy и блокировку на уровне строк для предотвращения "гонки состояний".

    Args:
        db_session: Активная сессия SQLAlchemy.

    Returns:
        Строка с новым уникальным номером документа.
    """
    now = datetime.now(timezone.utc)
    current_period = now.strftime("%y/%m")

    # Находим счетчик для текущего периода и блокируем эту строку в таблице
    # до завершения транзакции, чтобы избежать одновременной генерации
    # одинаковых номеров при высокой нагрузке.
    counter = (
        db_session.query(DocumentCounter)
        .filter_by(period=current_period)
        .with_for_update()
        .first()
    )

    if counter:
        # Если запись за этот месяц уже есть, увеличиваем счетчик
        counter.last_sequence_number += 1
        next_seq = counter.last_sequence_number
    else:
        # Если это первый документ в этом месяце, создаем новую запись
        next_seq = 1
        counter = DocumentCounter(period=current_period, last_sequence_number=next_seq)
        db_session.add(counter)

    # Важно: сама функция не делает commit.
    # commit будет выполнен в вызывающем коде (в маршруте /save_results)
    # после того, как все операции с БД будут успешно подготовлены.

    return f"{current_period}-{next_seq:04d}"
