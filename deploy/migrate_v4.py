import os
import json
import sqlite3
import logging
from typing import Dict, Any, Optional, List, Tuple

# =============================================================================
# CONFIGURATION
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, 'results_data')
DATABASE_PATH = os.path.join(BASE_DIR, 'app_data.db')
TARGET_DB_VERSION = 4  # CHANGED: Target schema version is now 4
BATCH_SIZE = 100

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s'
)

# =============================================================================
# TARGET DATABASE SCHEMA (V4)
# =============================================================================

def get_target_schema() -> Dict[str, str]:
    """Returns CREATE statements for the new schema V4."""
    return {
        # ... other table definitions are unchanged ...
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
                test_type TEXT, score INTEGER, start_time TEXT, end_time TEXT,
                client_ip TEXT, -- ADDED: client_ip column
                filename TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE SET NULL,
                FOREIGN KEY(fingerprint_hash) REFERENCES fingerprints(fingerprint_hash) ON DELETE SET NULL
            )
        ''',
        # ... other table definitions are unchanged ...
        'document_counters': '''
            CREATE TABLE document_counters (
                period TEXT PRIMARY KEY,
                last_sequence_number INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''',
        'proctoring_events': '''
            CREATE TABLE proctoring_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, event_type TEXT NOT NULL, event_timestamp TEXT NOT NULL,
                details TEXT, persistent_id TEXT, client_ip TEXT, page TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''',
        'certificates': '''
            CREATE TABLE certificates (
                document_number TEXT PRIMARY KEY, user_fullname TEXT NOT NULL,
                user_position TEXT, test_type TEXT NOT NULL, issue_date TEXT NOT NULL,
                score_percentage INTEGER NOT NULL, session_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        '''
    }

def get_target_indexes() -> List[str]:
    # ... code is unchanged ...
    return [
        'CREATE INDEX IF NOT EXISTS idx_events_session ON proctoring_events(session_id)',
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_users_persistent_id ON users(persistent_id)',
        'CREATE INDEX IF NOT EXISTS idx_metadata_user_id ON result_metadata(user_id)',
    ]

# =============================================================================
# MIGRATION CLASS
# =============================================================================

class DatabaseMigrator:
    # __init__, _connect, _close, run, _backup_and_recreate_schema, 
    # and _get_or_create_user_id are unchanged.

    def __init__(self, db_path: str, results_dir: str):
        self.db_path = db_path
        self.results_dir = results_dir
        self.conn: Optional[sqlite3.Connection] = None
        self.user_cache: Dict[str, int] = {}

    def _connect(self):
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA foreign_keys = ON;")
            logging.info(f"Successfully connected to the database: {self.db_path}")
        except sqlite3.Error as e:
            logging.error(f"Error connecting to the database: {e}")
            raise

    def _close(self):
        if self.conn:
            self.conn.close()
            logging.info("Database connection closed.")

    def run(self):
        self._connect()
        if not self.conn: return

        try:
            cursor = self.conn.cursor()
            current_version = cursor.execute("PRAGMA user_version").fetchone()[0]
            if current_version >= TARGET_DB_VERSION:
                logging.info(f"Migration not required. Current schema version ({current_version}) is already V{TARGET_DB_VERSION} or newer.")
                return

            logging.info(f"Starting migration from schema v{current_version} to v{TARGET_DB_VERSION}...")
            
            cursor.execute("BEGIN;")
            self._backup_and_recreate_schema(cursor)
            self._migrate_from_json_files(cursor)
            self._migrate_from_old_tables(cursor)
            
            logging.info("Creating indexes...")
            for index_sql in get_target_indexes():
                cursor.execute(index_sql)
            logging.info("Indexes created successfully.")

            cursor.execute(f"PRAGMA user_version = {TARGET_DB_VERSION};")
            
            self.conn.commit()
            logging.info("Transaction successfully committed. Migration to V%s is complete.", TARGET_DB_VERSION)
            self._print_report()

        except Exception as e:
            logging.error(f"An error occurred during migration: {e}", exc_info=True)
            if self.conn:
                self.conn.rollback()
                logging.warning("All changes have been rolled back due to an error.")
        finally:
            self._close()

    def _backup_and_recreate_schema(self, cursor: sqlite3.Cursor):
        logging.info("Starting schema backup and recreation...")
        schema = get_target_schema()
        
        for table_name in schema.keys():
            try:
                cursor.execute(f"ALTER TABLE {table_name} RENAME TO {table_name}_old")
                logging.info(f"Table '{table_name}' renamed to '{table_name}_old'.")
            except sqlite3.OperationalError as e:
                if "no such table" in str(e):
                    logging.warning(f"Table '{table_name}' not found for renaming, a new one will be created.")
                else: raise
        
        logging.info("Creating new tables...")
        for create_sql in schema.values():
            cursor.execute(create_sql)
        logging.info("New tables created successfully.")

    def _get_or_create_user_id(self, cursor: sqlite3.Cursor, user_info: Dict[str, Any], persistent_id: Optional[str]) -> Optional[int]:
        if not persistent_id:
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
        """Processes JSON files and populates new tables with batch inserts."""
        json_files = [f for f in os.listdir(self.results_dir) if f.endswith('.json')]
        logging.info(f"Found {len(json_files)} JSON files to migrate into users, fingerprints, and result_metadata tables.")
        
        success_count = 0
        metadata_batch: List[Tuple] = []
        fingerprints_to_update: Dict[str, Tuple] = {}

        for filename in json_files:
            filepath = os.path.join(self.results_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f: data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logging.warning(f"Failed to process file {filename}: {e}")
                continue

            session_id = data.get('sessionId')
            if not session_id:
                logging.warning(f"Skipping file {filename}: 'sessionId' is missing.")
                continue

            # --- MAIN FIX IS HERE ---
            user_info = data.get('userInfo', {})
            persistent_id = data.get('persistentId', {}).get('cookie')
            user_id = self._get_or_create_user_id(cursor, user_info, persistent_id)
            
            fp_hash = data.get('fingerprint', {}).get('privacySafeHash')
            start_time = data.get('sessionMetrics', {}).get('startTime')
            
            # CHANGED: Extract clientIp from the JSON file
            client_ip = data.get('clientIp') 

            if fp_hash and start_time:
                fp_data = data.get('fingerprint', {}).get('privacySafe', {})
                if fp_hash not in fingerprints_to_update or start_time > fingerprints_to_update[fp_hash][-1]:
                    fingerprints_to_update[fp_hash] = (fp_hash, fp_data.get('userAgent'), fp_data.get('platform'), fp_data.get('webGLRenderer'), start_time, start_time)
            
            # CHANGED: Add client_ip to the data batch
            metadata_batch.append((
                session_id, user_id, fp_hash,
                data.get('testType'), data.get('testResults', {}).get('percentage'),
                start_time, data.get('sessionMetrics', {}).get('endTime'),
                client_ip, # Added IP
                filename
            ))
            success_count += 1
            
            if len(metadata_batch) >= BATCH_SIZE:
                self._execute_batches(cursor, metadata_batch, list(fingerprints_to_update.values()))
                metadata_batch.clear(); fingerprints_to_update.clear()

        if metadata_batch: self._execute_batches(cursor, metadata_batch, list(fingerprints_to_update.values()))
        logging.info(f"JSON file processing complete. Success: {success_count}, Errors: {len(json_files) - success_count}")

    def _execute_batches(self, cursor: sqlite3.Cursor, metadata: List[Tuple], fingerprints: List[Tuple]):
        """Executes batch inserts for metadata and fingerprints."""
        
        if fingerprints:
            cursor.executemany("INSERT OR REPLACE INTO fingerprints (fingerprint_hash, user_agent, platform, webgl_renderer, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)", fingerprints)

        if metadata:
            # CHANGED: Updated INSERT statement to include client_ip
            cursor.executemany(
                "INSERT OR REPLACE INTO result_metadata (session_id, user_id, fingerprint_hash, test_type, score, start_time, end_time, client_ip, filename) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                metadata
            )

    def _migrate_from_old_tables(self, cursor: sqlite3.Cursor):
        # ... code is unchanged ...
        logging.info("Starting data migration from old tables...")
        tables_to_migrate = ['proctoring_events', 'certificates', 'document_counters']
        
        for table in tables_to_migrate:
            try:
                cursor.execute(f"PRAGMA table_info({table})"); new_cols = {row['name'] for row in cursor.fetchall()}
                cursor.execute(f"PRAGMA table_info({table}_old)"); old_cols = {row['name'] for row in cursor.fetchall()}
                
                common_cols = sorted(list(new_cols.intersection(old_cols)))
                if not common_cols:
                    logging.warning(f"No common columns between {table} and {table}_old. Skipping."); continue

                cols_str = ", ".join(common_cols)
                cursor.execute(f"INSERT OR IGNORE INTO {table} ({cols_str}) SELECT {cols_str} FROM {table}_old")
                logging.info(f"Data from '{table}_old' successfully migrated to '{table}'.")

            except sqlite3.OperationalError as e:
                if "no such table" in str(e):
                    logging.warning(f"Old table '{table}_old' not found. Migration for it skipped.")
                else: raise

    # cleanup_old_tables and _print_report are unchanged
    def cleanup_old_tables(self):
        self._connect()
        if not self.conn: return
        logging.warning("ATTENTION: This will permanently delete the backup tables (_old). This action is irreversible.")
        confirm = input("To confirm, type 'DELETE': ")
        
        if confirm != 'DELETE':
            logging.info("Deletion canceled."); self._close(); return

        try:
            cursor = self.conn.cursor()
            schema = get_target_schema()
            for table_name in schema.keys():
                cursor.execute(f"DROP TABLE IF EXISTS {table_name}_old")
                logging.info(f"Table '{table_name}_old' deleted.")
            self.conn.commit()
            logging.info("Cleanup of old tables complete.")
        except Exception as e:
            logging.error(f"Error during cleanup: {e}"); self.conn.rollback()
        finally:
            self._close()

    def _print_report(self):
        print("\n" + "="*60 + "\n" + " " * 15 + "Database Migration Report" + "\n" + "="*60)
        print(f"Migration to schema V{TARGET_DB_VERSION} completed successfully.")
        print("✅ New database structure created.")
        print("✅ Data from JSON files and old tables has been migrated.")
        print("✅ Indexes for query acceleration have been created.")
        print("\nIMPORTANT:")
        print("🔵 Verify data integrity in the application.")
        print("🔵 Old tables have been saved with the '_old' suffix.")
        print("🔵 To delete them, uncomment and run cleanup_old_tables().")
        print("="*60)

# =============================================================================
# ENTRY POINT
# =============================================================================
if __name__ == '__main__':
    migrator = DatabaseMigrator(db_path=DATABASE_PATH, results_dir=RESULTS_DIR)
    migrator.run()
    
    # After verifying the data, you can run the cleanup:
    migrator.cleanup_old_tables()
