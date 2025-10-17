# app/api/routes.py

import json
from datetime import UTC, datetime, timedelta

from flask import current_app, jsonify, request
from pydantic import ValidationError
from sqlalchemy import and_, case, func, or_
from sqlalchemy.exc import IntegrityError

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
    """
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
        # --- Пользователь (User) ---
        user_info = validated_data.userInfo
        user = User.query.filter_by(persistent_id=persistent_id).first()
        if not user:
            user = User(
                lastname=user_info.lastName,
                firstname=user_info.firstName,
                middlename=user_info.middleName,
                position=user_info.position,
                persistent_id=persistent_id,
            )
            db.session.add(user)
            db.session.flush()

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
            return (
                jsonify(
                    {"status": "error", "message": f"Session {session_id} not found"}
                ),
                404,
            )

        # Разбор данных из Pydantic-объекта
        score = validated_data.testResults.percentage
        start_time_str = (
            validated_data.dict().get("sessionMetrics", {}).get("startTime")
        )  # dict() для вложенных полей
        end_time_str = validated_data.dict().get("sessionMetrics", {}).get("endTime")

        # Безопасная обработка дат
        start_time, end_time = None, None
        if start_time_str:
            try:
                start_time = datetime.fromisoformat(start_time_str.replace("Z", ""))
            except (ValueError, TypeError):
                current_app.logger.warning(
                    f"Invalid start_time format: '{start_time_str}'"
                )
        if end_time_str:
            try:
                end_time = datetime.fromisoformat(end_time_str.replace("Z", ""))
            except (ValueError, TypeError):
                current_app.logger.warning(f"Invalid end_time format: '{end_time_str}'")

        # Обновляем поля существующей записи
        result.user_id = user.id
        result.fingerprint_hash = fp_hash
        # <--- ИЗМЕНЕНИЕ: Берем test_type из корневого объекта, как его шлет фронтенд ---
        result.test_type = validated_data.test_type
        result.score = validated_data.testResults.percentage
        result.start_time = validated_data.sessionMetrics.startTime
        result.end_time = validated_data.sessionMetrics.endTime
        result.raw_data = validated_data.model_dump(
            mode="json", by_alias=True
        )  # Более правильный способ сериализации
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
                # <--- ИЗМЕНЕНИЕ: Используем правильное поле test_type и здесь ---
                test_type=validated_data.test_type,
                score_percentage=result.score,
                session_id=session_id,
            )
            db.session.add(certificate)

        # --- Фиксация транзакции ---
        db.session.commit()

        # --- Действия после успешного сохранения ---

        # 1. Очистка кэша
        cache.delete_memoized(get_results_api)
        cache.delete_memoized(get_certificates)

        # 2. Уведомление клиентов через WebSocket
        socketio.emit("update_needed", {"type": "new_result", "session_id": session_id})

        # <--- ИЗМЕНЕНИЕ: Инкремент метрик Prometheus ---
        result_status = "passed" if passed else "failed"
        TESTS_COMPLETED_TOTAL.labels(
            test_type=validated_data.test_type, result=result_status
        ).inc()
        if document_number:
            DOCUMENTS_GENERATED_TOTAL.labels(test_type=validated_data.test_type).inc()
        # -----------------------------------------------

        current_app.logger.info(
            f"Results saved: session={session_id}, score={score}%, doc={document_number}"
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
        current_app.logger.error(f"DB integrity error in save_results: {e}")
        return jsonify({"status": "error", "message": "Data conflict"}), 409
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            f"Unexpected error in save_results: {e}", exc_info=True
        )
        return jsonify({"status": "error", "message": "Internal server error"}), 500


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
    """Возвращает результаты с пагинацией из БД."""
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
        # чтобы избежать лишних запросов к БД (N+1 проблема).
        base_query = ResultMetadata.query.options(
            db.joinedload(ResultMetadata.user)
        ).order_by(ResultMetadata.start_time.desc())

        # Используем встроенную пагинацию SQLAlchemy - это проще и надежнее
        pagination = base_query.paginate(page=page, per_page=per_page, error_out=False)
        results_from_db = pagination.items
        total = pagination.total

        results = []
        for row in results_from_db:
            score = row.score if row.score is not None else 0
            if score >= 90:
                grade_class, grade_text = "excellent", "Отлично"
            elif score >= 80:
                grade_class, grade_text = "good", "Хорошо"
            elif score >= 60:
                grade_class, grade_text = "satisfactory", "Удовлетворительно"
            else:
                grade_class, grade_text = "poor", "Неудовлетворительно"

            results.append(
                {
                    "sessionId": row.session_id,
                    "testType": row.test_type,
                    "clientIp": row.client_ip,
                    "userInfo": {
                        "lastName": row.user.lastname if row.user else None,
                        "firstName": row.user.firstname if row.user else None,
                    },
                    "testResults": {
                        "percentage": score,
                        "grade": {"class": grade_class, "text": grade_text},
                    },
                    "sessionMetrics": {
                        "startTime": (
                            row.start_time.isoformat() if row.start_time else None
                        ),
                        "endTime": row.end_time.isoformat() if row.end_time else None,
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
    Находит прерванные сессии одним эффективным запросом к БД.
    """
    try:
        # 1. Создаем подзапрос, который выберет ID всех ЗАВЕРШЕННЫХ сессий.
        completed_sessions_sq = db.session.query(
            ResultMetadata.session_id
        ).scalar_subquery()

        # 2. Основной запрос:
        #    - Агрегируем данные из таблицы событий (proctoring_events).
        #    - Присоединяем информацию о пользователе через User.
        #    - Исключаем все сессии, ID которых есть в нашем подзапросе.
        abandoned_query = (
            db.session.query(
                ProctoringEvent.session_id,
                func.min(ProctoringEvent.event_timestamp).label("start_time"),
                func.max(ProctoringEvent.persistent_id).label(
                    "persistent_id"
                ),  # Получаем persistent_id
                func.count(case((ProctoringEvent.event_type == "focus_loss", 1))).label(
                    "focus_loss_count"
                ),
                func.count(
                    case((ProctoringEvent.event_type == "screenshot_attempt", 1))
                ).label("screenshot_count"),
                func.count(
                    case((ProctoringEvent.event_type == "print_attempt", 1))
                ).label("print_count"),
                User.lastname,
                User.firstname,
            )
            .outerjoin(User, ProctoringEvent.persistent_id == User.persistent_id)
            .filter(ProctoringEvent.session_id.notin_(completed_sessions_sq))
            .group_by(ProctoringEvent.session_id, User.lastname, User.firstname)
            .order_by(func.min(ProctoringEvent.event_timestamp).desc())
        )

        abandoned_sessions_details = abandoned_query.all()

        abandoned_results = []
        for session in abandoned_sessions_details:
            abandoned_results.append(
                {
                    "sessionId": session.session_id,
                    "sessionType": "unknown",  # Тип сессии определить сложнее без доп. запроса, пока оставим так
                    "startTime": session.start_time.isoformat(),
                    "userInfo": {
                        "lastName": session.lastname or "N/A",
                        "firstName": (
                            session.firstname
                            or f"ID: {str(session.persistent_id)[:8]}..."
                            if session.persistent_id
                            else "N/A"
                        ),
                    },
                    "clientIp": "N/A",  # IP теперь сложнее получить одним запросом, пока оставляем N/A
                    "violationCounts": {
                        "focusLoss": session.focus_loss_count,
                        "screenshots": session.screenshot_count,
                        "prints": session.print_count,
                    },
                }
            )

        current_app.logger.info(
            f"Found {len(abandoned_results)} abandoned sessions (Optimized)"
        )
        return jsonify(abandoned_results), 200

    except Exception as e:
        current_app.logger.error(f"Error in get_abandoned_sessions: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Error analyzing sessions"}), 500
