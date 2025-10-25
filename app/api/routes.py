# app/api/routes.py

import json
from datetime import UTC, datetime, timedelta

from flask import current_app, jsonify, request
from pydantic import ValidationError
from sqlalchemy import and_, case, func, or_, distinct, cast, Text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import DBAPIError

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


@api_bp.route("/save_results", methods=["POST"])
@limiter.limit("10 per minute")
@api_key_required
@csrf.exempt
def save_results():
    """
    Атомарно обновляет существующую запись о результатах теста,
    создает связанные сущности, сохраняет все в БД и обновляет метрики.
    Устойчив к race condition при создании пользователя.
    """
    try:
        # Pydantic-валидация входящего JSON. Этот блок работает корректно.
        validated_data = SaveResultsRequest.model_validate(request.get_json())
    except ValidationError as e:
        current_app.logger.warning(
            f"Invalid data from {request.remote_addr}: {e.errors()}"
        )
        return jsonify({"status": "error", "message": "Invalid input data", "details": e.errors()}), 400

    # <--- ИЗМЕНЕНИЕ: Работаем напрямую с Pydantic-объектом, а не со словарем ---
    session_id = validated_data.sessionId
    persistent_id = validated_data.persistentId.get("cookie")
    fp_hash = validated_data.fingerprint.get("privacySafeHash")

    if not persistent_id or not fp_hash:
        return jsonify({"status": "error", "message": "Missing required identifiers"}), 400

    user = None # Инициализируем user
    attempt = 1
    max_attempts = 2 # Достаточно двух попыток для race condition

    while attempt <= max_attempts:
        try:
        # --- Пользователь (User) ---
            user = User.query.filter_by(persistent_id=persistent_id).with_for_update().first() # Блокировка строки на время транзакции
            if not user:
                user_info = validated_data.userInfo
                if not user_info or not user_info.lastName or not user_info.firstName:
                     current_app.logger.warning(f"Missing required userInfo fields for persistent_id {persistent_id}")
                     return jsonify({"status": "error", "message": "Missing required user information (lastName, firstName)"}), 400
                user = User(
                    lastname=user_info.lastName,
                    firstname=user_info.firstName,
                    middlename=user_info.middleName,
                    position=user_info.position,
                    persistent_id=persistent_id,
                )
                db.session.add(user)
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
            result = ResultMetadata.query.get(session_id)
            if not result:
                current_app.logger.error(
                    f"Attempted to save results for non-existent session: {session_id}"
                )
                return jsonify({"status": "error", "message": f"Session {session_id} not found"}), 404

        # Разбор данных из Pydantic-объекта
            start_time_dt = validated_data.sessionMetrics.startTime
            end_time_dt = validated_data.sessionMetrics.endTime
            score = validated_data.testResults.percentage # Используется ниже
            start_time_from_dict = validated_data.dict().get("sessionMetrics", {}).get("startTime")
            end_time_from_dict = validated_data.dict().get("sessionMetrics", {}).get("endTime")

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
            result.user_id = user.id # Связываем с найденным или только что созданным user
            result.fingerprint_hash = fp_hash
            result.test_type = validated_data.test_type
            result.score = score # Используем score
            result.start_time = start_time_dt # Используем datetime из Pydantic
            result.end_time = end_time_dt     # Используем datetime из Pydantic
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
                    user_fullname=user.full_name, # Используем user
                    user_position=user.position,  # Используем user
                # <--- ИЗМЕНЕНИЕ: Используем правильное поле test_type и здесь ---
                    test_type=validated_data.test_type,
                    score_percentage=result.score,
                    session_id=session_id,
                )
                db.session.add(certificate)

        # --- Фиксация транзакции ---
            db.session.commit() # Все изменения одним коммитом

        # --- Действия после успешного сохранения ---

        # 1. Очистка кэша
            cache.delete_memoized(get_results_api)
            cache.delete_memoized(get_certificates)

        # 2. Уведомление клиентов через WebSocket
            socketio.emit("update_needed", {"type": "new_result", "session_id": session_id})

        # <--- ИЗМЕНЕНИЕ: Инкремент метрик Prometheus ---
            result_status = "passed" if passed else "failed"
            TESTS_COMPLETED_TOTAL.labels(test_type=validated_data.test_type, result=result_status).inc()
            if document_number:
                DOCUMENTS_GENERATED_TOTAL.labels(test_type=validated_data.test_type).inc()
        # -----------------------------------------------

            current_app.logger.info(f"Results saved: session={session_id}, score={result.score}%, doc={document_number}")


            response = {"status": "success", "message": "Results saved successfully", "session_id": session_id}
            if document_number:
                response["officialDocumentNumber"] = document_number

            return jsonify(response), 201 # Успех, выходим из цикла и функции

        except IntegrityError as e:
            db.session.rollback()
            if "ix_users_persistent_id" in str(e.orig) and attempt < max_attempts:
                 current_app.logger.warning(f"Race condition detected for persistent_id {persistent_id} on attempt {attempt}. Retrying.")
                 attempt += 1 # Увеличиваем счетчик и пробуем снова
                 continue # Переходим к следующей итерации цикла
            else:
                 current_app.logger.error(f"DB integrity error (not race condition or retries exceeded): {e}", exc_info=True) # Добавлено exc_info=True
                 return jsonify({"status": "error", "message": "Data conflict"}), 409
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Unexpected error in save_results: {e}", exc_info=True)
            return jsonify({"status": "error", "message": "Internal server error"}), 500
    current_app.logger.error(f"Failed to save results for {session_id} after {max_attempts} attempts due to persistent race condition or other issue.")
    return jsonify({"status": "error", "message": "Failed to save results after multiple attempts"}), 500


@api_bp.route("/log_event", methods=["POST"])
@limiter.limit("60 per minute")
@csrf.exempt
@api_key_required
def log_event():
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


from datetime import datetime, timedelta

from sqlalchemy import and_, case, func, or_


@api_bp.route("/get_behavior_analysis", methods=["GET"])
@admin_required
@cache.memoize(timeout=120)
def get_behavior_analysis():
    """
    Optimized behavioral analysis using database aggregation.
    All heavy lifting done by PostgreSQL.
    """
    try:
        # Configuration
        config = current_app.config
        thresholds = config.get(
            "BEHAVIOR_THRESHOLDS",
            {"min_score": 90, "max_test_duration_sec": 180, "min_engagement_score": 15},
        )

        # Build optimized query using SQLAlchemy ORM
        # Subquery for study sessions with engagement metrics
        study_subquery = (
            db.session.query(
                ProctoringEvent.session_id.label("study_session_id"),
                ProctoringEvent.persistent_id,
                ProctoringEvent.client_ip,
                func.min(ProctoringEvent.event_timestamp).label("study_start"),
                func.max(ProctoringEvent.event_timestamp).label("study_end"),
                func.sum(
                    case(
                        (
                            ProctoringEvent.event_type == "module_view_time",
                            func.cast(
                                ProctoringEvent.details["duration"].as_string(),
                                db.Integer,
                            ),
                        ),
                        else_=0,
                    )
                ).label("total_view_time"),
                func.max(
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
                    )
                ).label("max_scroll_depth"),
                func.sum(
                    case(
                        (ProctoringEvent.event_type == "self_check_answered", 1),
                        else_=0,
                    )
                ).label("self_check_count"),
            )
            .filter(
                ProctoringEvent.event_type.in_(
                    [
                        "study_started",
                        "module_view_time",
                        "scroll_depth_milestone",
                        "self_check_answered",
                    ]
                )
            )
            .group_by(
                ProctoringEvent.session_id,
                ProctoringEvent.persistent_id,
                ProctoringEvent.client_ip,
            )
            .subquery()
        )

        # Main query joining test results with study metrics
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
                study_subquery.c.total_view_time,
                study_subquery.c.max_scroll_depth,
                study_subquery.c.self_check_count,
                func.extract(
                    "epoch", study_subquery.c.study_end - study_subquery.c.study_start
                ).label("study_duration"),
            )
            .join(User, ResultMetadata.user_id == User.id)
            .outerjoin(
                study_subquery,
                and_(
                    or_(
                        User.persistent_id == study_subquery.c.persistent_id,
                        ResultMetadata.client_ip == study_subquery.c.client_ip,
                    ),
                    study_subquery.c.study_start < ResultMetadata.start_time,
                    study_subquery.c.study_start
                    > ResultMetadata.start_time - timedelta(hours=24),
                ),
            )
            .filter(
                ResultMetadata.score >= thresholds["min_score"],
                func.extract(
                    "epoch", ResultMetadata.end_time - ResultMetadata.start_time
                )
                < thresholds["max_test_duration_sec"],
            )
        )

        # Execute query and process results
        suspicious_sessions = []
        for row in suspicious_query.all():
            # Calculate engagement score
            engagement_score = 0
            if row.total_view_time:
                engagement_score += int(row.total_view_time / 60)
            if row.max_scroll_depth:
                if row.max_scroll_depth >= 95:
                    engagement_score += 10
                elif row.max_scroll_depth >= 50:
                    engagement_score += 5
            if row.self_check_count:
                engagement_score += row.self_check_count * 2

            # Check if suspicious
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
                            "duration": (
                                int(row.study_duration) if row.study_duration else 0
                            ),
                            "engagementScore": engagement_score,
                        },
                        "reason": f"High score ({row.score}%) with fast completion "
                        f"({int(row.test_duration)}s) and low study engagement "
                        f"(Score: {engagement_score})",
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
    Возвращает ТОЛЬКО ЗАВЕРШЕННЫЕ результаты тестов с пагинацией из БД.
    Фильтрует записи 'pending' и те, у которых нет score или end_time.
    """
    try:
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        max_per_page = current_app.config.get("MAX_RESULTS_PER_PAGE", 1000)

        if not (1 <= page <= 1000 and 1 <= per_page <= max_per_page):
            return (
                jsonify(
                    {"status": "error", "message": "Invalid pagination parameters"}
                ),
                400,
            )

        # Основа запроса: получаем метаданные и сразу подгружаем связанного пользователя,
        base_query = ResultMetadata.query.options(
            db.joinedload(ResultMetadata.user)
        )
        # --- 👇 ДОБАВЛЕНЫ ФИЛЬТРЫ ДЛЯ ЗАВЕРШЕННЫХ ТЕСТОВ 👇 ---
        base_query = base_query.filter(
            ResultMetadata.score.isnot(None),    # Убеждаемся, что балл сохранен
            ResultMetadata.end_time.isnot(None), # Убеждаемся, что тест завершен
            ResultMetadata.user_id.isnot(None)   # Убеждаемся, что пользователь привязан
        ).order_by(ResultMetadata.start_time.desc())

        # Используем встроенную пагинацию SQLAlchemy - это проще и надежнее
        pagination = base_query.paginate(page=page, per_page=per_page, error_out=False)
        results_from_db = pagination.items
        total = pagination.total

        results = []
        for row in results_from_db:
            # score теперь точно не None из-за фильтра .isnot(None)
            score = row.score
            if score >= 90:
                grade_class, grade_text = "excellent", "Отлично"
            elif score >= 80:
                grade_class, grade_text = "good", "Хорошо"
            # Используем >= 70 для Удовлетворительно, как в 117-test.html
            elif score >= 70:
                grade_class, grade_text = "satisfactory", "Удовлетворительно"
            # Используем >= 60 для Неудовлетворительно, как в 117-test.html
            elif score >= 60:
                 grade_class, grade_text = "unsatisfactory", "Неудовлетворительно"
            else:
                grade_class, grade_text = "poor", "Плохо" # Для < 60

            results.append(
                {
                    "sessionId": row.session_id,
                    "testType": row.test_type,
                    "clientIp": row.client_ip,
                    "userInfo": {
                        # Проверяем row.user на случай редких ошибок связи, хотя joinload должен помочь
                        "lastName": row.user.lastname if row.user else "N/A",
                        "firstName": row.user.firstname if row.user else "N/A",
                    },
                    "testResults": {
                        "percentage": score,
                        "grade": {"class": grade_class, "text": grade_text},
                    },
                    "sessionMetrics": {
                        # start_time и end_time теперь точно не None из-за фильтров
                        "startTime": row.start_time.isoformat(),
                        "endTime": row.end_time.isoformat(),
                    },
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
    """Возвращает реестр всех выданных сертификатов из БД."""
    try:
        # Запрос к БД через SQLAlchemy
        certs_from_db = Certificate.query.order_by(Certificate.issue_date.desc()).all()

        # Преобразуем объекты в словари с помощью нашего нового метода to_dict()
        certificates = [cert.to_dict() for cert in certs_from_db]
        return jsonify(certificates), 200

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
    Находит прерванные сессии...
    ИСПРАВЛЕНО: Теперь пытается извлечь userInfo из деталей первого события,
    если пользователь не найден в основной таблице.
    """
    try:
        passing_score = current_app.config.get('PASSING_SCORE_THRESHOLD', 80)
        # CTE для получения всех завершенных сессий
        successful_sessions = db.session.query(
            ResultMetadata.session_id
        ).filter(
            ResultMetadata.session_id.isnot(None),
            ResultMetadata.score >= passing_score # <-- Используем переменную passing_score
        ).cte('successful_sessions')

        first_event = db.session.query(
            ProctoringEvent.session_id,
            ProctoringEvent.event_type,
            ProctoringEvent.event_timestamp,
            ProctoringEvent.details,
            ProctoringEvent.persistent_id,
            func.row_number().over(
                partition_by=ProctoringEvent.session_id,
                order_by=ProctoringEvent.event_timestamp.asc()
            ).label('rn')
        ).filter(
            ProctoringEvent.event_type.in_(['test_started', 'study_started']), # Добавлена запятая
            ProctoringEvent.details.isnot(None)
        ).cte('first_event')

        # Основной запрос для брошенных сессий
        abandoned_query = db.session.query(
            ProctoringEvent.session_id,
            func.max(first_event.c.event_type).label('session_type'),
            func.min(ProctoringEvent.event_timestamp).label('start_time'),
            func.max(cast(first_event.c.details, Text)).label('first_event_details_text'),
            func.max(first_event.c.persistent_id).label('persistent_id'),
             # ... (остальные агрегатные функции) ...
            func.count(case((ProctoringEvent.event_type == 'focus_loss', 1))).label('focus_loss_count'),
            func.count(case((ProctoringEvent.event_type == 'screenshot_attempt', 1))).label('screenshot_count'),
            func.count(case((ProctoringEvent.event_type == 'print_attempt', 1))).label('print_count')
        ).select_from(
            ProctoringEvent
        ).outerjoin(
            first_event,
            and_(
                ProctoringEvent.session_id == first_event.c.session_id,
                first_event.c.rn == 1
            )
        ).filter(
            ~ProctoringEvent.session_id.in_(
                db.session.query(successful_sessions.c.session_id)
            )
        ).group_by(
            ProctoringEvent.session_id
        ).order_by(
            func.min(ProctoringEvent.event_timestamp).desc()
        )

        # Выполняем запрос
        abandoned_sessions = abandoned_query.all()

        if not abandoned_sessions:
            current_app.logger.info("No abandoned/unsuccessful sessions found.")
            return jsonify([]), 200

        # Собираем все persistent_id для batch-запроса пользователей
        persistent_ids = {s.persistent_id for s in abandoned_sessions if s.persistent_id}

        # Получаем информацию о пользователях одним запросом
        users_map = {}
        if persistent_ids:
            users = User.query.filter(User.persistent_id.in_(persistent_ids)).all()
            users_map = {user.persistent_id: user for user in users}

        # Маппинг типов сессий
        session_type_map = {
            'test_started': 'test',
            'study_started': 'study'
        }

        results = []
        for session in abandoned_sessions:
            first_event_details = {} # Инициализируем как пустой dict
            client_ip = "N/A"
            user_info_from_event = {} # Инициализируем как пустой dict
            if session.first_event_details_text:
                try:
                    first_event_details = json.loads(session.first_event_details_text)
                    if isinstance(first_event_details, dict):
                         client_ip = first_event_details.get('ip', 'N/A')
                         user_info_from_event = first_event_details.get('userInfo', {})
                    else:
                         current_app.logger.warning(
                             f"Parsed details is not a dict for session {session.session_id}"
                         )
                         first_event_details = {} # Сбрасываем до пустого словаря
                         user_info_from_event = {}
                except (json.JSONDecodeError, TypeError) as e:
                    current_app.logger.warning(
                        f"Could not parse details text for session {session.session_id}: {e}"
                    )
                    first_event_details = {} # Сбрасываем до пустого словаря
                    user_info_from_event = {}
            # --- ЛОГИКА ОПРЕДЕЛЕНИЯ USER INFO ---
            user = users_map.get(session.persistent_id)

            if user:
                # 1. Лучший случай: Пользователь найден в таблице users
                user_info = {'lastName': user.lastname, 'firstName': user.firstname}
            elif user_info_from_event and user_info_from_event.get('lastName'):
                # 2. Пользователя нет, но есть userInfo в деталях события
                user_info = {
                    'lastName': user_info_from_event.get('lastName', 'N/A'),
                    'firstName': user_info_from_event.get('firstName', 'N/A'),
                    # Можно добавить отчество и должность, если нужно
                    # 'middleName': user_info_from_event.get('middleName'),
                    'source': 'event_log'
                }
                # Добавляем пометку, что данные из лога
            elif session.persistent_id:
                # 3. Пользователя нет, userInfo в событии нет, но есть persistent_id
                user_info = {'lastName': 'N/A', 'firstName': f"ID: {str(session.persistent_id)[:8]}..."}
            else:
                # 4. Совсем ничего нет
                user_info = {'lastName': 'N/A', 'firstName': 'N/A'}
            # --- КОНЕЦ ЛОГИКИ ОПРЕДЕЛЕНИЯ USER INFO ---

            results.append({
                'sessionId': session.session_id,
                'sessionType': session_type_map.get(session.session_type, 'unknown'),
                'startTime': session.start_time.isoformat() if session.start_time else 'N/A',
                'userInfo': user_info, # Используем собранный userInfo
                'clientIp': client_ip,
                'violationCounts': {
                    'focusLoss': session.focus_loss_count or 0,
                    'screenshots': session.screenshot_count or 0,
                    'prints': session.print_count or 0
                }
            })

        current_app.logger.info(
            f"Returning {len(results)} abandoned/unsuccessful sessions (safe JSON parsing)"
        )
        return jsonify(results), 200

    except DBAPIError as e: # Ловим ошибки базы данных (включая ошибки SQL синтаксиса/типов)
         db.session.rollback()
         current_app.logger.error(f"Database error in get_abandoned_sessions: {e}", exc_info=True)
         return jsonify({'status': 'error', 'message': 'Database error during analysis'}), 500
    except Exception as e:
        db.session.rollback() # Откат на всякий случай
        current_app.logger.error(
            f"Unexpected error in get_abandoned_sessions: {e}",
            exc_info=True
        )
        return jsonify({'status': 'error','message': 'Internal server error'}), 500
