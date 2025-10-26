# app/api/routes.py

import json
from datetime import UTC, datetime, timedelta

from flask import current_app, jsonify, request
from pydantic import ValidationError
from sqlalchemy import Text, and_, case, cast, distinct, func, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.auth.decorators import admin_required, api_key_required
from app.extensions import cache, csrf, db, limiter, socketio
from app.metrics import (
    ANALYSIS_DURATION_SECONDS,
    DOCUMENTS_GENERATED_TOTAL,
    TESTS_COMPLETED_TOTAL,
)
from app.models import (
    Certificate,
    DocumentCounter,
    Fingerprint,
    ProctoringEvent,
    ResultMetadata,
    User,
)
from app.schemas.result_schema import SaveResultsRequest
from app.utils.analytics import (
    compare_mouse_trajectories,
    find_result_file_by_session_id,
)
from app.utils.document import generate_document_number
from app.utils.sanitizers import sanitize_filename
from app.utils.validators import (
    validate_event_type,
    validate_json_data,
    validate_session_id,
)

from . import api_bp

ALLOWED_PRESETS = {"all", "today", "week", "anomalies"}

# --- 1.4: Лимиты размера запроса ---
MAX_LOG_EVENT_SIZE = 1 * 1024 * 1024  # 1MB
MAX_SAVE_RESULTS_SIZE = 10 * 1024 * 1024  # 10MB (для данных с движениями мыши)
# 1.1: АТОМАРНАЯ ФУНКЦИЯ СОЗДАНИЯ ПОЛЬЗОВАТЕЛЯ (для save_results)
# =============================================================================


def get_or_create_user(session, persistent_id, user_info_schema):
    """
    Атомарно получает или создает пользователя, используя 'ON CONFLICT DO NOTHING'.
    Это решает проблему race condition.
    """
    if not user_info_schema:
        current_app.logger.warning(
            f"get_or_create_user: userInfo is missing for persistent_id {persistent_id}"
        )
        # Возвращаем None, чтобы save_results мог выдать ошибку 400
        return None

    # Данные из Pydantic схемы
    user_data = {
        "lastname": user_info_schema.lastName,
        "firstname": user_info_schema.firstName,
        "middlename": user_info_schema.middleName,
        "position": user_info_schema.position,
        "persistent_id": persistent_id,
        "created_at": datetime.now(UTC),  # Устанавливаем время создания
        "updated_at": datetime.now(UTC),  # Устанавливаем время обновления
    }

    # 1. Пытаемся вставить, игнорируя конфликт, если persistent_id уже существует
    stmt = (
        insert(User)
        .values(**user_data)
        .on_conflict_do_nothing(
            index_elements=[
                "persistent_id"
            ]  # Убедитесь, что у вас есть unique constraint/index 'ix_users_persistent_id'
        )
    )
    session.execute(stmt)

    # 2. Теперь *гарантированно* получаем пользователя
    # (Либо он был только что создан, либо он уже существовал)
    # Используем .one(), т.к. пользователь теперь обязан существовать
    try:
        user = session.query(User).filter_by(persistent_id=persistent_id).one()
        # (Опционально) Обновляем данные, если пользователь уже существовал, а данные изменились
        if user.lastname != user_data["lastname"]:
            user.lastname = user_data["lastname"]
            user.firstname = user_data["firstname"]
            user.middlename = user_data["middlename"]
            user.position = user_data["position"]
            user.updated_at = datetime.now(UTC)

        return user
    except Exception as e:
        current_app.logger.error(
            f"Failed to get_or_create user {persistent_id} after UPSERT: {e}",
            exc_info=True,
        )
        return None


# =============================================================================
# API ЭНДПОИНТЫ
# =============================================================================
@api_bp.route("/save_results", methods=["POST"])
@limiter.limit("30 per minute")  # 1.1: Увеличен лимит
@api_key_required
# @csrf.exempt # <-- 1.5: УДАЛЕНО. CSRF-защита включена
def save_results():
    """
    Атомарно обновляет существующую запись о результатах теста.
    """
    # --- 1.4: ИСПРАВЛЕНИЕ DoS ---
    if request.content_length > MAX_SAVE_RESULTS_SIZE:
        return (
            jsonify({"status": "error", "message": "Result payload is too large"}),
            413,
        )
    try:
        # Pydantic-валидация входящего JSON. Этот блок работает корректно.
        validated_data = SaveResultsRequest.model_validate(request.get_json())
    except ValidationError as e:
        current_app.logger.warning(
            f"Invalid data from {request.remote_addr}: {e.errors()}"
        )
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Invalid input data",
                    "details": e.errors(),
                }
            ),
            400,
        )

    # <--- ИЗМЕНЕНИЕ: Работаем напрямую с Pydantic-объектом, а не со словарем ---
    session_id = validated_data.sessionId
    persistent_id = validated_data.persistentId.get("cookie")
    fp_hash = validated_data.fingerprint.get("privacySafeHash")

    if not persistent_id or not fp_hash:
        return (
            jsonify({"status": "error", "message": "Missing required identifiers"}),
            400,
        )

    try:
        # --- 1.1: ИСПРАВЛЕНИЕ RACE CONDITION ---
        user = get_or_create_user(db.session, persistent_id, validated_data.userInfo)
        if not user:
            # Это может случиться, если userInfo был None или произошла ошибка в get_or_create
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Failed to get or create user profile",
                    }
                ),
                400,
            )

            # НЕ ДЕЛАЕМ FLUSH ЗДЕСЬ

        # --- "Цифровой отпечаток" (Fingerprint) ---
        fingerprint = Fingerprint.query.filter_by(fingerprint_hash=fp_hash).first()
        if not fingerprint:
            fp_data = validated_data.fingerprint.get("privacySafe", {})
            fingerprint = Fingerprint(
                fingerprint_hash=fp_hash,
                user_agent=fp_data.get("userAgent"),
                platform=fp_data.get("platform"),
                webgl_renderer=fp_data.get("webGLRenderer"),
            )
            db.session.add(fingerprint)
        else:
            fingerprint.last_seen = datetime.now(UTC)

        # --- Метаданные результата (ResultMetadata) - ОБНОВЛЕНИЕ ---
        result = db.session.get(ResultMetadata, session_id)
        if not result:
            current_app.logger.error(
                f"Attempted to save results for non-existent session: {session_id}"
            )
            return (
                jsonify(
                    {"status": "error", "message": f"Session {session_id} not found"}
                ),
                404,
            )

            # Разбор данных из Pydantic-объекта
            start_time_dt = validated_data.sessionMetrics.startTime
            end_time_dt = validated_data.sessionMetrics.endTime
            score = validated_data.testResults.percentage  # Используется ниже
            start_time_from_dict = (
                validated_data.dict().get("sessionMetrics", {}).get("startTime")
            )
            end_time_from_dict = (
                validated_data.dict().get("sessionMetrics", {}).get("endTime")
            )

            # Безопасная обработка дат
            if not isinstance(start_time_dt, datetime) and start_time_from_dict:
                current_app.logger.warning(
                    f"Pydantic might have failed to parse startTime: '{start_time_from_dict}' for session {session_id}. Using raw value if available."
                )
                # Если start_time_dt не datetime, можно попытаться распарсить start_time_from_dict здесь,
                # но Pydantic должен был выдать ошибку валидации раньше, если формат неверный.
            if not isinstance(end_time_dt, datetime) and end_time_from_dict:
                current_app.logger.warning(
                    f"Pydantic might have failed to parse endTime: '{end_time_from_dict}' for session {session_id}. Using raw value if available."
                )

        # Обновляем поля существующей записи
        result.user_id = user.id
        result.fingerprint_hash = fp_hash
        result.test_type = validated_data.test_type
        result.score = validated_data.testResults.percentage
        result.start_time = validated_data.sessionMetrics.startTime
        result.end_time = validated_data.sessionMetrics.endTime
        result.raw_data = validated_data.model_dump(mode="json", by_alias=True)
        result.client_ip = request.remote_addr

        # --- Номер документа и Сертификат ---
        document_number = None
        passed = result.score >= current_app.config.get("PASSING_SCORE_THRESHOLD", 80)
        if passed:
            document_number = generate_document_number(db.session)
            result.document_number = document_number

            certificate = Certificate(
                document_number=document_number,
                user_fullname=user.full_name,
                user_position=user.position,
                test_type=validated_data.test_type,
                score_percentage=result.score,
                session_id=session_id,
            )
            db.session.add(certificate)

        # --- Фиксация транзакции ---
        db.session.commit()

        # --- Действия после успешного сохранения ---
        cache.delete_memoized(get_results_api)
        cache.delete_memoized(get_certificates)
        socketio.emit("update_needed", {"type": "new_result", "session_id": session_id})

        result_status = "passed" if passed else "failed"
        TESTS_COMPLETED_TOTAL.labels(
            test_type=validated_data.test_type, result=result_status
        ).inc()
        if document_number:
            DOCUMENTS_GENERATED_TOTAL.labels(test_type=validated_data.test_type).inc()

        current_app.logger.info(
            f"Results saved: session={session_id}, score={result.score}%, doc={document_number}"
        )

        response = {
            "status": "success",
            "message": "Results saved successfully",
            "session_id": session_id,
        }
        if document_number:
            response["officialDocumentNumber"] = document_number

        return jsonify(response), 201

    except IntegrityError as e:
        db.session.rollback()
        current_app.logger.error(
            f"DB integrity error in save_results: {e}", exc_info=True
        )
        return jsonify({"status": "error", "message": "Data conflict"}), 409
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"Unexpected error in save_results: {e}", exc_info=True
        )
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@api_bp.route("/log_event", methods=["POST"])
@limiter.limit("60 per minute")
# @csrf.exempt # <-- 1.5: УДАЛЕНО. CSRF-защита включена
@api_key_required
def log_event():

    # --- 1.4: ИСПРАВЛЕНИЕ DoS ---
    if request.content_length > MAX_LOG_EVENT_SIZE:
        return jsonify({"status": "error", "message": "Request is too large"}), 413

    # Вызываем get_json() БЕЗ аргумента max_content_length
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data"}), 400

    is_valid, error_msg = validate_json_data(data, ["sessionId", "eventType"])
    if not is_valid:
        return jsonify({"status": "error", "message": error_msg}), 400

    session_id = data["sessionId"]
    event_type = data["eventType"]
    if not validate_session_id(session_id):
        return jsonify({"status": "error", "message": "Invalid session ID"}), 400
    if not validate_event_type(event_type):
        return jsonify({"status": "error", "message": "Invalid event type"}), 400

    try:
        # --- ЛОГИКА ПРОВЕРКИ И СОЗДАНИЯ РОДИТЕЛЬСКОЙ ЗАПИСИ ---
        result_metadata = db.session.get(ResultMetadata, session_id)
        if not result_metadata:
            # Если родительская запись не существует, создаем "черновик"
            result_metadata = ResultMetadata(session_id=session_id, test_type="pending")
            db.session.add(result_metadata)
        # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

        # Логика создания самого события (proctoring_event)
        details = data.get("details", {})
        details["ip"] = request.remote_addr

        event = ProctoringEvent(
            session_id=session_id,
            event_type=event_type,
            event_timestamp=datetime.fromisoformat(
                data.get("eventTimestamp", datetime.now(UTC).isoformat()).replace(
                    "Z", ""
                )
            ),
            details=details,
            persistent_id=details.get("persistentId"),
            client_ip=request.remote_addr,
            page=details.get("page"),
        )
        db.session.add(event)

        # Сохраняем и событие, и, возможно, новую запись о сессии в одной транзакции
        db.session.commit()

        return jsonify({"status": "success"}), 200

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in log_event: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal error"}), 500


@api_bp.route("/get_behavior_analysis", methods=["GET"])
@admin_required
@cache.memoize(timeout=120)
def get_behavior_analysis():
    # ... (Весь код этой функции остается как в предыдущем ответе)
    try:
        # Configuration
        config = current_app.config
        thresholds = config.get(
            "BEHAVIOR_THRESHOLDS",
            {"min_score": 90, "max_test_duration_sec": 180, "min_engagement_score": 15},
        )

        # --- 👇 ИЗМЕНЕНИЕ: ЛОГИКА SUBQUERY 👇 ---

        # Шаг 1: CTE для извлечения 'чистых' данных из JSON
        # Это делает последующую агрегацию чище и быстрее
        engagement_events = (
            db.session.query(
                ProctoringEvent.persistent_id,
                ProctoringEvent.client_ip,
                ProctoringEvent.event_timestamp,
                # Извлекаем 'duration'
                case(
                    (
                        ProctoringEvent.event_type == "module_view_time",
                        func.cast(
                            ProctoringEvent.details["duration"].as_string(),
                            db.Integer,
                        ),
                    ),
                    else_=0,
                ).label("view_time"),
                # Извлекаем 'scroll_depth'
                case(
                    (
                        ProctoringEvent.event_type == "scroll_depth_milestone",
                        func.cast(
                            func.replace(
                                ProctoringEvent.details["depth"].as_string(),
                                "%",
                                "",
                            ),
                            db.Integer,
                        ),
                    ),
                    else_=0,
                ).label("scroll_depth"),
                # Извлекаем 'self_check'
                case(
                    (ProctoringEvent.event_type == "self_check_answered", 1),
                    else_=0,
                ).label("self_check"),
            )
            .filter(
                ProctoringEvent.event_type.in_(
                    [
                        "study_started",  # study_started нужен для определения времени
                        "module_view_time",
                        "scroll_depth_milestone",
                        "self_check_answered",
                    ]
                )
            )
            .cte("engagement_events")
        )

        # Шаг 2: Агрегируем ВСЕ сессии обучения для каждого пользователя
        # (вместо группировки по session_id)
        study_subquery = (
            db.session.query(
                engagement_events.c.persistent_id,
                engagement_events.c.client_ip,
                # Агрегируем все сессии обучения пользователя
                func.sum(engagement_events.c.view_time).label("total_view_time"),
                func.max(engagement_events.c.scroll_depth).label("max_scroll_depth"),
                func.sum(engagement_events.c.self_check).label("self_check_count"),
                # Нам также нужно самое раннее время обучения для join
                func.min(engagement_events.c.event_timestamp).label("first_study_time"),
            )
            .group_by(
                engagement_events.c.persistent_id,
                engagement_events.c.client_ip,
            )
            .subquery()
        )
        # --- 👆 КОНЕЦ ИЗМЕНЕНИЯ SUBQUERY 👆 ---

        # Основной запрос (теперь соединяет 1 тест с 1 агрегированной строкой обучения)
        suspicious_query = (
            db.session.query(
                ResultMetadata.session_id,
                ResultMetadata.score,
                ResultMetadata.test_type,
                ResultMetadata.client_ip,
                User.lastname,
                User.firstname,
                User.middlename,
                User.position,
                func.extract(
                    "epoch", ResultMetadata.end_time - ResultMetadata.start_time
                ).label("test_duration"),
                # Эти поля теперь представляют СУММУ всех сессий обучения
                study_subquery.c.total_view_time,
                study_subquery.c.max_scroll_depth,
                study_subquery.c.self_check_count,
            )
            .join(User, ResultMetadata.user_id == User.id)
            .outerjoin(  # Используем outerjoin, на случай если обучения не было
                study_subquery,
                and_(
                    or_(
                        and_(  # Явное соединение по ID, только если они не NULL
                            User.persistent_id.isnot(None),
                            study_subquery.c.persistent_id.isnot(None),
                            User.persistent_id == study_subquery.c.persistent_id,
                        ),
                        and_(  # Явное соединение по IP, только если они не NULL
                            ResultMetadata.client_ip.isnot(None),
                            study_subquery.c.client_ip.isnot(None),
                            ResultMetadata.client_ip == study_subquery.c.client_ip,
                        ),
                    ),
                    # Убеждаемся, что обучение было до теста
                    study_subquery.c.first_study_time < ResultMetadata.start_time,
                    # (Опционально) Ограничиваем поиск обучения (напр. за 24 часа до теста)
                    study_subquery.c.first_study_time
                    > ResultMetadata.start_time - timedelta(hours=24),
                ),
            )
            .filter(
                # Фильтруем тесты с высоким баллом и быстрым прохождением
                ResultMetadata.score >= thresholds["min_score"],
                func.extract(
                    "epoch", ResultMetadata.end_time - ResultMetadata.start_time
                )
                < thresholds["max_test_duration_sec"],
            )
            # --- 👇 НОВОЕ: Группируем результат, чтобы убрать дубликаты от JOIN 👇 ---
            .group_by(
                ResultMetadata.session_id,
                User.id,  # Группируем по ID пользователя и сессии
                study_subquery.c.total_view_time,
                study_subquery.c.max_scroll_depth,
                study_subquery.c.self_check_count,
            )
            # --- 👆 ---
        )

        # Execute query and process results
        suspicious_sessions = []
        for row in suspicious_query.all():
            # Рассчитываем engagement_score (теперь на основе ОБЩЕЙ суммы обучения)
            engagement_score = 0
            if row.total_view_time:
                engagement_score += int(
                    row.total_view_time / 60
                )  # 1 очко за минуту просмотра
            if row.max_scroll_depth:
                if row.max_scroll_depth >= 95:
                    engagement_score += 10
                elif row.max_scroll_depth >= 50:
                    engagement_score += 5
            if row.self_check_count:
                engagement_score += row.self_check_count * 2  # 2 очка за самопроверку

            # Check if suspicious (общий engagement_score < порога)
            if engagement_score < thresholds["min_engagement_score"]:
                suspicious_sessions.append(
                    {
                        "sessionId": row.session_id,
                        "userInfo": {
                            "lastName": row.lastname,
                            "firstName": row.firstname,
                            "middleName": row.middlename,
                            "position": row.position,
                        },
                        "testResult": {
                            "score": row.score,
                            "duration": (
                                int(row.test_duration) if row.test_duration else 0
                            ),
                        },
                        "studyInfo": {
                            "totalStudyTimeSec": (
                                int(row.total_view_time) if row.total_view_time else 0
                            ),
                            "engagementScore": engagement_score,
                        },
                        "reason": f"High score ({row.score}%) with fast completion ({int(row.test_duration)}s) and low TOTAL study engagement (Score: {engagement_score})",
                    }
                )

        current_app.logger.info(
            f"Behavior analysis found {len(suspicious_sessions)} suspicious sessions"
        )
        return jsonify(suspicious_sessions), 200

    except Exception as e:
        current_app.logger.error(f"Error in behavior analysis: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Analysis failed"}), 500


from app.utils.analytics import find_result_file_by_session_id


@api_bp.route("/get_full_result/<session_id>", methods=["GET"])
@admin_required
def get_full_result_data(session_id: str):
    """
    Находит и возвращает полные JSON-данные из БД для детального анализа.
    """
    # Эта функция теперь сама делает валидацию и ищет данные в БД
    result_data = find_result_file_by_session_id(session_id)

    if result_data:
        # Если данные найдены, просто возвращаем их
        return jsonify(result_data), 200
    else:
        # Если функция вернула None (ID невалиден или не найден), отдаем 404
        return jsonify({"status": "error", "message": "Result not found"}), 404


from app.utils.analytics import (
    compare_mouse_trajectories,
    find_result_file_by_session_id,
)


@api_bp.route("/analyze_mouse", methods=["POST"])
@admin_required  # <--- ДОБАВЬТЕ ЭТОТ ДЕКОРАТОР
@limiter.limit("5 per minute")
def analyze_mouse():
    """
    Анализирует схожесть траекторий мыши, получая данные напрямую из БД,
    и измеряет длительность каждой операции сравнения.
    """
    try:
        data = request.get_json()
        session_ids = data.get("session_ids")

        if not session_ids or len(session_ids) < 2:
            return jsonify({"error": "Нужно минимум 2 сессии для сравнения"}), 400

        # Извлекаем траектории напрямую из данных, полученных из БД
        trajectories = {}
        for sid in session_ids:
            result_data = find_result_file_by_session_id(sid)
            if not result_data:
                current_app.logger.warning(f"Данные для сессии {sid} не найдены в БД")
                continue

            per_question = result_data.get("behavioralMetrics", {}).get(
                "perQuestion", []
            )
            trajectories[sid] = {}
            for i, q_data in enumerate(per_question):
                movements = q_data.get("mouseMovements")
                if movements:
                    trajectories[sid][i] = movements

        # Логика сравнения траекторий
        results = {}
        sid_list = list(session_ids)

        for i in range(len(sid_list)):
            for j in range(i + 1, len(sid_list)):
                s1, s2 = sid_list[i], sid_list[j]
                pair_key = f"{s1}_vs_{s2}"
                results[pair_key] = {}

                common_qs = set(trajectories.get(s1, {}).keys()) & set(
                    trajectories.get(s2, {}).keys()
                )

                for q_idx in common_qs:
                    t1 = trajectories[s1][q_idx]
                    t2 = trajectories[s2][q_idx]

                    # <--- НАЧАЛО ИЗМЕНЕНИЯ: ИЗМЕРЕНИЕ ДЛИТЕЛЬНОСТИ --->
                    # Запускаем таймер перед вызовом ресурсоемкой функции
                    with ANALYSIS_DURATION_SECONDS.time():
                        similarity = compare_mouse_trajectories(t1, t2)
                    # После завершения блока `with` длительность будет
                    # автоматически записана в метрику Prometheus.
                    # <--- КОНЕЦ ИЗМЕНЕНИЯ --->

                    results[pair_key][q_idx] = round(similarity, 1)

        return jsonify(results), 200

    except Exception as e:
        current_app.logger.error(f"Ошибка в анализе движений мыши: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@api_bp.route("/get_results", methods=["GET"])
@admin_required
@cache.cached(timeout=60, query_string=True)
def get_results_api():
    """
    Возвращает результаты с пагинацией из БД.
    Фильтрует по статусу И по пресетам (сегодня, неделя, аномалии).
    """
    try:
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)

        # --- 👇 НОВЫЙ КОД: Получаем статус из запроса 👇 ---
        status = request.args.get(
            "status", "", type=str
        )  # По умолчанию "" (Все статусы)
        # --- 👆 КОНЕЦ НОВОГО КОДА 👆 ---
        preset = request.args.get("preset", "all", type=str)

        if preset not in ALLOWED_PRESETS:
            return jsonify({"status": "error", "message": "Invalid preset value"}), 400
        max_per_page = current_app.config.get("MAX_RESULTS_PER_PAGE", 1000)

        if not (1 <= page <= 1000 and 1 <= per_page <= max_per_page):
            return (
                jsonify(
                    {"status": "error", "message": "Invalid pagination parameters"}
                ),
                400,
            )

        # Основа запроса: получаем метаданные и сразу подгружаем связанного пользователя,
        base_query = ResultMetadata.query.options(db.joinedload(ResultMetadata.user))
        # --- 👇 ДОБАВЛЕНЫ ФИЛЬТРЫ ДЛЯ ЗАВЕРШЕННЫХ ТЕСТОВ 👇 ---
        # --- 👇 ИЗМЕНЕНИЕ: Динамические фильтры вместо "жестких" 👇 ---
        if status == "completed":
            # "Завершен" - есть оценка, время окончания и пользователь
            base_query = base_query.filter(
                ResultMetadata.score.isnot(None),
                ResultMetadata.end_time.isnot(None),
                ResultMetadata.user_id.isnot(None),
            )
        elif status == "in_progress" or status == "abandoned":
            # "В процессе" или "Прерван" - это записи, созданные log_event,
            # но еще не обработанные save_results.
            # У них нет user_id или end_time.
            base_query = base_query.filter(
                ResultMetadata.user_id.is_(None),
                ResultMetadata.end_time.is_(None),
                # 'pending' также попадет сюда, т.к. у него user_id is None
            )

        # --- 👇 НОВЫЙ КОД: Фильтры по ПРЕСЕТАМ (даты и аномалии) 👇 ---
        now = datetime.now(UTC)

        if preset == "today":
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            base_query = base_query.filter(ResultMetadata.start_time >= today_start)

        elif preset == "week":
            week_start = (now - timedelta(days=now.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            base_query = base_query.filter(ResultMetadata.start_time >= week_start)

        elif preset == "anomalies":
            # Загружаем пороги из конфига
            focus_thresh = current_app.config.get("FOCUS_THRESHOLD", 5)
            blur_thresh = current_app.config.get("BLUR_THRESHOLD", 60)
            print_thresh = current_app.config.get("PRINT_THRESHOLD", 0)

            # Фильтр по JSON-полю raw_data. Это специфично для PostgreSQL (JSONB)
            # Мы ищем, где ('sessionMetrics' -> 'totalFocusLoss')::int > focus_thresh
            base_query = base_query.filter(
                ResultMetadata.raw_data.isnot(None),
                or_(
                    ResultMetadata.raw_data.op("->")("sessionMetrics")
                    .op("->>")("totalFocusLoss")
                    .cast(db.Integer)
                    > focus_thresh,
                    ResultMetadata.raw_data.op("->")("sessionMetrics")
                    .op("->>")("totalBlurTime")
                    .cast(db.Float)
                    > blur_thresh,
                    ResultMetadata.raw_data.op("->")("sessionMetrics")
                    .op("->>")("printAttempts")
                    .cast(db.Integer)
                    > print_thresh,
                ),
            )
        # --- 👆 КОНЕЦ НОВОГО КОДА 👆 ---

        # Сортировка применяется всегда
        base_query = base_query.order_by(ResultMetadata.start_time.desc())

        # Используем встроенную пагинацию SQLAlchemy - это проще и надежнее
        pagination = base_query.paginate(page=page, per_page=per_page, error_out=False)
        results_from_db = pagination.items
        total = pagination.total

        results = []
        for row in results_from_db:
            # score теперь точно не None из-за фильтра .isnot(None)
            score = row.score if row.score is not None else 0

            # ... (логика grade_class, grade_text) ...
            if score >= 90:
                grade_class, grade_text = "excellent", "Отлично"
            elif score >= 80:
                grade_class, grade_text = "good", "Хорошо"
            elif score >= 70:
                grade_class, grade_text = "satisfactory", "Удовлетворительно"
            elif score >= 60:
                grade_class, grade_text = "unsatisfactory", "Неудовлетворительно"
            else:
                grade_class, grade_text = "poor", "Плохо"

            # --- 👇 ИЗМЕНЕНИЕ: Извлекаем данные об аномалиях из raw_data 👇 ---
            sm_raw = (row.raw_data or {}).get("sessionMetrics", {})
            session_metrics = {
                "startTime": row.start_time.isoformat() if row.start_time else None,
                "endTime": row.end_time.isoformat() if row.end_time else None,
                # Добавляем данные для фильтра аномалий
                "totalFocusLoss": sm_raw.get("totalFocusLoss", 0),
                "totalBlurTime": sm_raw.get("totalBlurTime", 0),
                "printAttempts": sm_raw.get("printAttempts", 0),
            }
            # --- 👆 КОНЕЦ ИЗМЕНЕНИЯ 👆 ---

            results.append(
                {
                    "sessionId": row.session_id,
                    "testType": row.test_type,
                    "clientIp": row.client_ip,
                    "userInfo": {
                        # Эта проверка снова критична
                        "lastName": row.user.lastname if row.user else None,
                        "firstName": row.user.firstname if row.user else None,
                    },
                    "testResults": {
                        "percentage": score,
                        "grade": {"class": grade_class, "text": grade_text},
                    },
                    "sessionMetrics": session_metrics,
                }
            )

        return jsonify(
            {"results": results, "page": page, "per_page": per_page, "total": total}
        )

    except Exception as e:
        current_app.logger.error(f"Error in get_results_api: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Error retrieving results"}), 500


@api_bp.route("/get_certificates", methods=["GET"])
@admin_required
@cache.memoize(timeout=360)
def get_certificates():
    """
    Возвращает реестр всех выданных сертификатов из БД.
    4.4: ИСПРАВЛЕНО: Добавлена пагинация.
    """
    try:
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)

        pagination = Certificate.query.order_by(Certificate.issue_date.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

        certificates = [cert.to_dict() for cert in pagination.items]

        return (
            jsonify(
                {
                    "certificates": certificates,
                    "total": pagination.total,
                    "page": page,
                    "per_page": per_page,
                    "has_next": pagination.has_next,
                    "has_prev": pagination.has_prev,
                }
            ),
            200,
        )

    except Exception as e:
        current_app.logger.error(f"Error in get_certificates: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@api_bp.route("/get_events/<session_id>", methods=["GET"])
@admin_required
def get_events(session_id: str):
    """Возвращает все события прокторинга для указанной сессии."""
    try:
        if not validate_session_id(session_id):
            return jsonify({"status": "error", "message": "Invalid session ID"}), 400

        events_from_db = (
            ProctoringEvent.query.filter_by(session_id=session_id)
            .order_by(ProctoringEvent.event_timestamp.asc())
            .all()
        )
        events = [event.to_dict() for event in events_from_db]

        return jsonify(events), 200
    except Exception as e:
        current_app.logger.error(
            f"Error in get_events for session {session_id}: {e}", exc_info=True
        )
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@api_bp.route("/get_abandoned_sessions", methods=["GET"])
@admin_required
@cache.memoize(timeout=60)
def get_abandoned_sessions():
    """
    Находит прерванные сессии.
    Использует fallback на userInfo из JSON-деталей.
    """
    try:
        passing_score = current_app.config.get("PASSING_SCORE_THRESHOLD", 80)
        # CTE для получения всех завершенных сессий
        successful_sessions = (
            db.session.query(ResultMetadata.session_id)
            .filter(
                ResultMetadata.session_id.isnot(None),
                ResultMetadata.score
                >= passing_score,  # <-- Используем переменную passing_score
            )
            .cte("successful_sessions")
        )

        first_event = (
            db.session.query(
                ProctoringEvent.session_id,
                ProctoringEvent.event_type,
                ProctoringEvent.event_timestamp,
                ProctoringEvent.details,
                ProctoringEvent.persistent_id,
                func.row_number()
                .over(
                    partition_by=ProctoringEvent.session_id,
                    order_by=ProctoringEvent.event_timestamp.asc(),
                )
                .label("rn"),
            )
            .filter(
                ProctoringEvent.event_type.in_(
                    ["test_started", "study_started"]
                ),  # Добавлена запятая
                ProctoringEvent.details.isnot(None),
            )
            .cte("first_event")
        )

        # Основной запрос для брошенных сессий
        abandoned_query = (
            db.session.query(
                ProctoringEvent.session_id,
                func.max(first_event.c.event_type).label("session_type"),
                func.min(ProctoringEvent.event_timestamp).label("start_time"),
                func.max(cast(first_event.c.details, Text)).label(
                    "first_event_details_text"
                ),
                func.max(first_event.c.persistent_id).label("persistent_id"),
                # ... (остальные агрегатные функции) ...
                func.count(case((ProctoringEvent.event_type == "focus_loss", 1))).label(
                    "focus_loss_count"
                ),
                func.count(
                    case((ProctoringEvent.event_type == "screenshot_attempt", 1))
                ).label("screenshot_count"),
                func.count(
                    case((ProctoringEvent.event_type == "print_attempt", 1))
                ).label("print_count"),
            )
            .select_from(ProctoringEvent)
            .outerjoin(
                first_event,
                and_(
                    ProctoringEvent.session_id == first_event.c.session_id,
                    first_event.c.rn == 1,
                ),
            )
            .filter(
                ~ProctoringEvent.session_id.in_(
                    db.session.query(successful_sessions.c.session_id)
                )
            )
            .group_by(ProctoringEvent.session_id)
            .order_by(func.min(ProctoringEvent.event_timestamp).desc())
        )

        # Выполняем запрос
        abandoned_sessions = abandoned_query.all()

        if not abandoned_sessions:
            current_app.logger.info("No abandoned/unsuccessful sessions found.")
            return jsonify([]), 200

        # Собираем все persistent_id для batch-запроса пользователей
        persistent_ids = {
            s.persistent_id for s in abandoned_sessions if s.persistent_id
        }

        # Получаем информацию о пользователях одним запросом
        users_map = {}
        if persistent_ids:
            users = User.query.filter(User.persistent_id.in_(persistent_ids)).all()
            users_map = {user.persistent_id: user for user in users}

        # Маппинг типов сессий
        session_type_map = {"test_started": "test", "study_started": "study"}

        results = []
        for session in abandoned_sessions:
            first_event_details = {}  # Инициализируем как пустой dict
            client_ip = "N/A"
            user_info_from_event = {}  # Инициализируем как пустой dict
            if session.first_event_details_text:
                try:
                    first_event_details = json.loads(session.first_event_details_text)
                    if isinstance(first_event_details, dict):
                        client_ip = first_event_details.get("ip", "N/A")
                        user_info_from_event = first_event_details.get("userInfo", {})
                    else:
                        current_app.logger.warning(
                            f"Parsed details is not a dict for session {session.session_id}"
                        )
                        first_event_details = {}  # Сбрасываем до пустого словаря
                        user_info_from_event = {}
                except (json.JSONDecodeError, TypeError) as e:
                    current_app.logger.warning(
                        f"Could not parse details text for session {session.session_id}: {e}"
                    )
                    first_event_details = {}  # Сбрасываем до пустого словаря
                    user_info_from_event = {}
            # --- ЛОГИКА ОПРЕДЕЛЕНИЯ USER INFO ---
            user = users_map.get(session.persistent_id)

            if user:
                # 1. Лучший случай: Пользователь найден в таблице users
                user_info = {"lastName": user.lastname, "firstName": user.firstname}
            elif user_info_from_event and user_info_from_event.get("lastName"):
                # 2. Пользователя нет, но есть userInfo в деталях события
                user_info = {
                    "lastName": user_info_from_event.get("lastName", "N/A"),
                    "firstName": user_info_from_event.get("firstName", "N/A"),
                    # Можно добавить отчество и должность, если нужно
                    # 'middleName': user_info_from_event.get('middleName'),
                    "source": "event_log",
                }
                # Добавляем пометку, что данные из лога
            elif session.persistent_id:
                # 3. Пользователя нет, userInfo в событии нет, но есть persistent_id
                user_info = {
                    "lastName": "N/A",
                    "firstName": f"ID: {str(session.persistent_id)[:8]}...",
                }
            else:
                # 4. Совсем ничего нет
                user_info = {"lastName": "N/A", "firstName": "N/A"}
            # --- КОНЕЦ ЛОГИКИ ОПРЕДЕЛЕНИЯ USER INFO ---

            results.append(
                {
                    "sessionId": session.session_id,
                    "sessionType": session_type_map.get(
                        session.session_type, "unknown"
                    ),
                    "startTime": (
                        session.start_time.isoformat() if session.start_time else "N/A"
                    ),
                    "userInfo": user_info,  # Используем собранный userInfo
                    "clientIp": client_ip,
                    "violationCounts": {
                        "focusLoss": session.focus_loss_count or 0,
                        "screenshots": session.screenshot_count or 0,
                        "prints": session.print_count or 0,
                    },
                }
            )

        current_app.logger.info(
            f"Returning {len(results)} abandoned/unsuccessful sessions (safe JSON parsing)"
        )
        return jsonify(results), 200

    except (
        DBAPIError
    ) as e:  # Ловим ошибки базы данных (включая ошибки SQL синтаксиса/типов)
        db.session.rollback()
        current_app.logger.error(
            f"Database error in get_abandoned_sessions: {e}", exc_info=True
        )
        return (
            jsonify({"status": "error", "message": "Database error during analysis"}),
            500,
        )
    except Exception as e:
        db.session.rollback()  # Откат на всякий случай
        current_app.logger.error(
            f"Unexpected error in get_abandoned_sessions: {e}", exc_info=True
        )
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@api_bp.route("/global_search", methods=["GET"])
@admin_required
@cache.cached(timeout=60, query_string=True)
def global_search():
    """Выполняет глобальный поиск по пользователям и сессиям."""
    query = request.args.get("q", "")

    if not query or len(query) < 3:
        return jsonify(
            {"users": [], "sessions": []}
        )  # Возвращаем пустой результат, если запрос короткий

    search_term = f"%{query}%"

    results = {"users": [], "sessions": []}

    # 1. Поиск по Пользователям
    users = (
        User.query.filter(
            or_(
                User.lastname.ilike(search_term),
                User.firstname.ilike(search_term),
                User.middlename.ilike(search_term),
            )
        )
        .limit(5)
        .all()
    )

    for user in users:
        results["users"].append(
            {"id": user.id, "name": user.full_name, "position": user.position}
        )

    # 2. Поиск по Сессиям
    sessions = (
        ResultMetadata.query.filter(
            or_(
                ResultMetadata.session_id.ilike(search_term),
                ResultMetadata.client_ip.ilike(search_term),
                ResultMetadata.document_number.ilike(search_term),
            )
        )
        .limit(5)
        .all()
    )

    for session in sessions:
        results["sessions"].append(
            {
                "id": session.session_id,
                "type": session.test_type,
                "date": session.start_time.isoformat() if session.start_time else "N/A",
            }
        )

    return jsonify(results)


def get_stats_for_period(start_date, end_date, settings):
    """Вспомогательная функция для расчета статистики за период."""

    # Базовый запрос для завершенных тестов в указанном периоде
    query = ResultMetadata.query.filter(
        ResultMetadata.end_time.isnot(None),
        ResultMetadata.score.isnot(None),
        ResultMetadata.user_id.isnot(None),
        ResultMetadata.end_time >= start_date,
        ResultMetadata.end_time < end_date,
    )

    # 1. Всего тестов
    total_tests = query.count()

    # 2. Средний балл
    avg_score_result = query.with_entities(func.avg(ResultMetadata.score)).scalar()
    avg_score = round(float(avg_score_result), 1) if avg_score_result else 0

    # 3. Аномалии
    # Для подсчета аномалий нам нужно получить все результаты
    results = query.all()
    anomalies_count = 0
    if results:
        # Эта логика должна быть синхронизирована с вашим frontend/settings
        # Используем .get('totalFocusLoss', 0) > X, так как raw_data может не иметь этих полей
        focus_threshold = settings.get("focusThreshold", 5)
        blur_threshold = settings.get("blurThreshold", 60)
        print_threshold = settings.get("printThreshold", 0)

        for r in results:
            sm = (r.raw_data or {}).get("sessionMetrics", {})
            focus_loss = sm.get("totalFocusLoss", 0)
            blur_time = sm.get("totalBlurTime", 0)
            print_attempts = sm.get("printAttempts", 0)

            if (
                focus_loss > focus_threshold
                or blur_time > blur_threshold
                or print_attempts > print_threshold
            ):
                anomalies_count += 1

    # 4. Уникальные пользователи
    unique_users = query.with_entities(
        func.count(distinct(ResultMetadata.user_id))
    ).scalar()

    return {
        "totalTests": total_tests,
        "avgScore": avg_score,
        "anomaliesCount": anomalies_count,
        "uniqueUsers": unique_users,
    }


def calculate_change(current, previous):
    """Вспомогательная функция для расчета % изменения."""
    if previous == 0:
        return 100.0 if current > 0 else 0.0  # Рост на 100%, если было 0, а стало > 0
    change = ((current - previous) / previous) * 100
    return round(change, 1)


@api_bp.route("/get_dashboard_stats", methods=["GET"])
@admin_required
@cache.memoize(timeout=3600)  # Кэшируем на 1 час
def get_dashboard_stats():
    """Рассчитывает статистику для виджетов дашборда."""
    try:
        # Загружаем настройки, чтобы знать пороги аномалий
        # Временное решение, т.к. у нас нет доступа к 'settings' из state.js
        # Лучше передавать настройки из current_app.config
        settings = {
            "focusThreshold": current_app.config.get("FOCUS_THRESHOLD", 5),
            "blurThreshold": current_app.config.get("BLUR_THRESHOLD", 60),
            "printThreshold": current_app.config.get("PRINT_THRESHOLD", 0),
        }

        # Определяем периоды
        now = datetime.now(UTC)
        current_period_start = now - timedelta(days=7)
        previous_period_start = now - timedelta(days=14)

        # Получаем статистику
        current_stats = get_stats_for_period(current_period_start, now, settings)
        previous_stats = get_stats_for_period(
            previous_period_start, current_period_start, settings
        )

        # Рассчитываем изменения
        response = {
            "totalTests": {
                "value": current_stats["totalTests"],
                "change": calculate_change(
                    current_stats["totalTests"], previous_stats["totalTests"]
                ),
            },
            "avgScore": {
                "value": current_stats["avgScore"],
                "change": calculate_change(
                    current_stats["avgScore"], previous_stats["avgScore"]
                ),
            },
            "anomaliesCount": {
                "value": current_stats["anomaliesCount"],
                "change": calculate_change(
                    current_stats["anomaliesCount"], previous_stats["anomaliesCount"]
                ),
            },
            "uniqueUsers": {
                "value": current_stats["uniqueUsers"],
                "change": calculate_change(
                    current_stats["uniqueUsers"], previous_stats["uniqueUsers"]
                ),
            },
        }

        return jsonify(response), 200

    except Exception as e:
        current_app.logger.error(f"Error in get_dashboard_stats: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to calculate stats"}), 500
