import os
import json
import sqlite3
import logging
from typing import Dict, Any, Optional, List, Tuple

# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, 'results_data')
DATABASE_PATH = os.path.join(BASE_DIR, 'app_data.db')
TARGET_DB_VERSION = 3  # ИЗМЕНЕНО: Целевая версия схемы теперь 3
BATCH_SIZE = 100

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s'
)

# =============================================================================
# ОПРЕДЕЛЕНИЕ ЦЕЛЕВОЙ СХЕМЫ БАЗЫ ДАННЫХ (V3)
# =============================================================================

def get_target_schema() -> Dict[str, str]:
    """
    Возвращает словарь с CREATE-запросами для новой схемы V3.
    Схема полностью синхронизирована с последней версией app.py.
    """
    return {
        'users': '''
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                lastname TEXT, firstname TEXT, middlename TEXT, position TEXT,
                persistent_id TEXT UNIQUE NOT NULL
            )
        ''',
        'fingerprints': '''
            CREATE TABLE fingerprints (
                fingerprint_hash TEXT PRIMARY KEY,
                user_agent TEXT, platform TEXT, webgl_renderer TEXT,
                first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
            )
        ''',
        'result_metadata': '''
            CREATE TABLE result_metadata (
                session_id TEXT PRIMARY KEY, user_id INTEGER, fingerprint_hash TEXT,
                test_type TEXT, score INTEGER, start_time TEXT, end_time TEXT, -- ИЗМЕНЕНО: score REAL -> INTEGER
                filename TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, -- ДОБАВЛЕНО: created_at
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE SET NULL,
                FOREIGN KEY(fingerprint_hash) REFERENCES fingerprints(fingerprint_hash) ON DELETE SET NULL
            )
        ''',
        'document_counters': '''
            CREATE TABLE document_counters (
                period TEXT PRIMARY KEY,
                last_sequence_number INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP -- ДОБАВЛЕНО: updated_at
            )
        ''',
        'proctoring_events': '''
            CREATE TABLE proctoring_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, event_type TEXT NOT NULL, event_timestamp TEXT NOT NULL,
                details TEXT, persistent_id TEXT, client_ip TEXT, page TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP -- ДОБАВЛЕНО: created_at
            )
        ''',
        'certificates': '''
            CREATE TABLE certificates (
                document_number TEXT PRIMARY KEY, user_fullname TEXT NOT NULL,
                user_position TEXT, test_type TEXT NOT NULL, issue_date TEXT NOT NULL,
                score_percentage INTEGER NOT NULL, session_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP -- ДОБАВЛЕНО: created_at
            )
        '''
    }

def get_target_indexes() -> List[str]:
    """ДОБАВЛЕНО: Возвращает список CREATE-запросов для индексов."""
    return [
        'CREATE INDEX IF NOT EXISTS idx_events_session ON proctoring_events(session_id)',
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_users_persistent_id ON users(persistent_id)',
        'CREATE INDEX IF NOT EXISTS idx_metadata_user_id ON result_metadata(user_id)',
    ]

# =============================================================================
# КЛАСС МИГРАЦИИ (остальная часть класса без изменений, кроме run)
# =============================================================================

class DatabaseMigrator:
    def __init__(self, db_path: str, results_dir: str):
        self.db_path = db_path
        self.results_dir = results_dir
        self.conn: Optional[sqlite3.Connection] = None
        self.user_cache: Dict[str, int] = {}

    def _connect(self):
        # ... код без изменений
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA foreign_keys = ON;")
            logging.info(f"Успешное подключение к базе данных: {self.db_path}")
        except sqlite3.Error as e:
            logging.error(f"Ошибка подключения к базе данных: {e}")
            raise

    def _close(self):
        # ... код без изменений
        if self.conn:
            self.conn.close()
            logging.info("Соединение с базой данных закрыто.")

    def run(self):
        """Запускает полный процесс миграции."""
        self._connect()
        if not self.conn: return

        try:
            cursor = self.conn.cursor()
            current_version = cursor.execute("PRAGMA user_version").fetchone()[0]
            if current_version >= TARGET_DB_VERSION:
                logging.info(f"Миграция не требуется. Текущая версия схемы ({current_version}) уже соответствует V{TARGET_DB_VERSION}.")
                return

            logging.info(f"Начало миграции со схемы v{current_version} на v{TARGET_DB_VERSION}...")
            
            cursor.execute("BEGIN;")
            self._backup_and_recreate_schema(cursor)
            self._migrate_from_json_files(cursor)
            self._migrate_from_old_tables(cursor)
            
            # ДОБАВЛЕНО: Создание индексов для ускорения работы
            logging.info("Создание индексов...")
            for index_sql in get_target_indexes():
                cursor.execute(index_sql)
            logging.info("Индексы успешно созданы.")

            cursor.execute(f"PRAGMA user_version = {TARGET_DB_VERSION};")
            
            self.conn.commit()
            logging.info("Транзакция успешно зафиксирована. Миграция на V%s завершена.", TARGET_DB_VERSION)
            self._print_report()

        except Exception as e:
            # ... код без изменений
            logging.error(f"Произошла ошибка во время миграции: {e}", exc_info=True)
            if self.conn:
                self.conn.rollback()
                logging.warning("Все изменения отменены из-за ошибки.")
        finally:
            self._close()

    def _backup_and_recreate_schema(self, cursor: sqlite3.Cursor):
        # ... код без изменений
        logging.info("Начало резервного копирования и пересоздания схемы...")
        schema = get_target_schema()
        
        for table_name in schema.keys():
            try:
                cursor.execute(f"ALTER TABLE {table_name} RENAME TO {table_name}_old")
                logging.info(f"Таблица '{table_name}' переименована в '{table_name}_old'.")
            except sqlite3.OperationalError as e:
                if "no such table" in str(e):
                    logging.warning(f"Таблица '{table_name}' не найдена для переименования, будет создана новая.")
                else: raise
        
        logging.info("Создание новых таблиц...")
        for create_sql in schema.values():
            cursor.execute(create_sql)
        logging.info("Новые таблицы успешно созданы.")

    def _get_or_create_user_id(self, cursor: sqlite3.Cursor, user_info: Dict[str, Any], persistent_id: Optional[str]) -> Optional[int]:
        # ИЗМЕНЕНИЕ: Добавлена проверка на NOT NULL для persistent_id
        if not persistent_id:
            # Если нет persistent_id, мы не можем надежно идентифицировать пользователя.
            # Можно создать "анонимного" пользователя, но лучше пропустить для целостности.
            return None

        if persistent_id in self.user_cache:
            return self.user_cache[persistent_id]

        cursor.execute("SELECT user_id FROM users WHERE persistent_id = ?", (persistent_id,))
        row = cursor.fetchone()
        if row:
            self.user_cache[persistent_id] = row['user_id']
            return row['user_id']

        try:
            insert_cursor = cursor.execute(
                "INSERT INTO users (lastname, firstname, middlename, position, persistent_id) VALUES (?, ?, ?, ?, ?)",
                (user_info.get('lastName'), user_info.get('firstName'), user_info.get('middleName'), user_info.get('position'), persistent_id)
            )
            user_id = insert_cursor.lastrowid
            if user_id: self.user_cache[persistent_id] = user_id
            return user_id
        except sqlite3.IntegrityError:
            cursor.execute("SELECT user_id FROM users WHERE persistent_id = ?", (persistent_id,))
            row = cursor.fetchone()
            return row['user_id'] if row else None

    def _migrate_from_json_files(self, cursor: sqlite3.Cursor):
        # ... код без изменений
        json_files = [f for f in os.listdir(self.results_dir) if f.endswith('.json')]
        logging.info(f"Найдено {len(json_files)} JSON-файлов для миграции в таблицы users, fingerprints, result_metadata.")
        
        success_count = 0
        metadata_batch: List[Tuple] = []
        fingerprints_to_update: Dict[str, Tuple] = {}

        for filename in json_files:
            filepath = os.path.join(self.results_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f: data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logging.warning(f"Не удалось обработать файл {filename}: {e}")
                continue

            session_id = data.get('sessionId')
            if not session_id:
                logging.warning(f"Пропуск файла {filename}: отсутствует 'sessionId'.")
                continue

            user_info = data.get('userInfo', {})
            persistent_id = data.get('persistentId', {}).get('cookie')
            user_id = self._get_or_create_user_id(cursor, user_info, persistent_id)
            
            fp_hash = data.get('fingerprint', {}).get('privacySafeHash')
            start_time = data.get('sessionMetrics', {}).get('startTime')
            
            if fp_hash and start_time:
                fp_data = data.get('fingerprint', {}).get('privacySafe', {})
                if fp_hash not in fingerprints_to_update or start_time > fingerprints_to_update[fp_hash][-1]:
                    fingerprints_to_update[fp_hash] = (fp_hash, fp_data.get('userAgent'), fp_data.get('platform'), fp_data.get('webGLRenderer'), start_time, start_time)
            
            metadata_batch.append((
                session_id, user_id, fp_hash,
                data.get('testType'), data.get('testResults', {}).get('percentage'),
                start_time, data.get('sessionMetrics', {}).get('endTime'),
                filename
            ))
            success_count += 1
            
            if len(metadata_batch) >= BATCH_SIZE:
                self._execute_batches(cursor, metadata_batch, list(fingerprints_to_update.values()))
                metadata_batch.clear(); fingerprints_to_update.clear()

        if metadata_batch: self._execute_batches(cursor, metadata_batch, list(fingerprints_to_update.values()))
        logging.info(f"Обработка JSON-файлов завершена. Успешно: {success_count}, Ошибки: {len(json_files) - success_count}")

    def _execute_batches(self, cursor: sqlite3.Cursor, metadata: List[Tuple], fingerprints: List[Tuple]):
        # ... код без изменений
        if fingerprints:
            cursor.executemany("INSERT OR REPLACE INTO fingerprints (fingerprint_hash, user_agent, platform, webgl_renderer, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)", fingerprints)
        if metadata:
            cursor.executemany("INSERT OR REPLACE INTO result_metadata (session_id, user_id, fingerprint_hash, test_type, score, start_time, end_time, filename) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", metadata)

    def _migrate_from_old_tables(self, cursor: sqlite3.Cursor):
        # ... код без изменений
        logging.info("Начало миграции данных из старых таблиц...")
        tables_to_migrate = ['proctoring_events', 'certificates', 'document_counters']
        
        for table in tables_to_migrate:
            try:
                cursor.execute(f"PRAGMA table_info({table})"); new_cols = {row['name'] for row in cursor.fetchall()}
                cursor.execute(f"PRAGMA table_info({table}_old)"); old_cols = {row['name'] for row in cursor.fetchall()}
                
                common_cols = sorted(list(new_cols.intersection(old_cols)))
                if not common_cols:
                    logging.warning(f"Нет общих колонок между {table} и {table}_old. Пропуск."); continue

                cols_str = ", ".join(common_cols)
                cursor.execute(f"INSERT OR IGNORE INTO {table} ({cols_str}) SELECT {cols_str} FROM {table}_old")
                logging.info(f"Данные из '{table}_old' успешно перенесены в '{table}'.")

            except sqlite3.OperationalError as e:
                if "no such table" in str(e):
                    logging.warning(f"Старая таблица '{table}_old' не найдена. Миграция для нее пропущена.")
                else: raise

    def cleanup_old_tables(self):
        # ... код без изменений
        self._connect()
        if not self.conn: return
        logging.warning("ВНИМАНИЕ: Сейчас будут удалены резервные копии таблиц (_old). Это действие необратимо.")
        confirm = input("Для подтверждения введите 'DELETE': ")
        
        if confirm != 'DELETE':
            logging.info("Удаление отменено."); self._close(); return

        try:
            cursor = self.conn.cursor()
            schema = get_target_schema()
            for table_name in schema.keys():
                cursor.execute(f"DROP TABLE IF EXISTS {table_name}_old")
                logging.info(f"Таблица '{table_name}_old' удалена.")
            self.conn.commit()
            logging.info("Очистка старых таблиц завершена.")
        except Exception as e:
            logging.error(f"Ошибка при очистке: {e}"); self.conn.rollback()
        finally:
            self._close()

    def _print_report(self):
        # ... код без изменений
        print("\n" + "="*60 + "\n" + " " * 15 + "Отчет о миграции базы данных" + "\n" + "="*60)
        print(f"Миграция на схему V{TARGET_DB_VERSION} успешно завершена.")
        print("✅ Новая структура базы данных создана.")
        print("✅ Данные из JSON-файлов и старых таблиц перенесены.")
        print("✅ Индексы для ускорения запросов созданы.")
        print("\nВАЖНО:")
        print("🔵 Проверьте целостность данных в приложении.")
        print("🔵 Старые таблицы сохранены с суффиксом '_old'.")
        print("🔵 Для их удаления раскомментируйте и запустите cleanup_old_tables().")
        print("="*60)

# =============================================================================
# ТОЧКА ВХОДА
# =============================================================================
if __name__ == '__main__':
    migrator = DatabaseMigrator(db_path=DATABASE_PATH, results_dir=RESULTS_DIR)
    migrator.run()
    
    # После проверки данных, можно запустить очистку:
    migrator.cleanup_old_tables()
