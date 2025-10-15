import os
import json
import sqlite3
import logging
from typing import Dict, Any, Optional, List, Tuple

# =============================================================================
# КОНФИГУРАЦИЯ
# =============================================================================
# Настраиваемые параметры
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, 'results_data')
DATABASE_PATH = os.path.join(BASE_DIR, 'app_data.db')
TARGET_DB_VERSION = 2  # Устанавливаем целевую версию схемы
BATCH_SIZE = 100       # Количество файлов для обработки в одной транзакции

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s'
)

# =============================================================================
# ОПРЕДЕЛЕНИЕ ЦЕЛЕВОЙ СХЕМЫ БАЗЫ ДАННЫХ
# =============================================================================

def get_target_schema() -> Dict[str, str]:
    """
    Возвращает словарь с CREATE-запросами для новой схемы.
    Централизованное определение схемы упрощает её поддержку.
    """
    return {
        'users': '''
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                lastname TEXT,
                firstname TEXT,
                middlename TEXT,
                position TEXT,
                persistent_id TEXT UNIQUE
            )
        ''',
        'fingerprints': '''
            CREATE TABLE fingerprints (
                fingerprint_hash TEXT PRIMARY KEY,
                user_agent TEXT,
                platform TEXT,
                webgl_renderer TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
        ''',
        'result_metadata': '''
            CREATE TABLE result_metadata (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER,
                fingerprint_hash TEXT,
                test_type TEXT,
                score REAL,
                start_time TEXT,
                end_time TEXT,
                filename TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL,
                FOREIGN KEY (fingerprint_hash) REFERENCES fingerprints(fingerprint_hash) ON DELETE SET NULL
            )
        ''',
        'document_counters': '''
            CREATE TABLE document_counters (
                period TEXT PRIMARY KEY,
                last_sequence_number INTEGER NOT NULL
            )
        ''',
        'proctoring_events': '''
            CREATE TABLE proctoring_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_timestamp TEXT NOT NULL,
                details TEXT,
                persistent_id TEXT,
                client_ip TEXT,
                page TEXT
            )
        ''',
        'certificates': '''
            CREATE TABLE certificates (
                document_number TEXT PRIMARY KEY,
                user_fullname TEXT NOT NULL,
                user_position TEXT,
                test_type TEXT NOT NULL,
                issue_date TEXT NOT NULL,
                score_percentage INTEGER NOT NULL,
                session_id TEXT NOT NULL
            )
        '''
    }

# =============================================================================
# КЛАСС МИГРАЦИИ
# =============================================================================

class DatabaseMigrator:
    """
    Инкапсулирует логику миграции базы данных в виде класса.
    Ключевые улучшения:
    1. Идемпотентность: Проверяет версию БД и не запускает миграцию повторно.
    2. Транзакционность: Вся миграция выполняется в одной транзакции.
    3. Производительность: Использует пакетные вставки (executemany).
    4. Надёжность: Улучшенная обработка ошибок и кеширование пользователей.
    """
    def __init__(self, db_path: str, results_dir: str):
        self.db_path = db_path
        self.results_dir = results_dir
        self.conn: Optional[sqlite3.Connection] = None
        self.user_cache: Dict[str, int] = {}  # Кеш для user_id в памяти

    def _connect(self):
        """Устанавливает соединение с базой данных."""
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            # Включение WAL-режима для лучшей производительности при конкурентном доступе
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA foreign_keys = ON;")
            logging.info(f"Успешное подключение к базе данных: {self.db_path}")
        except sqlite3.Error as e:
            logging.error(f"Ошибка подключения к базе данных: {e}")
            raise

    def _close(self):
        """Закрывает соединение с базой данных."""
        if self.conn:
            self.conn.close()
            logging.info("Соединение с базой данных закрыто.")

    def run(self):
        """Запускает полный процесс миграции."""
        self._connect()
        if not self.conn:
            return

        try:
            # 1. Проверка на идемпотентность
            cursor = self.conn.cursor()
            current_version = cursor.execute("PRAGMA user_version").fetchone()[0]
            if current_version >= TARGET_DB_VERSION:
                logging.info(f"Миграция не требуется. Текущая версия схемы ({current_version}) "
                             f"уже соответствует или новее целевой ({TARGET_DB_VERSION}).")
                return

            logging.info(f"Начало миграции со схемы v{current_version} на v{TARGET_DB_VERSION}...")
            
            # 2. Основной процесс миграции в одной транзакции
            cursor.execute("BEGIN;")
            self._backup_and_recreate_schema(cursor)
            self._migrate_from_json_files(cursor)
            self._migrate_from_old_tables(cursor)

            # 3. Обновление версии схемы
            cursor.execute(f"PRAGMA user_version = {TARGET_DB_VERSION};")
            
            self.conn.commit()
            logging.info("Транзакция успешно зафиксирована. Миграция завершена.")
            self._print_report()

        except Exception as e:
            logging.error(f"Произошла ошибка во время миграции: {e}", exc_info=True)
            if self.conn:
                self.conn.rollback()
                logging.warning("Все изменения отменены из-за ошибки.")
        finally:
            self._close()

    def _backup_and_recreate_schema(self, cursor: sqlite3.Cursor):
        """Переименовывает старые таблицы и создает новые по актуальной схеме."""
        logging.info("Начало резервного копирования и пересоздания схемы...")
        schema = get_target_schema()
        
        for table_name in schema.keys():
            try:
                cursor.execute(f"ALTER TABLE {table_name} RENAME TO {table_name}_old")
                logging.info(f"Таблица '{table_name}' переименована в '{table_name}_old'.")
            except sqlite3.OperationalError as e:
                if "no such table" in str(e):
                    logging.warning(f"Таблица '{table_name}' не найдена для переименования, будет создана новая.")
                else:
                    raise
        
        logging.info("Создание новых таблиц...")
        for create_sql in schema.values():
            cursor.execute(create_sql)
        logging.info("Новые таблицы успешно созданы.")

    def _get_or_create_user_id(self, cursor: sqlite3.Cursor, user_info: Dict[str, Any], persistent_id: Optional[str]) -> Optional[int]:
        """Находит или создает пользователя и возвращает его ID. Использует кеш."""
        if not user_info and not persistent_id:
            return None

        # Проверка кеша по persistent_id
        if persistent_id and persistent_id in self.user_cache:
            return self.user_cache[persistent_id]

        # Поиск в БД
        if persistent_id:
            cursor.execute("SELECT user_id FROM users WHERE persistent_id = ?", (persistent_id,))
            row = cursor.fetchone()
            if row:
                self.user_cache[persistent_id] = row['user_id']
                return row['user_id']

        # Создание нового пользователя
        try:
            insert_cursor = cursor.execute(
                """
                INSERT INTO users (lastname, firstname, middlename, position, persistent_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_info.get('lastName'), user_info.get('firstName'),
                    user_info.get('middleName'), user_info.get('position'),
                    persistent_id
                )
            )
            user_id = insert_cursor.lastrowid
            if persistent_id and user_id:
                self.user_cache[persistent_id] = user_id
            return user_id
        except sqlite3.IntegrityError:
            # Случай, когда persistent_id уже есть, но не был найден (маловероятно, но возможно)
            cursor.execute("SELECT user_id FROM users WHERE persistent_id = ?", (persistent_id,))
            row = cursor.fetchone()
            return row['user_id'] if row else None


    def _migrate_from_json_files(self, cursor: sqlite3.Cursor):
        """Обрабатывает JSON-файлы и заполняет новые таблицы пакетными вставками."""
        json_files = [f for f in os.listdir(self.results_dir) if f.endswith('.json')]
        logging.info(f"Найдено {len(json_files)} JSON-файлов для миграции в таблицы users, fingerprints, result_metadata.")
        
        success_count = 0
        metadata_batch: List[Tuple] = []
        fingerprints_to_update: Dict[str, Tuple] = {}

        for filename in json_files:
            filepath = os.path.join(self.results_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
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
                    fingerprints_to_update[fp_hash] = (
                        fp_hash,
                        fp_data.get('userAgent'),
                        fp_data.get('platform'),
                        fp_data.get('webGLRenderer'),
                        start_time,  # first_seen
                        start_time   # last_seen
                    )
            
            metadata_batch.append((
                session_id, user_id, fp_hash,
                data.get('testType'), data.get('testResults', {}).get('percentage'),
                start_time, data.get('sessionMetrics', {}).get('endTime'),
                filename
            ))
            success_count += 1
            
            if len(metadata_batch) >= BATCH_SIZE:
                self._execute_batches(cursor, metadata_batch, list(fingerprints_to_update.values()))
                metadata_batch.clear()
                fingerprints_to_update.clear()

        # Обработка оставшихся данных
        if metadata_batch:
            self._execute_batches(cursor, metadata_batch, list(fingerprints_to_update.values()))

        logging.info(f"Обработка JSON-файлов завершена. Успешно: {success_count}, Ошибки: {len(json_files) - success_count}")

    def _execute_batches(self, cursor: sqlite3.Cursor, metadata: List[Tuple], fingerprints: List[Tuple]):
        """Выполняет пакетные вставки для метаданных и отпечатков."""
        
        # ИСПРАВЛЕНИЕ: Сначала вставляем родительские записи (fingerprints), затем дочерние (metadata).
        # Это устраняет ошибку 'FOREIGN KEY constraint failed'.
        if fingerprints:
            cursor.executemany(
                "INSERT OR REPLACE INTO fingerprints (fingerprint_hash, user_agent, platform, webgl_renderer, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                fingerprints
            )
            logging.info(f"Вставлено/обновлено {len(fingerprints)} записей в fingerprints.")

        if metadata:
            cursor.executemany(
                "INSERT OR REPLACE INTO result_metadata (session_id, user_id, fingerprint_hash, test_type, score, start_time, end_time, filename) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                metadata
            )
            logging.info(f"Вставлено/обновлено {len(metadata)} записей в result_metadata.")

    def _migrate_from_old_tables(self, cursor: sqlite3.Cursor):
        """Переносит данные из старых таблиц в новые, пропуская уже заполненные."""
        logging.info("Начало миграции данных из старых таблиц (proctoring_events, certificates, document_counters)...")
        tables_to_migrate = ['proctoring_events', 'certificates', 'document_counters']
        
        for table in tables_to_migrate:
            try:
                cursor.execute(f"PRAGMA table_info({table})")
                new_cols = {row['name'] for row in cursor.fetchall()}
                
                cursor.execute(f"PRAGMA table_info({table}_old)")
                old_cols = {row['name'] for row in cursor.fetchall()}
                
                common_cols = sorted(list(new_cols.intersection(old_cols)))
                if not common_cols:
                    logging.warning(f"Нет общих колонок между {table} и {table}_old. Пропуск.")
                    continue

                cols_str = ", ".join(common_cols)
                cursor.execute(f"INSERT INTO {table} ({cols_str}) SELECT {cols_str} FROM {table}_old")
                logging.info(f"Данные из '{table}_old' успешно перенесены в '{table}'.")

            except sqlite3.OperationalError as e:
                if "no such table" in str(e):
                    logging.warning(f"Старая таблица '{table}_old' не найдена. Миграция для нее пропущена.")
                else:
                    raise

    def cleanup_old_tables(self):
        """Удаляет старые таблицы с суффиксом _old. Выполнять после проверки миграции!"""
        self._connect()
        if not self.conn:
            return
            
        logging.warning("ВНИМАНИЕ: Сейчас будут удалены резервные копии таблиц (_old). Это действие необратимо.")
        confirm = input("Для подтверждения введите 'DELETE': ")
        
        if confirm != 'DELETE':
            logging.info("Удаление отменено.")
            self._close()
            return

        try:
            cursor = self.conn.cursor()
            schema = get_target_schema()
            for table_name in schema.keys():
                try:
                    cursor.execute(f"DROP TABLE IF EXISTS {table_name}_old")
                    logging.info(f"Таблица '{table_name}_old' удалена.")
                except sqlite3.OperationalError as e:
                    logging.error(f"Не удалось удалить таблицу {table_name}_old: {e}")
            self.conn.commit()
            logging.info("Очистка старых таблиц завершена.")
        except Exception as e:
            logging.error(f"Ошибка при очистке: {e}")
            self.conn.rollback()
        finally:
            self._close()

    def _print_report(self):
        """Выводит финальный отчет о миграции."""
        print("\n" + "="*60)
        print(" " * 15 + "Отчет о миграции базы данных")
        print("="*60)
        print(f"Миграция на схему V{TARGET_DB_VERSION} успешно завершена.")
        print("Новая структура базы данных создана.")
        print("Данные из JSON-файлов и старых таблиц перенесены.")
        print("\nВАЖНО:")
        print("Старые таблицы переименованы с суффиксом '_old'.")
        print("Проверьте целостность данных перед их удалением.")
        print("Для удаления старых таблиц вызовите функцию cleanup_old_tables().")
        print("="*60)


# =============================================================================
# ТОЧКА ВХОДА
# =============================================================================
if __name__ == '__main__':
    migrator = DatabaseMigrator(db_path=DATABASE_PATH, results_dir=RESULTS_DIR)
    migrator.run()
    
    # Для удаления старых таблиц после проверки раскомментируйте следующую строку:
    migrator.cleanup_old_tables()



