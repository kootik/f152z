import os
import json
import sqlite3
import click
from flask import Flask, request, jsonify, render_template, send_from_directory, g
from flask_cors import CORS
from datetime import datetime, timedelta
import unidecode
from werkzeug.middleware.proxy_fix import ProxyFix
import traceback
from typing import Dict, List, Optional, Tuple, Any

# =============================================================================
# КОНФИГУРАЦИЯ И КОНСТАНТЫ
# =============================================================================

# Пути и директории
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, 'results_data')
DATABASE_PATH = os.path.join(BASE_DIR, 'app_data.db')
SSL_CERT_PATH = os.path.join(BASE_DIR, 'fz152.crt')
SSL_KEY_PATH = os.path.join(BASE_DIR, 'fz152.key')

# Пороги для поведенческого анализа
BEHAVIOR_THRESHOLDS = {
    'default': {
        'min_score': 90,
        'max_test_duration_sec': 180,
        'min_engagement_score': 15
    },
    'INFOSEC_117': {
        'min_score': 90,
        'max_test_duration_sec': 120,
        'min_engagement_score': 10
    }
}

# Соответствие типов тестов и страниц обучения
TEST_TO_STUDY_PAGE_MAP = {
    'INFOSEC_117': 'study-117',
    'PD_152': 'studytest-152'
}

# Пороги для успешного прохождения теста
PASSING_SCORE_THRESHOLD = 80

# Максимальное время поиска связанной учебной сессии (в часах)
MAX_STUDY_SESSION_LOOKUP_HOURS = 24

# =============================================================================
# ИНИЦИАЛИЗАЦИЯ ПРИЛОЖЕНИЯ
# =============================================================================

app = Flask(__name__, template_folder='templates', static_folder='static')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
CORS(app)

# Создание необходимых директорий
if not os.path.exists(RESULTS_DIR):
    os.makedirs(RESULTS_DIR)

# =============================================================================
# РАБОТА С БАЗОЙ ДАННЫХ
# =============================================================================

def get_db_connection() -> sqlite3.Connection:
    """
    Устанавливает соединение с БД SQLite, используя контекст приложения Flask.
    Предотвращает ошибки 'database is locked'.
    
    Returns:
        sqlite3.Connection: Объект соединения с базой данных
    """
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE_PATH)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def teardown_db(exception):
    """Закрывает соединение с БД после завершения запроса."""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()


def init_db():
    """
    Инициализирует таблицы базы данных и создает индексы для оптимизации производительности.
    """
    with app.app_context():
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Создание таблиц
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS document_counters (
                    period TEXT PRIMARY KEY,
                    last_sequence_number INTEGER NOT NULL
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS proctoring_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_timestamp TEXT NOT NULL,
                    details TEXT
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS certificates (
                    document_number TEXT PRIMARY KEY,
                    user_fullname TEXT NOT NULL,
                    user_position TEXT,
                    test_type TEXT NOT NULL,
                    issue_date TEXT NOT NULL,
                    score_percentage INTEGER NOT NULL,
                    session_id TEXT NOT NULL
                )
            ''')
            
            # Создание индексов для оптимизации запросов
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_proctoring_events_session_id 
                ON proctoring_events(session_id)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_proctoring_events_event_type 
                ON proctoring_events(event_type)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_proctoring_events_timestamp 
                ON proctoring_events(event_timestamp)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_certificates_issue_date 
                ON certificates(issue_date)
            ''')
            
            conn.commit()
            app.logger.info("База данных успешно инициализирована")
            
        except sqlite3.Error as e:
            app.logger.error(f"Ошибка при инициализации базы данных: {e}")
            conn.rollback()
            raise


@app.cli.command("init-db")
def init_db_command():
    """CLI команда для создания таблиц базы данных."""
    try:
        init_db()
        click.echo("База данных успешно инициализирована.")
    except Exception as e:
        click.echo(f"Ошибка при инициализации базы данных: {e}")


def get_next_document_number() -> str:
    """
    Генерирует следующий номер документа в формате ГГ/ММ-XXXX.
    
    Returns:
        str: Уникальный номер документа
        
    Raises:
        sqlite3.Error: При ошибке работы с базой данных
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        now = datetime.now()
        current_year_short = now.strftime("%y")
        current_month = now.strftime("%m")
        current_period = f"{current_year_short}/{current_month}"
        
        cursor.execute(
            "SELECT last_sequence_number FROM document_counters WHERE period = ?", 
            (current_period,)
        )
        row = cursor.fetchone()
        
        if row:
            next_sequence_number = row['last_sequence_number'] + 1
            cursor.execute(
                "UPDATE document_counters SET last_sequence_number = ? WHERE period = ?", 
                (next_sequence_number, current_period)
            )
        else:
            next_sequence_number = 1
            cursor.execute(
                "INSERT INTO document_counters (period, last_sequence_number) VALUES (?, ?)",
                (current_period, next_sequence_number)
            )
        
        conn.commit()
        document_number = f"{current_period}-{next_sequence_number:04d}"
        app.logger.info(f"Сгенерирован номер документа: {document_number}")
        return document_number
        
    except sqlite3.Error as e:
        app.logger.error(f"Ошибка при генерации номера документа: {e}")
        raise


# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================

def sanitize_filename(name_part: str) -> str:
    """
    Очищает строку для использования в имени файла.
    Удаляет недопустимые символы и выполняет транслитерацию.
    
    Args:
        name_part: Часть имени для очистки
        
    Returns:
        str: Очищенная строка
    """
    if not name_part:
        return "Unknown"
    
    # Транслитерация в латиницу
    name_part = unidecode.unidecode(str(name_part))
    # Оставляем только буквы, цифры, подчеркивания и дефисы
    name_part = "".join(c if c.isalnum() or c in ['_', '-'] else '_' for c in name_part)
    return name_part.strip('_') or "Unknown"


def validate_json_data(data: Dict[str, Any], required_fields: List[str]) -> Tuple[bool, str]:
    """
    Валидирует JSON данные на наличие обязательных полей.
    
    Args:
        data: Данные для валидации
        required_fields: Список обязательных полей
        
    Returns:
        Tuple[bool, str]: (True, "") если валидация прошла успешно, 
                         (False, error_message) в противном случае
    """
    if not data:
        return False, "Отсутствуют данные"
    
    missing_fields = [field for field in required_fields if field not in data]
    if missing_fields:
        return False, f"Отсутствуют обязательные поля: {', '.join(missing_fields)}"
    
    return True, ""


def save_certificate_to_db(document_number: str, user_info: Dict[str, Any], 
                          test_type: str, score_percentage: int, session_id: str) -> bool:
    """
    Сохраняет информацию о сертификате в базу данных.
    
    Args:
        document_number: Номер документа
        user_info: Информация о пользователе
        test_type: Тип теста
        score_percentage: Процент правильных ответов
        session_id: ID сессии
        
    Returns:
        bool: True если сохранение прошло успешно
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        full_name = " ".join([
            user_info.get('lastName', ''),
            user_info.get('firstName', ''),
            user_info.get('middleName', '')
        ]).strip()
        
        cursor.execute("""
            INSERT INTO certificates (document_number, user_fullname, user_position, 
                                    test_type, issue_date, score_percentage, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            document_number,
            full_name,
            user_info.get('position', ''),
            test_type,
            datetime.now().isoformat(),
            score_percentage,
            session_id
        ))
        conn.commit()
        app.logger.info(f"Сертификат {document_number} сохранен в БД для пользователя {full_name}")
        return True
        
    except sqlite3.Error as e:
        app.logger.error(f"Ошибка при сохранении сертификата в БД: {e}")
        return False


def load_completed_tests() -> List[Dict[str, Any]]:
    """
    Загружает все завершенные тесты из файловой системы.
    
    Returns:
        List[Dict]: Список данных завершенных тестов
    """
    completed_tests = []
    
    try:
        for filename in os.listdir(RESULTS_DIR):
            if not filename.endswith('.json'):
                continue
                
            filepath = os.path.join(RESULTS_DIR, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    test_data = json.load(f)
                    completed_tests.append(test_data)
            except (json.JSONDecodeError, FileNotFoundError) as e:
                app.logger.warning(f"Не удалось загрузить файл {filename}: {e}")
                continue
                
    except OSError as e:
        app.logger.error(f"Ошибка при чтении директории результатов: {e}")
    
    return completed_tests


# =============================================================================
# МАРШРУТЫ ДЛЯ СТАТИЧЕСКИХ ФАЙЛОВ
# =============================================================================

@app.route('/')
def index():
    """Отдает главную страницу с тестом."""
    return render_template('index.html')


@app.route('/questions_data-117.js')
def serve_questions_data117():
    """Отдает файл questions_data-117.js из статической папки."""
    return send_from_directory(app.static_folder, 'questions_data-117.js')


@app.route('/questions_data.js')
def serve_questions_data():
    """Отдает файл questions_data.js из статической папки."""
    return send_from_directory(app.static_folder, 'questions_data.js')


@app.route('/jspdf.umd.min.js')
def serve_jspdf():
    """Отдает файл jspdf.umd.min.js из статической папки."""
    return send_from_directory(app.static_folder, 'jspdf.umd.min.js')


@app.route('/jspdf.umd.min.js.map')
def serve_jspdf_map():
    """Отдает файл jspdf.umd.min.js.map из статической папки."""
    return send_from_directory(app.static_folder, 'jspdf.umd.min.js.map')


@app.route('/html2canvas.min.js')
def serve_html2canvas():
    """Отдает файл html2canvas.min.js из статической папки."""
    return send_from_directory(app.static_folder, 'html2canvas.min.js')


@app.route('/FKGroteskNeue.woff2')
def serve_FKGroteskNeue():
    """Отдает файл FKGroteskNeue.woff2 из статической папки."""
    return send_from_directory(app.static_folder, 'FKGroteskNeue.woff2')


# =============================================================================
# МАРШРУТЫ ДЛЯ HTML СТРАНИЦ
# =============================================================================

@app.route('/results')
def show_results_page():
    """Отдает HTML-страницу для отображения результатов."""
    return render_template('display_results.html')


@app.route('/152test')
def show_152test_page():
    """Отдает HTML-страницу для тестирования ПД-152."""
    return render_template('studytest.html')


@app.route('/117infographic')
def show_117infographic_page():
    """Отдает HTML-страницу с инфографикой для 117-ФЗ."""
    return render_template('infographic-117.html')


@app.route('/117study')
def show_117study_page():
    """Отдает HTML-страницу для обучения по 117-ФЗ."""
    return render_template('study-117.html')


@app.route('/152info')
def show_152info_page():
    """Отдает HTML-страницу с информацией по ПД-152."""
    return render_template('152info.html')


@app.route('/117test')
def show_117test_page():
    """Отдает HTML-страницу для тестирования по 117-ФЗ."""
    return render_template('117-test.html')


@app.route('/study')
def show_study_page():
    """Отдает HTML-страницу для общего обучения."""
    return render_template('study.html')


@app.route('/index2')
def show_index2_page():
    """Отдает альтернативную главную страницу."""
    return render_template('index2.html')


# =============================================================================
# API МАРШРУТЫ ДЛЯ РАБОТЫ С РЕЗУЛЬТАТАМИ ТЕСТОВ
# =============================================================================

@app.route('/api/save_results', methods=['POST'])
def save_results():
    """
    Принимает результаты теста в формате JSON, генерирует номер документа при успешной сдаче,
    и сохраняет данные в файл и базу данных.
    
    Returns:
        JSON response с информацией о сохранении
    """
    try:
        data = request.get_json()
        user_ip = request.remote_addr
        
        # Валидация входных данных
        is_valid, error_message = validate_json_data(data, ['userInfo', 'testResults'])
        if not is_valid:
            app.logger.warning(f"Получены невалидные данные от {user_ip}: {error_message}")
            return jsonify({"status": "error", "message": error_message}), 400

        user_info = data.get('userInfo', {})
        test_results = data.get('testResults', {})
        session_id = data.get('sessionId', 'Unknown')
        
        # Добавляем метаданные сервера
        data['serverReceiveTimestamp'] = datetime.now().isoformat()
        data['clientIp'] = user_ip
        
        official_document_number = None
        score_percentage = test_results.get('percentage', 0)
        
        # Генерируем официальный номер документа при успешном прохождении
        if score_percentage >= PASSING_SCORE_THRESHOLD:
            try:
                official_document_number = get_next_document_number()
                data['officialDocumentNumber'] = official_document_number
                
                # Сохраняем сертификат в БД
                save_certificate_to_db(
                    official_document_number,
                    user_info,
                    data.get('testType', 'N/A'),
                    score_percentage,
                    session_id
                )
                
            except Exception as e:
                app.logger.error(f"Ошибка при создании сертификата для сессии {session_id}: {e}")
        
        # Формируем имя файла
        last_name = sanitize_filename(user_info.get('lastName', 'Unknown'))
        first_name = sanitize_filename(user_info.get('firstName', 'User'))
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        doc_num_part = f"_{official_document_number.replace('/', '-')}" if official_document_number else ""
        filename = f"result_{last_name}_{first_name}{doc_num_part}_{timestamp_str}.json"
        
        # Сохраняем файл
        filepath = os.path.join(RESULTS_DIR, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        
        app.logger.info(f"Результаты сохранены: {filename}, сессия: {session_id}, балл: {score_percentage}%")
        
        response_data = {
            "status": "success", 
            "message": "Results saved", 
            "filename": filename
        }
        if official_document_number:
            response_data["officialDocumentNumber"] = official_document_number
        
        return jsonify(response_data), 201
        
    except json.JSONDecodeError:
        app.logger.warning(f"Получены некорректные JSON данные от {request.remote_addr}")
        return jsonify({"status": "error", "message": "Invalid JSON data"}), 400
    except OSError as e:
        app.logger.error(f"Ошибка при сохранении файла: {e}")
        return jsonify({"status": "error", "message": "File system error"}), 500
    except Exception as e:
        app.logger.error(f"Неожиданная ошибка при сохранении результатов: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.route('/api/get_results', methods=['GET'])
def get_results_api():
    """
    Возвращает все сохраненные результаты тестов из файловой системы.
    
    Returns:
        JSON список всех результатов тестов, отсортированный по времени (новые первыми)
    """
    try:
        results_list = load_completed_tests()
        
        # Сортируем по времени получения сервером
        results_list.sort(
            key=lambda x: x.get('serverReceiveTimestamp', x.get('clientSubmitTimestamp', '')), 
            reverse=True
        )
        
        app.logger.info(f"Отправлено {len(results_list)} результатов тестов")
        return jsonify(results_list), 200
        
    except Exception as e:
        app.logger.error(f"Ошибка при получении результатов: {e}")
        return jsonify({"status": "error", "message": "Error retrieving results"}), 500


# =============================================================================
# API МАРШРУТЫ ДЛЯ РАБОТЫ С СОБЫТИЯМИ ПРОКТОРИНГА
# =============================================================================

@app.route('/api/log_event', methods=['POST'])
def log_event():
    """
    Принимает и логирует единичное событие прокторинга в базу данных.
    
    Returns:
        JSON response с результатом операции
    """
    try:
        data = request.get_json()
        user_ip = request.remote_addr
        
        # Валидация обязательных полей
        is_valid, error_message = validate_json_data(data, ['sessionId', 'eventType'])
        if not is_valid:
            app.logger.warning(f"Получено невалидное событие от {user_ip}: {error_message}")
            return jsonify({"status": "error", "message": error_message}), 400
        
        session_id = data.get('sessionId')
        event_type = data.get('eventType')
        event_timestamp = data.get('eventTimestamp', datetime.now().isoformat())
        details = data.get('details', {})
        details['ip'] = user_ip

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO proctoring_events (session_id, event_type, event_timestamp, details) 
               VALUES (?, ?, ?, ?)""",
            (session_id, event_type, event_timestamp, json.dumps(details))
        )
        conn.commit()
        
        app.logger.debug(f"Событие {event_type} записано для сессии {session_id}")
        return jsonify({"status": "success"}), 200
        
    except json.JSONDecodeError:
        app.logger.warning(f"Получены некорректные JSON данные события от {request.remote_addr}")
        return jsonify({"status": "error", "message": "Invalid JSON data"}), 400
    except sqlite3.Error as e:
        app.logger.error(f"Ошибка БД при записи события: {e}")
        return jsonify({"status": "error", "message": "Database error"}), 500
    except Exception as e:
        app.logger.error(f"Неожиданная ошибка при записи события: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.route('/api/get_events/<session_id>', methods=['GET'])
def get_events(session_id: str):
    """
    Возвращает все события прокторинга для указанной сессии.
    
    Args:
        session_id: Идентификатор сессии
        
    Returns:
        JSON список событий для указанной сессии
    """
    try:
        if not session_id or not session_id.strip():
            return jsonify({"status": "error", "message": "Invalid session ID"}), 400
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT * FROM proctoring_events 
               WHERE session_id = ? 
               ORDER BY event_timestamp ASC""",
            (session_id,)
        )
        events = [dict(row) for row in cursor.fetchall()]
        
        app.logger.info(f"Отправлено {len(events)} событий для сессии {session_id}")
        return jsonify(events), 200
        
    except sqlite3.Error as e:
        app.logger.error(f"Ошибка БД при получении событий для сессии {session_id}: {e}")
        return jsonify({"status": "error", "message": "Database error"}), 500
    except Exception as e:
        app.logger.error(f"Неожиданная ошибка при получении событий для сессии {session_id}: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


# =============================================================================
# API МАРШРУТЫ ДЛЯ РАБОТЫ С СЕРТИФИКАТАМИ
# =============================================================================

@app.route('/api/get_certificates', methods=['GET'])
def get_certificates():
    """
    Возвращает реестр всех выданных сертификатов из базы данных.
    
    Returns:
        JSON список всех сертификатов, отсортированный по дате выдачи
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM certificates ORDER BY issue_date DESC")
        certificates = [dict(row) for row in cursor.fetchall()]
        
        app.logger.info(f"Отправлено {len(certificates)} сертификатов")
        return jsonify(certificates), 200
        
    except sqlite3.Error as e:
        app.logger.error(f"Ошибка БД при получении сертификатов: {e}")
        return jsonify({"status": "error", "message": "Database error"}), 500
    except Exception as e:
        app.logger.error(f"Неожиданная ошибка при получении сертификатов: {e}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500


# =============================================================================
# ФУНКЦИИ ДЛЯ АНАЛИТИКИ
# =============================================================================

def get_completed_session_ids() -> set:
    """
    Получает ID всех успешно завершенных сессий из JSON-файлов.
    
    Returns:
        set: Множество ID завершенных сессий
    """
    completed_session_ids = set()
    completed_tests = load_completed_tests()
    
    for test_data in completed_tests:
        session_id = test_data.get('sessionId')
        if session_id and isinstance(session_id, str):
            completed_session_ids.add(session_id)
    
    return completed_session_ids


def get_all_started_sessions() -> List[sqlite3.Row]:
    """
    Получает информацию о всех начатых сессиях из базы данных.
    
    Returns:
        List[sqlite3.Row]: Список записей о начатых сессиях с метриками нарушений
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                session_id, 
                MIN(event_timestamp) as start_time,
                SUM(CASE WHEN event_type = 'focus_loss' THEN 1 ELSE 0 END) as focus_loss_count,
                SUM(CASE WHEN event_type = 'screenshot_attempt' THEN 1 ELSE 0 END) as screenshot_count,
                SUM(CASE WHEN event_type = 'print_attempt' THEN 1 ELSE 0 END) as print_count
            FROM proctoring_events
            GROUP BY session_id
        """)
        return cursor.fetchall()
        
    except sqlite3.Error as e:
        app.logger.error(f"Ошибка при получении начатых сессий: {e}")
        return []


def get_session_user_info(session_id: str) -> Tuple[Dict[str, Any], str, str]:
    """
    Получает информацию о пользователе и сессии из событий прокторинга.
    
    Args:
        session_id: ID сессии
        
    Returns:
        Tuple: (user_info, client_ip, session_type)
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT details, event_type FROM proctoring_events 
            WHERE session_id = ? AND (event_type = 'test_started' OR event_type = 'study_started') 
            LIMIT 1
        """, (session_id,))
        start_event_row = cursor.fetchone()
        
        user_info = {}
        client_ip = "N/A"
        session_type = "unknown"
        
        if start_event_row:
            try:
                details_json = json.loads(start_event_row['details'])
                client_ip = details_json.get('ip', "N/A")
                
                if start_event_row['event_type'] == 'test_started':
                    session_type = "test"
                    user_info = details_json.get('userInfo', {"lastName": "N/A"})
                elif start_event_row['event_type'] == 'study_started':
                    session_type = "study"
                    temp_user_info = details_json.get('userInfo')
                    if temp_user_info and temp_user_info.get('lastName'):
                        user_info = temp_user_info
                    else:
                        persistent_id = details_json.get('persistentId', 'N/A')
                        user_info = {
                            "lastName": "Учебная сессия",
                            "firstName": f"ID: {persistent_id[:8]}..." if persistent_id else 'N/A'
                        }
            except (json.JSONDecodeError, AttributeError, KeyError) as e:
                app.logger.warning(f"Ошибка при парсинге данных сессии {session_id}: {e}")
                user_info = {"lastName": "Ошибка данных"}
        
        return user_info, client_ip, session_type
        
    except sqlite3.Error as e:
        app.logger.error(f"Ошибка БД при получении информации о сессии {session_id}: {e}")
        return {"lastName": "Ошибка БД"}, "N/A", "unknown"


def find_related_study_session(test_start_time: str, test_persistent_id: str, 
                              test_ip: str, required_study_page: str) -> Optional[str]:
    """
    Находит связанную учебную сессию для тестовой сессии.
    
    Args:
        test_start_time: Время начала теста
        test_persistent_id: Постоянный ID пользователя
        test_ip: IP адрес пользователя
        required_study_page: Требуемая страница обучения
        
    Returns:
        Optional[str]: ID связанной учебной сессии или None
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Сначала ищем по persistent ID
        if test_persistent_id:
            cursor.execute("""
                SELECT session_id FROM proctoring_events
                WHERE event_type = 'study_started' AND event_timestamp < ? 
                AND json_extract(details, '$.persistentId') = ?
                AND json_extract(details, '$.page') = ?
                GROUP BY session_id ORDER BY MIN(event_timestamp) DESC LIMIT 1
            """, (test_start_time, test_persistent_id, required_study_page))
            result = cursor.fetchone()
            if result:
                return result['session_id']
        
        # Если не нашли по ID, ищем по IP в пределах 24 часов
        if test_ip:
            cursor.execute("""
                SELECT session_id FROM proctoring_events
                WHERE event_type = 'study_started' AND event_timestamp < ? 
                AND json_extract(details, '$.ip') = ?
                AND datetime(event_timestamp) > datetime(?, '-{} hours')
                AND json_extract(details, '$.page') = ?
                GROUP BY session_id ORDER BY MIN(event_timestamp) DESC LIMIT 1
            """.format(MAX_STUDY_SESSION_LOOKUP_HOURS), 
            (test_start_time, test_ip, test_start_time, required_study_page))
            result = cursor.fetchone()
            if result:
                return result['session_id']
        
        return None
        
    except sqlite3.Error as e:
        app.logger.error(f"Ошибка при поиске связанной учебной сессии: {e}")
        return None


def calculate_engagement_score(study_session_id: str) -> Tuple[int, int]:
    """
    Рассчитывает индекс вовлеченности пользователя в учебную сессию.
    
    Args:
        study_session_id: ID учебной сессии
        
    Returns:
        Tuple[int, int]: (engagement_score, study_duration_seconds)
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        engagement_score = 0
        study_duration = 0
        
        # Вычисляем длительность сессии
        cursor.execute("""
            SELECT MIN(event_timestamp), MAX(event_timestamp) 
            FROM proctoring_events WHERE session_id = ?
        """, (study_session_id,))
        times = cursor.fetchone()
        
        if times and times[0] and times[1]:
            try:
                start = datetime.fromisoformat(times[0].replace('Z', ''))
                end = datetime.fromisoformat(times[1].replace('Z', ''))
                study_duration = int((end - start).total_seconds())
            except ValueError as e:
                app.logger.warning(f"Ошибка при парсинге времени для сессии {study_session_id}: {e}")
        
        # Баллы за время просмотра модулей
        cursor.execute("""
            SELECT details FROM proctoring_events 
            WHERE session_id = ? AND event_type = 'module_view_time'
        """, (study_session_id,))
        module_events = cursor.fetchall()
        
        total_module_view_time = 0
        for row in module_events:
            try:
                details = json.loads(row['details'])
                total_module_view_time += details.get('duration', 0)
            except (json.JSONDecodeError, KeyError):
                continue
        
        engagement_score += int(total_module_view_time / 60)  # 1 балл за минуту
        
        # Баллы за прокрутку
        cursor.execute("""
            SELECT details FROM proctoring_events 
            WHERE session_id = ? AND event_type = 'scroll_depth_milestone'
        """, (study_session_id,))
        scroll_events = cursor.fetchall()
        
        max_depth = 0
        for row in scroll_events:
            try:
                details = json.loads(row['details'])
                depth_str = details.get('depth', '0%').replace('%', '')
                max_depth = max(max_depth, int(depth_str))
            except (json.JSONDecodeError, ValueError, KeyError):
                continue
        
        if max_depth >= 95:
            engagement_score += 10
        elif max_depth >= 50:
            engagement_score += 5
        
        # Баллы за ответы на вопросы самоконтроля
        cursor.execute("""
            SELECT COUNT(*) FROM proctoring_events 
            WHERE session_id = ? AND event_type = 'self_check_answered'
        """, (study_session_id,))
        self_check_count = cursor.fetchone()[0]
        engagement_score += self_check_count * 2
        
        return engagement_score, study_duration
        
    except sqlite3.Error as e:
        app.logger.error(f"Ошибка при расчете индекса вовлеченности для сессии {study_session_id}: {e}")
        return 0, 0


# =============================================================================
# API МАРШРУТЫ ДЛЯ АНАЛИТИКИ
# =============================================================================

@app.route('/api/get_abandoned_sessions', methods=['GET'])
def get_abandoned_sessions():
    """
    Находит сессии, которые были начаты, но не завершены успешно.
    
    Returns:
        JSON список прерванных сессий с информацией о пользователях и нарушениях
    """
    try:
        completed_session_ids = get_completed_session_ids()
        all_started_sessions = get_all_started_sessions()
        
        abandoned_sessions = []
        
        for session in all_started_sessions:
            session_id = session['session_id']
            
            # Если сессия была начата, но не завершена
            if session_id not in completed_session_ids:
                user_info, client_ip, session_type = get_session_user_info(session_id)
                
                abandoned_sessions.append({
                    "sessionId": session_id,
                    "sessionType": session_type,
                    "startTime": session['start_time'],
                    "userInfo": user_info,
                    "clientIp": client_ip,
                    "violationCounts": {
                        "focusLoss": session['focus_loss_count'],
                        "screenshots": session['screenshot_count'],
                        "prints": session['print_count']
                    }
                })
        
        # Сортируем по времени начала (новые первыми)
        abandoned_sessions.sort(key=lambda x: x['startTime'], reverse=True)
        
        app.logger.info(f"Найдено {len(abandoned_sessions)} прерванных сессий")
        return jsonify(abandoned_sessions), 200

    except Exception as e:
        app.logger.error(f"Ошибка при получении прерванных сессий: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": "Error analyzing abandoned sessions"}), 500


@app.route('/api/get_behavior_analysis', methods=['GET'])
def get_behavior_analysis():
    """
    Выполняет поведенческий анализ для выявления подозрительных паттернов сдачи тестов.
    Анализирует связь между учебными сессиями и результатами тестов.
    
    Returns:
        JSON список подозрительных сессий с детальным анализом
    """
    try:
        completed_tests = load_completed_tests()
        suspicious_sessions = []

        for test in completed_tests:
            test_start_time = test.get('sessionMetrics', {}).get('startTime')
            test_persistent_id = test.get('persistentId', {}).get('cookie')
            test_ip = test.get('clientIp')
            test_type = test.get('testType', 'default')
            
            # Получаем пороги для текущего типа теста
            thresholds = BEHAVIOR_THRESHOLDS.get(test_type, BEHAVIOR_THRESHOLDS['default'])
            required_study_page = TEST_TO_STUDY_PAGE_MAP.get(test_type)
            
            # Проверяем наличие необходимых данных
            if not all([test_start_time, required_study_page, (test_persistent_id or test_ip)]):
                continue
            
            # Ищем связанную учебную сессию
            study_session_id = find_related_study_session(
                test_start_time, test_persistent_id, test_ip, required_study_page
            )
            
            engagement_score = 0
            study_duration = 0
            
            if study_session_id:
                engagement_score, study_duration = calculate_engagement_score(study_session_id)
            
            # Вычисляем метрики теста
            try:
                test_duration = (
                    datetime.fromisoformat(test['sessionMetrics']['endTime'].replace('Z', '')) -
                    datetime.fromisoformat(test['sessionMetrics']['startTime'].replace('Z', ''))
                ).total_seconds()
                test_score = test['testResults']['percentage']
            except (KeyError, ValueError) as e:
                app.logger.warning(f"Ошибка при обработке метрик теста: {e}")
                continue
            
            # Проверяем подозрительные паттерны
            if (test_score >= thresholds['min_score'] and
                test_duration < thresholds['max_test_duration_sec'] and
                engagement_score < thresholds['min_engagement_score']):
                
                reason = (
                    f"Высокий балл ({test_score}%) при быстром прохождении "
                    f"({int(test_duration)} сек) и низком индексе вовлеченности в обучение "
                    f"(Очки: {engagement_score})."
                )
                
                suspicious_sessions.append({
                    "userInfo": test.get('userInfo'),
                    "testResult": {
                        "score": test_score, 
                        "duration": int(test_duration)
                    },
                    "studyInfo": {
                        "duration": study_duration,
                        "engagementScore": engagement_score
                    },
                    "reason": reason,
                    "sessionId": test.get('sessionId')
                })

        app.logger.info(f"Найдено {len(suspicious_sessions)} подозрительных сессий")
        return jsonify(suspicious_sessions), 200

    except Exception as e:
        app.logger.error(f"Ошибка при поведенческом анализе: {e}")
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": "Error in behavioral analysis"}), 500


# =============================================================================
# ЗАПУСК ПРИЛОЖЕНИЯ
# =============================================================================

if __name__ == '__main__':
    # Инициализация базы данных при запуске
    try:
        init_db()
    except Exception as e:
        print(f"Ошибка при инициализации базы данных: {e}")
        exit(1)
    
    # Проверка наличия SSL сертификатов
    if os.path.exists(SSL_CERT_PATH) and os.path.exists(SSL_KEY_PATH):
        print("--- Запуск в режиме HTTPS ---")
        ssl_context = (SSL_CERT_PATH, SSL_KEY_PATH)
        app.run(host='0.0.0.0', port=5000, debug=True, ssl_context=ssl_context)
    else:
        print("--- Файлы сертификата не найдены. Запуск в обычном режиме HTTP ---")
        app.run(host='0.0.0.0', port=5000, debug=True)