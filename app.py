import os
import json
import sqlite3
from contextlib import contextmanager
from flask import Flask, request, jsonify, render_template, send_from_directory, g
from flask_cors import CORS
from datetime import datetime, timedelta
from werkzeug.middleware.proxy_fix import ProxyFix
import traceback
from typing import Dict, List, Optional, Tuple, Any
import logging
import secrets
import re

from flask_sock import Sock
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import numpy as np
from scipy.spatial.distance import euclidean
from fastdtw import fastdtw
import math

# =============================================================================
# CONFIGURATION & CONSTANTS
# =============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, 'results_data')
DATABASE_PATH = os.path.join(BASE_DIR, 'app_data.db')
SSL_CERT_PATH = os.path.join(BASE_DIR, 'fz152.crt')
SSL_KEY_PATH = os.path.join(BASE_DIR, 'fz152.key')
MAX_RESULTS_PER_PAGE = int(os.environ.get('MAX_RESULTS_PER_PAGE', 1000))
# Thresholds for behavioral analysis
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

TEST_TO_STUDY_PAGE_MAP = {
    'INFOSEC_117': 'study-117',
    'PD_152': 'studytest-152'
}

PASSING_SCORE_THRESHOLD = 80
MAX_STUDY_SESSION_LOOKUP_HOURS = 24
MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB
PAUSE_THRESHOLD_SEC = 0.25
MAX_DISTANCE_THRESHOLD_PX = 1000

# Input validation constants
MAX_SESSION_ID_LENGTH = 128
SESSION_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')
MAX_EVENT_TYPE_LENGTH = 64

# =============================================================================
# APPLICATION INITIALIZATION
# =============================================================================

# SECRET_KEY must be set via environment variable
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    # Генерируем временный ключ, если он не задан
    SECRET_KEY = secrets.token_hex(32) 
    
    # Выводим очень заметное предупреждение в лог
    logger.warning(
        "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
        "!!! ВНИМАНИЕ: SECRET_KEY не установлен в переменных окружения.\n"
        "!!! Используется временный, небезопасный ключ.\n"
        "!!! Все сессии пользователей будут сброшены после перезапуска.\n"
        "!!! ОБЯЗАТЕЛЬНО УСТАНОВИТЕ SECRET_KEY В ПРОДАКТИВЕ!\n"
        "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    )
# --- NEW CODE: Add server name configuration ---
# This helps Flask-WTF correctly validate the host in a proxied HTTPS setup.
APP_SERVER_NAME = os.environ.get('APP_SERVER_NAME', None)
PREFERRED_URL_SCHEME = os.environ.get('PREFERRED_URL_SCHEME', 'https')

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config.from_mapping({
    'SECRET_KEY': SECRET_KEY,
    'SERVER_NAME': APP_SERVER_NAME,
    'PREFERRED_URL_SCHEME': PREFERRED_URL_SCHEME,  # NEW: Force HTTPS in URL generation
    'MAX_CONTENT_LENGTH': MAX_CONTENT_LENGTH,
    'CACHE_TYPE': 'SimpleCache',
    'CACHE_DEFAULT_TIMEOUT': 300,
    'WTF_CSRF_TIME_LIMIT': None,
    'WTF_CSRF_SSL_STRICT': True,
    'WTF_CSRF_CHECK_DEFAULT': False,  # NEW: Only check when explicitly enabled
})

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
CORS(app, origins=["https://fz152.dprgek.loc:8443"], supports_credentials=True)

csrf = CSRFProtect(app)
sock = Sock(app)
cache = Cache(app)
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

clients = set()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create necessary directories
os.makedirs(RESULTS_DIR, exist_ok=True)

# =============================================================================
# INPUT VALIDATION HELPERS
# =============================================================================

# --- НОВЫЙ КОД: Добавьте эти две функции ---
def validate_session_id(session_id: str) -> bool:
    """Checks if the session ID format is valid."""
    if not session_id or not isinstance(session_id, str):
        return False
    return len(session_id) <= MAX_SESSION_ID_LENGTH and bool(SESSION_ID_PATTERN.match(session_id))

def validate_event_type(event_type: str) -> bool:
    """Checks if the event type format is valid."""
    if not event_type or not isinstance(event_type, str):
        return False
    return len(event_type) <= MAX_EVENT_TYPE_LENGTH
# --- КОНЕЦ НОВОГО КОДА ---


# --- NEW: Centralized Schema Definition ---
def get_db_schema() -> Dict[str, str]:
    """Returns a dictionary with CREATE statements for the target schema."""
    return {
        'users': '''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                lastname TEXT, firstname TEXT, middlename TEXT, position TEXT,
                persistent_id TEXT UNIQUE NOT NULL
            )
        ''',
        'fingerprints': '''
            CREATE TABLE IF NOT EXISTS fingerprints (
                fingerprint_hash TEXT PRIMARY KEY,
                user_agent TEXT, platform TEXT, webgl_renderer TEXT,
                first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
            )
        ''',
        'result_metadata': '''
            CREATE TABLE IF NOT EXISTS result_metadata (
                session_id TEXT PRIMARY KEY, user_id INTEGER, fingerprint_hash TEXT,
                test_type TEXT, score INTEGER, start_time TEXT, end_time TEXT,
                filename TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id),
                FOREIGN KEY(fingerprint_hash) REFERENCES fingerprints(fingerprint_hash)
            )
        ''',
        'document_counters': '''
            CREATE TABLE IF NOT EXISTS document_counters (
                period TEXT PRIMARY KEY, last_sequence_number INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''',
        'proctoring_events': '''
            CREATE TABLE IF NOT EXISTS proctoring_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                event_type TEXT NOT NULL, event_timestamp TEXT NOT NULL, details TEXT,
                persistent_id TEXT, client_ip TEXT, page TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''',
        'certificates': '''
            CREATE TABLE IF NOT EXISTS certificates (
                document_number TEXT PRIMARY KEY, user_fullname TEXT NOT NULL,
                user_position TEXT, test_type TEXT NOT NULL, issue_date TEXT NOT NULL,
                score_percentage INTEGER NOT NULL, session_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        '''
    }

@contextmanager
def get_db():
    """Context manager for database connection."""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(
            DATABASE_PATH,
            timeout=10.0,
            isolation_level='IMMEDIATE'
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys = ON") # Enable foreign key constraints
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        pass  # Connection closed in teardown


@app.teardown_appcontext
def close_db(exception):
    """Close database connection after request."""
    db = g.pop('_database', None)
    if db is not None:
        db.close()

def get_db_schema_version(cursor: sqlite3.Cursor) -> int:
    """Gets the current schema version from PRAGMA."""
    return cursor.execute("PRAGMA user_version").fetchone()[0]

# --- REVISED: init_db function ---
def init_db():
    """Initializes the database if it's empty and checks the schema version."""
    with app.app_context():
        with get_db() as conn:
            cursor = conn.cursor()
            current_version = get_db_schema_version(cursor)

            # Scenario 1: Fresh database (version 0)
            if current_version == 0:
                logger.info("New database detected. Creating schema from scratch for version %s.", DB_SCHEMA_VERSION)
                schema = get_db_schema()
                for table_sql in schema.values():
                    cursor.execute(table_sql)
                
                # Create indexes
                indexes = [
                    'CREATE INDEX IF NOT EXISTS idx_events_session ON proctoring_events(session_id)',
                    'CREATE UNIQUE INDEX IF NOT EXISTS idx_users_persistent_id ON users(persistent_id)',
                    'CREATE INDEX IF NOT EXISTS idx_metadata_user_id ON result_metadata(user_id)',
                ]
                for idx_sql in indexes:
                    cursor.execute(idx_sql)

                cursor.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
                conn.commit()
                logger.info("Database schema created successfully.")

            # Scenario 2: Existing but outdated database
            elif current_version < DB_SCHEMA_VERSION:
                logger.critical(
                    f"DB schema version is outdated ({current_version})! "
                    f"Required: V{DB_SCHEMA_VERSION}. "
                    f"Please run the migration script (e.g., migrate_v2.py)."
                )
                # In a real production environment, you might want to stop the app
                # exit(1)
            
            # Scenario 3: Database is up-to-date
            else:
                logger.info(f"DB schema check passed. Current version: V{current_version}.")


def get_next_document_number() -> str:
    """
    Generate next document number in format YY/MM-XXXX.
    FIXED: Uses proper transaction locking to prevent race conditions.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        
        now = datetime.now()
        current_period = now.strftime("%y/%m")
        
        # Use UPDATE ... RETURNING if SQLite version supports it, otherwise use transaction
        try:
            cursor.execute("BEGIN IMMEDIATE")  # Acquire lock immediately
            
            cursor.execute(
                "SELECT last_sequence_number FROM document_counters WHERE period = ?",
                (current_period,)
            )
            row = cursor.fetchone()
            
            if row:
                next_seq = row['last_sequence_number'] + 1
                cursor.execute(
                    "UPDATE document_counters SET last_sequence_number = ?, updated_at = ? WHERE period = ?",
                    (next_seq, now.isoformat(), current_period)
                )
            else:
                next_seq = 1
                cursor.execute(
                    "INSERT INTO document_counters (period, last_sequence_number, updated_at) VALUES (?, ?, ?)",
                    (current_period, next_seq, now.isoformat())
                )
            
            conn.commit()
            document_number = f"{current_period}-{next_seq:04d}"
            logger.info(f"Generated document number: {document_number}")
            return document_number
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Error generating document number: {e}")
            raise


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def sanitize_filename(name_part: str) -> str:
    """Sanitize string for safe filename usage."""
    if not name_part or not isinstance(name_part, str):
        return "Unknown"
    
    import unidecode
    name_part = unidecode.unidecode(name_part)
    name_part = "".join(c if c.isalnum() or c in ['_', '-'] else '_' for c in name_part)
    return name_part.strip('_') or "Unknown"


def validate_json_data(data: Dict[str, Any], required_fields: List[str]) -> Tuple[bool, str]:
    """Validate JSON data for required fields."""
    if not data or not isinstance(data, dict):
        return False, "Invalid or missing data"
    
    missing = [f for f in required_fields if f not in data]
    if missing:
        return False, f"Missing required fields: {', '.join(missing)}"
    
    return True, ""


def save_certificate_to_db(document_number: str, user_info: Dict[str, Any],
                          test_type: str, score_percentage: int, session_id: str):
    """Save certificate information to database. Raises exception on error."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        full_name = " ".join([
            str(user_info.get('lastName', '')),
            str(user_info.get('firstName', '')),
            str(user_info.get('middleName', ''))
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
        logger.info(f"Certificate {document_number} saved for {full_name}")


def save_result_metadata(session_id: str, data: Dict, filename: str):
    """NEW: Save result metadata to database for faster querying."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            user_info = data.get('userInfo', {})
            test_results = data.get('testResults', {})
            session_metrics = data.get('sessionMetrics', {})
            
            cursor.execute("""
                INSERT OR REPLACE INTO result_metadata 
                (session_id, user_lastname, user_firstname, test_type, score, start_time, end_time, filename)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                user_info.get('lastName'),
                user_info.get('firstName'),
                data.get('testType'),
                test_results.get('percentage'),
                session_metrics.get('startTime'),
                session_metrics.get('endTime'),
                filename
            ))
            conn.commit()
    except sqlite3.Error as e:
        logger.warning(f"Failed to save metadata for {session_id}: {e}")


def load_completed_tests(page: int = 1, per_page: int = 20) -> Tuple[List[Dict], int]:
    """
    IMPROVED: Load completed tests with pagination using metadata cache.
    NOTE: This is retained for analytics that need the full JSON data.
    """
    try:
        # Try to use metadata cache first
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as total FROM result_metadata")
            total_row = cursor.fetchone()
            
            if total_row and total_row['total'] > 0:
                # Use metadata cache
                offset = (page - 1) * per_page
                cursor.execute("""
                    SELECT filename FROM result_metadata 
                    ORDER BY start_time DESC 
                    LIMIT ? OFFSET ?
                """, (per_page, offset))
                
                filenames = [row['filename'] for row in cursor.fetchall()]
                total = total_row['total']
                
                tests = []
                for filename in filenames:
                    filepath = os.path.join(RESULTS_DIR, filename)
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            tests.append(json.load(f))
                    except (json.JSONDecodeError, OSError) as e:
                        logger.warning(f"Failed to load {filename}: {e}")
                        continue
                
                return tests, total
        
        # Fallback to file-based approach if cache is empty
        all_files = sorted(
            [f for f in os.listdir(RESULTS_DIR) if f.endswith('.json')],
            key=lambda x: os.path.getmtime(os.path.join(RESULTS_DIR, x)),
            reverse=True
        )
        
        total = len(all_files)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_files = all_files[start_idx:end_idx]
        
        tests = []
        for filename in page_files:
            filepath = os.path.join(RESULTS_DIR, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    tests.append(json.load(f))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load {filename}: {e}")
                continue
        
        return tests, total
        
    except OSError as e:
        logger.error(f"Error reading results directory: {e}")
        return [], 0


# =============================================================================
# MOUSE TRAJECTORY ANALYSIS
# =============================================================================

def extract_initial_stroke(trajectory: List[List[float]]) -> List[List[float]]:
    """Extract only the first deliberate movement before a long pause."""
    if len(trajectory) < 2:
        return trajectory
    
    for i in range(1, len(trajectory)):
        time_delta = (trajectory[i][2] - trajectory[i-1][2]) / 1000.0
        if time_delta > PAUSE_THRESHOLD_SEC:
            return trajectory[:i]
    
    return trajectory


def compare_mouse_trajectories(traj1: List[List[float]], traj2: List[List[float]]) -> float:
    """
    Compare two mouse trajectories using three-factor analysis.
    Returns similarity percentage (0-100).
    """
    stroke1 = extract_initial_stroke(traj1)
    stroke2 = extract_initial_stroke(traj2)

    if not stroke1 or not stroke2 or len(stroke1) < 10 or len(stroke2) < 10:
        return 0.0

    points1 = np.array([[p[0], p[1]] for p in stroke1])
    points2 = np.array([[p[0], p[1]] for p in stroke2])

    # FACTOR 1: Shape similarity (DTW)
    def get_shape_similarity(p1, p2):
        def normalize(points):
            min_coords = points.min(axis=0)
            max_coords = points.max(axis=0)
            range_coords = max_coords - min_coords
            range_coords[range_coords == 0] = 1
            return 1000 * (points - min_coords) / range_coords

        norm_p1 = normalize(p1)
        norm_p2 = normalize(p2)
        distance, _ = fastdtw(norm_p1, norm_p2, dist=euclidean)
        
        max_dist = 1000 * math.sqrt(2) * max(len(norm_p1), len(norm_p2))
        normalized_distance = distance / max_dist if max_dist > 0 else 1.0
        
        similarity = (1 - normalized_distance) * 100
        return max(0, min(100, similarity))

    shape_sim = get_shape_similarity(points1, points2)

    # FACTOR 2: Scale similarity
    def get_scale_similarity(p1, p2):
        size1 = p1.max(axis=0) - p1.min(axis=0)
        size2 = p2.max(axis=0) - p2.min(axis=0)
        
        width_diff = abs(size1[0] - size2[0])
        height_diff = abs(size1[1] - size2[1])
        
        width_sim = max(0, 1 - width_diff / 500)
        height_sim = max(0, 1 - height_diff / 500)
        
        return ((width_sim + height_sim) / 2) * 100

    scale_sim = get_scale_similarity(points1, points2)

    # FACTOR 3: Position similarity
    def get_position_similarity(p1, p2):
        center1 = p1.mean(axis=0)
        center2 = p2.mean(axis=0)
        distance = euclidean(center1, center2)
        return max(0, 1 - distance / MAX_DISTANCE_THRESHOLD_PX) * 100

    position_sim = get_position_similarity(points1, points2)
    
    # Weighted combination
    weights = {"shape": 0.60, "scale": 0.25, "position": 0.15}
    
    final_sim = (
        shape_sim * weights["shape"] +
        scale_sim * weights["scale"] +
        position_sim * weights["position"]
    )
    
    # Increase contrast for high similarities
    if final_sim > 80:
        final_sim = 80 + (final_sim - 80) * 1.5

    return max(0, min(100, final_sim))


def find_result_file_by_session_id(session_id: str) -> Optional[str]:
    """IMPROVED: Find result file path by session ID using metadata cache."""
    if not validate_session_id(session_id):
        logger.warning(f"Invalid session ID format: {session_id}")
        return None
    
    try:
        # Try metadata cache first
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT filename FROM result_metadata WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if row and row['filename']:
                filepath = os.path.join(RESULTS_DIR, row['filename'])
                return filepath if os.path.exists(filepath) else None
    except (sqlite3.Error, OSError) as e:
        logger.error(f"Ошибка поиска файла для сессии {session_id}: {e}")
    return None


# =============================================================================
# WEBSOCKET NOTIFICATION
# =============================================================================

def notify_clients_of_update():
    """Notify all WebSocket clients of data update."""
    dead_clients = set()
    for client in clients:
        try:
            client.send(json.dumps({"type": "update_needed"}))
        except Exception:
            dead_clients.add(client)
    
    clients.difference_update(dead_clients)


# =============================================================================
# STATIC ROUTES
# =============================================================================

@app.route('/')
def index():
    """Отдает главную страницу с тестом."""
    return render_template('index.html')


@app.route('/results')
def show_results_page():
    """Отдает HTML-страницу для отображения результатов."""
    return render_template('display_results.html')


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


@app.route('/index-start')
def show_index_start_page():
    """Отдает стартовую главную страницу."""
    return render_template('index-start.html')


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')


# =============================================================================
# API ROUTES - TEST RESULTS & DATA (NEW IMPLEMENTATION)
# =============================================================================

@app.route('/api/save_results', methods=['POST'])
@limiter.limit("10 per minute")
@csrf.exempt
def save_results():
    """
    Accepts results, saves to JSON, and atomically updates all related
    tables in the normalized database.
    """
    try:
        data = request.get_json()
        
        # --- ИЗМЕНЕНИЕ ЗДЕСЬ: Добавлена проверка на persistentId и fingerprint ---
        is_valid, error_msg = validate_json_data(
            data,
            ['userInfo', 'testResults', 'sessionId', 'persistentId', 'fingerprint']
        )
        if not is_valid:
            logger.warning(f"Invalid data from {request.remote_addr}: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        # --- ИЗМЕНЕНИЕ ЗДЕСЬ: Добавлена проверка на вложенные ключи ---
        persistent_id = data.get('persistentId', {}).get('cookie')
        fp_hash = data.get('fingerprint', {}).get('privacySafeHash')

        if not persistent_id or not fp_hash:
            logger.warning(f"Missing persistentId or fingerprint from {request.remote_addr}")
            return jsonify({"status": "error", "message": "Missing persistentId cookie or fingerprint hash"}), 400

        data['serverReceiveTimestamp'] = datetime.now().isoformat()
        data['clientIp'] = request.remote_addr

        # Generate certificate number on passing score
        score = data['testResults'].get('percentage', 0)
        official_doc_num = None
        if score >= PASSING_SCORE_THRESHOLD:
            official_doc_num = get_next_document_number()
            data['officialDocumentNumber'] = official_doc_num
        
        # Save JSON file
        last_name = sanitize_filename(data['userInfo'].get('lastName', 'Unknown'))
        first_name = sanitize_filename(data['userInfo'].get('firstName', 'User'))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        doc_part = f"_{official_doc_num.replace('/', '-')}" if official_doc_num else ""
        filename = f"result_{last_name}_{first_name}{doc_part}_{timestamp}.json"
        
        with open(os.path.join(RESULTS_DIR, filename), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Atomically update DB in a single transaction
        with get_db() as conn:
            cursor = conn.cursor()
            
            # 1. User (find or create)
            user_info = data.get('userInfo', {})
            # persistent_id уже получен выше
            cursor.execute("SELECT user_id FROM users WHERE persistent_id = ?", (persistent_id,))
            user_row = cursor.fetchone()
            if user_row:
                user_id = user_row['user_id']
            else:
                res = cursor.execute(
                    "INSERT INTO users (lastname, firstname, middlename, position, persistent_id) VALUES (?, ?, ?, ?, ?)",
                    (user_info.get('lastName'), user_info.get('firstName'), user_info.get('middleName'), user_info.get('position'), persistent_id)
                )
                user_id = res.lastrowid

            # 2. Fingerprint (insert or update last seen date)
            # fp_hash уже получен выше
            if fp_hash:
                fp_data = data.get('fingerprint', {}).get('privacySafe', {})
                cursor.execute("""
                    INSERT INTO fingerprints (fingerprint_hash, user_agent, platform, webgl_renderer, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fingerprint_hash) DO UPDATE SET last_seen = excluded.last_seen;
                """, (
                    fp_hash, fp_data.get('userAgent'), fp_data.get('platform'), 
                    fp_data.get('webGLRenderer'), data['sessionMetrics']['startTime'], data['sessionMetrics']['startTime']
                ))

            # 3. Result Metadata
            cursor.execute(
                "INSERT OR REPLACE INTO result_metadata (session_id, user_id, fingerprint_hash, test_type, score, start_time, end_time, filename) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (data['sessionId'], user_id, fp_hash, data.get('testType'), score, data['sessionMetrics']['startTime'], data['sessionMetrics']['endTime'], filename)
            )
            
            # 4. Certificate (if applicable)
            if official_doc_num:
                full_name = f"{user_info.get('lastName', '')} {user_info.get('firstName', '')} {user_info.get('middleName', '')}".strip()
                cursor.execute(
                    "INSERT INTO certificates (document_number, user_fullname, user_position, test_type, issue_date, score_percentage, session_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (official_doc_num, full_name, user_info.get('position'), data.get('testType'), datetime.now().isoformat(), score, data['sessionId'])
                )
            
            conn.commit()

        logger.info(f"Results saved: {filename}, session: {data['sessionId']}, score: {score}%")
        
        # Invalidate cache
        cache.delete_memoized(get_results_api)
        cache.delete_memoized(get_certificates)
        
        notify_clients_of_update()
        
        response = {"status": "success", "message": "Results saved", "filename": filename}
        if official_doc_num:
            response["officialDocumentNumber"] = official_doc_num
        return jsonify(response), 201
        
    except json.JSONDecodeError:
        logger.warning(f"Invalid JSON from {request.remote_addr}")
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400
    except OSError as e:
        logger.error(f"File system error: {e}")
        return jsonify({"status": "error", "message": "File system error"}), 500
    except Exception as e:
        logger.error(f"Unexpected error in save_results: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500


@app.route('/api/get_results', methods=['GET'])
# ИЗМЕНЕНИЕ 1: Используем @cache.cached с query_string=True
@cache.cached(timeout=60, query_string=True)
def get_results_api():
    """Returns paginated results from the DB cache for fast list rendering."""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        if not (1 <= page <= 1000 and 1 <= per_page <= MAX_RESULTS_PER_PAGE):
            return jsonify({"status": "error", "message": "Invalid pagination parameters"}), 400
        
        offset = (page - 1) * per_page
        results = []
        
        with get_db() as conn:
            cursor = conn.cursor()
            total = cursor.execute("SELECT COUNT(*) FROM result_metadata").fetchone()[0]
            
            # CHANGE 1: The query now also fetches the 'details' of the first event
            query = """
                SELECT 
                    rm.session_id, rm.test_type, rm.score, rm.start_time, rm.end_time,
                    u.lastname, u.firstname,
                    (SELECT pe.client_ip FROM proctoring_events pe WHERE pe.session_id = rm.session_id ORDER BY pe.event_timestamp ASC LIMIT 1) as client_ip,
                    (SELECT pe.details FROM proctoring_events pe WHERE pe.session_id = rm.session_id ORDER BY pe.event_timestamp ASC LIMIT 1) as first_event_details
                FROM result_metadata rm
                LEFT JOIN users u ON rm.user_id = u.user_id
                ORDER BY rm.start_time DESC
                LIMIT ? OFFSET ?
            """
            cursor.execute(query, (per_page, offset))
            
            for row in cursor.fetchall():
                # CHANGE 2: Fallback logic to get the IP from 'details' for old records
                client_ip = row['client_ip']
                if not client_ip or client_ip == "N/A":
                    try:
                        # Parse the details JSON of the first event
                        details = json.loads(row['first_event_details'] or '{}')
                        # Get the IP from inside the JSON
                        client_ip = details.get('ip', "N/A")
                    except (json.JSONDecodeError, AttributeError):
                        client_ip = "N/A"

                score = row['score'] if row['score'] is not None else 0

                # --- ИЗМЕНЕНИЕ ЗДЕСЬ: Добавляем полную русскую шкалу оценок ---
                if score >= 90:
                    grade_class = "excellent"
                    grade_text = "Отлично"
                elif score >= 80:
                    grade_class = "good"
                    grade_text = "Хорошо"
                elif score >= 60:
                    grade_class = "satisfactory"
                    grade_text = "Удовлетворительно"
                else:
                    grade_class = "poor"
                    grade_text = "Неудовлетворительно"
                # --- КОНЕЦ ИЗМЕНЕНИЯ ---

                results.append({
                    "sessionId": row['session_id'],
                    "testType": row['test_type'],
                    "clientIp": client_ip,
                    "userInfo": {"lastName": row['lastname'], "firstName": row['firstname']},
                    "testResults": {
                        "percentage": score,
                        "grade": {"class": grade_class, "text": grade_text}
                    },
                    "sessionMetrics": {
                        "startTime": row['start_time'],
                        "endTime": row['end_time'],
                        "totalFocusLoss": 0, "totalBlurTime": 0, "printAttempts": 0
                    }
                })

        logger.info(f"Returned {len(results)} results (page {page}, total {total}) from DB")
        return jsonify({"results": results, "page": page, "per_page": per_page, "total": total})

    except Exception as e:
        logger.error(f"Error in get_results_api: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Error retrieving results"}), 500


@app.route('/api/get_full_result/<session_id>', methods=['GET'])
def get_full_result_data(session_id: str):
    """Finds and returns the full JSON file content for detailed analysis."""
    try:
        if not validate_session_id(session_id):
            return jsonify({"status": "error", "message": "Invalid session ID format"}), 400

        filepath = find_result_file_by_session_id(session_id)
        if not filepath:
            return jsonify({"status": "error", "message": "Result not found"}), 404
        
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return jsonify(data), 200
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Could not read file for session {session_id}: {e}")
        return jsonify({"status": "error", "message": "Error reading result file"}), 500
    except Exception as e:
        logger.error(f"Error in get_full_result_data: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500

# =============================================================================
# API ROUTES - PROCTORING EVENTS
# =============================================================================

@app.route('/api/log_event', methods=['POST'])
@limiter.limit("60 per minute")
@csrf.exempt
def log_event():
    """
    Log a single proctoring event to database.
    IMPROVED: Better input validation.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data"}), 400
        
        user_ip = request.remote_addr
        
        is_valid, error_msg = validate_json_data(data, ['sessionId', 'eventType'])
        if not is_valid:
            logger.warning(f"Invalid event from {user_ip}: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400
        
        session_id = data['sessionId']
        event_type = data['eventType']
        
        # Validate inputs
        if not validate_session_id(session_id):
            return jsonify({"status": "error", "message": "Invalid session ID"}), 400
        
        if not validate_event_type(event_type):
            return jsonify({"status": "error", "message": "Invalid event type"}), 400
        
        event_timestamp = data.get('eventTimestamp', datetime.now().isoformat())
        details = data.get('details', {})
        details['ip'] = user_ip

        # Extract denormalized fields for faster querying
        persistent_id = details.get('persistentId')
        page = details.get('page')

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO proctoring_events 
                (session_id, event_type, event_timestamp, details, persistent_id, client_ip, page)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                event_type,
                event_timestamp,
                json.dumps(details),
                persistent_id,
                user_ip,
                page
            ))
            conn.commit()
        
        logger.debug(f"Event {event_type} logged for session {session_id}")
        return jsonify({"status": "success"}), 200
        
    except json.JSONDecodeError:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400
    except sqlite3.Error as e:
        logger.error(f"Database error in log_event: {e}")
        return jsonify({"status": "error", "message": "Database error"}), 500
    except Exception as e:
        logger.error(f"Unexpected error in log_event: {e}")
        return jsonify({"status": "error", "message": "Internal error"}), 500


@app.route('/api/get_events/<session_id>', methods=['GET'])
def get_events(session_id: str):
    """
    Return all proctoring events for a session.
    IMPROVED: Input validation.
    """
    try:
        if not validate_session_id(session_id):
            return jsonify({"status": "error", "message": "Invalid session ID"}), 400
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM proctoring_events
                WHERE session_id = ?
                ORDER BY event_timestamp ASC
            """, (session_id,))
            events = [dict(row) for row in cursor.fetchall()]
        
        logger.info(f"Returned {len(events)} events for session {session_id}")
        return jsonify(events), 200
        
    except sqlite3.Error as e:
        logger.error(f"Database error in get_events: {e}")
        return jsonify({"status": "error", "message": "Database error"}), 500
    except Exception as e:
        logger.error(f"Unexpected error in get_events: {e}")
        return jsonify({"status": "error", "message": "Internal error"}), 500


# =============================================================================
# API ROUTES - CERTIFICATES
# =============================================================================

@app.route('/api/get_certificates', methods=['GET'])
@cache.memoize(timeout=360)
def get_certificates():
    """Return registry of all issued certificates."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM certificates ORDER BY issue_date DESC")
            certificates = [dict(row) for row in cursor.fetchall()]
        
        logger.info(f"Returned {len(certificates)} certificates")
        return jsonify(certificates), 200
        
    except sqlite3.Error as e:
        logger.error(f"Database error in get_certificates: {e}")
        return jsonify({"status": "error", "message": "Database error"}), 500
    except Exception as e:
        logger.error(f"Unexpected error in get_certificates: {e}")
        return jsonify({"status": "error", "message": "Internal error"}), 500


# =============================================================================
# ANALYTICS FUNCTIONS
# =============================================================================

def get_completed_session_ids() -> set:
    """Get IDs of all successfully completed sessions."""
    completed_ids = set()
    try:
        for filename in os.listdir(RESULTS_DIR):
            if not filename.endswith('.json'):
                continue
            
            filepath = os.path.join(RESULTS_DIR, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    sid = data.get('sessionId')
                    if sid and isinstance(sid, str):
                        completed_ids.add(sid)
            except (json.JSONDecodeError, OSError):
                continue
    except OSError as e:
        logger.error(f"Error reading results directory: {e}")
    
    return completed_ids

def get_session_user_info(session_id: str) -> Tuple[Dict[str, Any], str, str]:
    """
    Get user information and session type from proctoring events.
    Returns: (user_info, client_ip, session_type)
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT details, event_type, client_ip
                FROM proctoring_events
                WHERE session_id = ?
                  AND (event_type = 'test_started' OR event_type = 'study_started')
                LIMIT 1
            """, (session_id,))
            row = cursor.fetchone()
        
        user_info = {}
        client_ip = "N/A"
        session_type = "unknown"
        
        if row:
            client_ip = row['client_ip'] or "N/A"
            
            try:
                details = json.loads(row['details'])
                
                # --- THIS IS THE CRUCIAL FIX ---
                # If the IP from the dedicated column is missing, fall back to the details JSON
                if client_ip == "N/A" and 'ip' in details:
                    client_ip = details['ip']
                # --- End of fix ---

                if row['event_type'] == 'test_started':
                    session_type = "test"
                    user_info = details.get('userInfo', {"lastName": "N/A"})
                elif row['event_type'] == 'study_started':
                    session_type = "study"
                    temp_info = details.get('userInfo')
                    if temp_info and temp_info.get('lastName'):
                        user_info = temp_info
                    else:
                        pid = details.get('persistentId')
                        user_info = {
                            "lastName": "Study Session",
                            "firstName": f"ID: {pid[:8]}..." if pid and pid != 'N/A' else 'N/A'
                        }
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Error parsing session {session_id} data: {e}")
                user_info = {"lastName": "Data Error"}
        
        return user_info, client_ip, session_type
        
    except sqlite3.Error as e:
        logger.error(f"Database error in get_session_user_info: {e}")
        return {"lastName": "DB Error"}, "N/A", "unknown"


def find_related_study_session(test_start_time: str, test_persistent_id: str,
                              test_ip: str, required_study_page: str) -> Optional[str]:
    """
    Find related study session for a test session.
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Search by persistent ID first
            if test_persistent_id:
                cursor.execute("""
                    SELECT session_id
                    FROM proctoring_events
                    WHERE event_type = 'study_started'
                      AND event_timestamp < ?
                      AND persistent_id = ?
                      AND page = ?
                    GROUP BY session_id
                    ORDER BY MIN(event_timestamp) DESC
                    LIMIT 1
                """, (test_start_time, test_persistent_id, required_study_page))
                result = cursor.fetchone()
                if result:
                    return result['session_id']
            
            # Fallback: search by IP within time window
            if test_ip:
                # Calculate time boundary in Python for safety
                test_time = datetime.fromisoformat(test_start_time.replace('Z', ''))
                min_time = test_time - timedelta(hours=MAX_STUDY_SESSION_LOOKUP_HOURS)
                
                cursor.execute("""
                    SELECT session_id
                    FROM proctoring_events
                    WHERE event_type = 'study_started'
                      AND event_timestamp < ?
                      AND event_timestamp > ?
                      AND client_ip = ?
                      AND page = ?
                    GROUP BY session_id
                    ORDER BY MIN(event_timestamp) DESC
                    LIMIT 1
                """, (test_start_time, min_time.isoformat(), test_ip, required_study_page))
                result = cursor.fetchone()
                if result:
                    return result['session_id']
            
            return None
            
    except (sqlite3.Error, ValueError) as e:
        logger.error(f"Error in find_related_study_session: {e}")
        return None


def calculate_engagement_score(study_session_id: str) -> Tuple[int, int]:
    """
    Calculate user engagement index for a study session.
    Returns: (engagement_score, study_duration_seconds)
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            engagement = 0
            duration = 0
            
            # Calculate session duration
            cursor.execute("""
                SELECT MIN(event_timestamp), MAX(event_timestamp)
                FROM proctoring_events
                WHERE session_id = ?
            """, (study_session_id,))
            times = cursor.fetchone()
            
            if times and times[0] and times[1]:
                try:
                    start = datetime.fromisoformat(times[0].replace('Z', ''))
                    end = datetime.fromisoformat(times[1].replace('Z', ''))
                    duration = int((end - start).total_seconds())
                except ValueError as e:
                    logger.warning(f"Time parse error for session {study_session_id}: {e}")
            
            # Points for module view time
            cursor.execute("""
                SELECT details FROM proctoring_events
                WHERE session_id = ? AND event_type = 'module_view_time'
            """, (study_session_id,))
            
            total_view_time = 0
            for row in cursor.fetchall():
                try:
                    details = json.loads(row['details'])
                    total_view_time += details.get('duration', 0)
                except (json.JSONDecodeError, KeyError):
                    continue
            
            engagement += int(total_view_time / 60)  # 1 point per minute
            
            # Points for scroll depth
            cursor.execute("""
                SELECT details FROM proctoring_events
                WHERE session_id = ? AND event_type = 'scroll_depth_milestone'
            """, (study_session_id,))
            
            max_depth = 0
            for row in cursor.fetchall():
                try:
                    details = json.loads(row['details'])
                    depth_str = details.get('depth', '0%').replace('%', '')
                    max_depth = max(max_depth, int(depth_str))
                except (json.JSONDecodeError, ValueError, KeyError):
                    continue
            
            if max_depth >= 95:
                engagement += 10
            elif max_depth >= 50:
                engagement += 5
            
            # Points for self-check answers
            cursor.execute("""
                SELECT COUNT(*) FROM proctoring_events
                WHERE session_id = ? AND event_type = 'self_check_answered'
            """, (study_session_id,))
            self_check_count = cursor.fetchone()[0]
            engagement += self_check_count * 2
            
            return engagement, duration
            
    except sqlite3.Error as e:
        logger.error(f"Error in calculate_engagement_score: {e}")
        return 0, 0


# =============================================================================
# API ROUTES - ANALYTICS
# =============================================================================

@app.route('/api/get_abandoned_sessions', methods=['GET'])
@cache.memoize(timeout=60)
def get_abandoned_sessions():
    """
    Find sessions that were started but not completed successfully.
    (Optimized version)
    """
    try:
        # Step 1: Get all completed session IDs from the fast file-based check.
        completed_ids = get_completed_session_ids()
        
        # Step 2: Use SQL to find all unique session IDs from the events table.
        # This is much faster than fetching all event data.
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT session_id FROM proctoring_events")
            all_event_sids = {row['session_id'] for row in cursor.fetchall()}

        # Step 3: Find the difference to identify abandoned sessions.
        abandoned_sids = list(all_event_sids - completed_ids)
        
        if not abandoned_sids:
            return jsonify([]), 200

        # Step 4: Fetch details ONLY for the abandoned sessions.
        # Using a parameterized query avoids SQL injection and is efficient.
        placeholders = ','.join('?' for _ in abandoned_sids)
        query = f"""
            SELECT
                session_id,
                MIN(event_timestamp) as start_time,
                SUM(CASE WHEN event_type = 'focus_loss' THEN 1 ELSE 0 END) as focus_loss_count,
                SUM(CASE WHEN event_type = 'screenshot_attempt' THEN 1 ELSE 0 END) as screenshot_count,
                SUM(CASE WHEN event_type = 'print_attempt' THEN 1 ELSE 0 END) as print_count
            FROM proctoring_events
            WHERE session_id IN ({placeholders})
            GROUP BY session_id
        """
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(query, abandoned_sids)
            abandoned_sessions_details = cursor.fetchall()

        # Step 5: Enrich with user info and format the final response.
        abandoned_results = []
        for session in abandoned_sessions_details:
            sid = session['session_id']
            user_info, client_ip, session_type = get_session_user_info(sid)
            abandoned_results.append({
                "sessionId": sid,
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
        
        abandoned_results.sort(key=lambda x: x['startTime'], reverse=True)
        
        logger.info(f"Found {len(abandoned_results)} abandoned sessions (Optimized)")
        return jsonify(abandoned_results), 200

    except Exception as e:
        logger.error(f"Error in get_abandoned_sessions: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": "Error analyzing sessions"}), 500


@app.route('/api/get_behavior_analysis', methods=['GET'])
def get_behavior_analysis():
    """
    Perform behavioral analysis to detect suspicious test-taking patterns.
    """
    try:
        # Load all completed tests (not paginated for analysis)
        all_tests, _ = load_completed_tests(page=1, per_page=10000)
        suspicious = []

        for test in all_tests:
            test_start = test.get('sessionMetrics', {}).get('startTime')
            test_pid = test.get('persistentId', {}).get('cookie')
            test_ip = test.get('clientIp')
            test_type = test.get('testType', 'default')
            
            thresholds = BEHAVIOR_THRESHOLDS.get(test_type, BEHAVIOR_THRESHOLDS['default'])
            required_page = TEST_TO_STUDY_PAGE_MAP.get(test_type)
            
            if not all([test_start, required_page, (test_pid or test_ip)]):
                continue
            
            study_sid = find_related_study_session(test_start, test_pid, test_ip, required_page)
            
            engagement = 0
            study_duration = 0
            
            if study_sid:
                engagement, study_duration = calculate_engagement_score(study_sid)
            
            try:
                test_duration = (
                    datetime.fromisoformat(test['sessionMetrics']['endTime'].replace('Z', '')) -
                    datetime.fromisoformat(test['sessionMetrics']['startTime'].replace('Z', ''))
                ).total_seconds()
                test_score = test['testResults']['percentage']
            except (KeyError, ValueError) as e:
                logger.warning(f"Error processing test metrics: {e}")
                continue
            
            if (test_score >= thresholds['min_score'] and
                test_duration < thresholds['max_test_duration_sec'] and
                engagement < thresholds['min_engagement_score']):
                
                reason = (
                    f"Высокий балл ({test_score}%) при быстром прохождении "
                    f"({int(test_duration)}с) и низкой вовлеченности в обучение "
                    f"(Оценка: {engagement})."
                )
                
                suspicious.append({
                    "userInfo": test.get('userInfo'),
                    "testResult": {"score": test_score, "duration": int(test_duration)},
                    "studyInfo": {"duration": study_duration, "engagementScore": engagement},
                    "reason": reason,
                    "sessionId": test.get('sessionId')
                })

        logger.info(f"Found {len(suspicious)} suspicious sessions")
        return jsonify(suspicious), 200

    except Exception as e:
        logger.error(f"Error in get_behavior_analysis: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": "Error in analysis"}), 500


@app.route('/api/analyze_mouse_from_files', methods=['POST'])
@limiter.limit("5 per minute")
def analyze_mouse_from_files():
    """
    Analyze mouse trajectory similarity using DTW.
    Accepts list of session IDs, finds corresponding files, performs analysis.
    """
    try:
        data = request.get_json()
        session_ids = data.get('session_ids')

        if not session_ids or len(session_ids) < 2:
            return jsonify({"error": "Need at least 2 sessions for comparison"}), 400

        # Extract trajectories from files
        trajectories = {}
        for sid in session_ids:
            filepath = find_result_file_by_session_id(sid)
            if not filepath:
                logger.warning(f"File not found for session {sid}")
                continue

            with open(filepath, 'r', encoding='utf-8') as f:
                result_data = json.load(f)
            
            per_question = result_data.get('behavioralMetrics', {}).get('perQuestion', [])
            trajectories[sid] = {}
            for i, q_data in enumerate(per_question):
                movements = q_data.get('mouseMovements')
                if movements:
                    trajectories[sid][i] = movements
        
        # Compare trajectories pairwise
        results = {}
        sid_list = list(session_ids)
        
        for i in range(len(sid_list)):
            for j in range(i + 1, len(sid_list)):
                s1, s2 = sid_list[i], sid_list[j]
                pair_key = f"{s1}_vs_{s2}"
                results[pair_key] = {}
                
                common_qs = set(trajectories.get(s1, {}).keys()) & set(trajectories.get(s2, {}).keys())
                
                for q_idx in common_qs:
                    t1 = trajectories[s1][q_idx]
                    t2 = trajectories[s2][q_idx]
                    
                    similarity = compare_mouse_trajectories(t1, t2)
                    results[pair_key][q_idx] = round(similarity, 1)

        return jsonify(results), 200

    except Exception as e:
        logger.error(f"Error in analyze_mouse_from_files: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"status": "error", "message": "Internal server error"}), 500


# =============================================================================
# WEBSOCKET
# =============================================================================

@sock.route('/ws')
def ws_handler(ws):
    """Handle WebSocket connections for real-time updates."""
    clients.add(ws)
    logger.info(f"WebSocket client connected. Total: {len(clients)}")
    
    try:
        while True:
            data = ws.receive(timeout=60)
            if data is None:
                break
    except Exception:
        pass
    finally:
        clients.discard(ws)
        logger.info(f"WebSocket client disconnected. Total: {len(clients)}")


# =============================================================================
# CSRF TOKEN INJECTION
# =============================================================================

@app.after_request
def inject_csrf_token(response):
    """
    Inject CSRF token into cookie for JavaScript access.
    IMPROVED: More secure cookie settings.
    """
    if request.endpoint not in ['static', None]:
        response.set_cookie(
            'csrf_token',
            generate_csrf(),
            secure=True,  # Always use secure in production
            httponly=False,
            samesite='Strict',
            max_age=3600  # NEW: 1 hour expiration
        )
    return response


# =============================================================================
# ERROR HANDLERS
# =============================================================================

@app.errorhandler(413)
def request_entity_too_large(error):
    """Handle file upload size exceeded."""
    return jsonify({"status": "error", "message": "File too large"}), 413


@app.errorhandler(429)
def ratelimit_handler(e):
    """Handle rate limit exceeded."""
    return jsonify({"status": "error", "message": "Rate limit exceeded"}), 429


@app.errorhandler(500)
def internal_error(error):
    """Handle internal server errors."""
    logger.error(f"Internal error: {error}")
    return jsonify({"status": "error", "message": "Internal server error"}), 500


# =============================================================================
# APPLICATION STARTUP
# =============================================================================

if __name__ == '__main__':
    try:
        init_db()
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        exit(1)
    
    if os.path.exists(SSL_CERT_PATH) and os.path.exists(SSL_KEY_PATH):
        logger.info("Starting in HTTPS mode")
        ssl_context = (SSL_CERT_PATH, SSL_KEY_PATH)
        app.run(host='0.0.0.0', port=5000, debug=False, ssl_context=ssl_context)
    else:
        logger.warning("SSL certificates not found. Starting in HTTP mode")
        app.run(host='0.0.0.0', port=5000, debug=False)