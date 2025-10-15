import sqlite3
import os

# --- КОНФИГУРАЦИЯ ---
# Убедитесь, что путь к вашей базе данных правильный.
# Скрипт ожидает, что app_data.db находится в той же папке.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, 'app_data.db')

def migrate_database():
    """
    Безопасно обновляет схему существующей базы данных app_data.db,
    добавляя новые поля и индексы, необходимые для последней версии приложения.
    Скрипт можно безопасно запускать несколько раз.
    """
    print(f"Подключение к базе данных: {DATABASE_PATH}...")
    
    if not os.path.exists(DATABASE_PATH):
        print("Ошибка: База данных не найдена. Сначала запустите основное приложение, чтобы создать ее.")
        return

    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        print("\n--- Обновление таблицы proctoring_events ---")
        
        # Получаем информацию о существующих колонках
        cursor.execute("PRAGMA table_info(proctoring_events)")
        existing_columns = [row[1] for row in cursor.fetchall()]
        
        # Безопасно добавляем новые колонки, если их нет
        new_columns = {
            'persistent_id': 'TEXT',
            'client_ip': 'TEXT',
            'page': 'TEXT'
        }
        
        for col_name, col_type in new_columns.items():
            if col_name not in existing_columns:
                print(f"Добавление новой колонки: {col_name}...")
                cursor.execute(f"ALTER TABLE proctoring_events ADD COLUMN {col_name} {col_type}")
            else:
                print(f"Колонка {col_name} уже существует.")

        print("\n--- Создание индексов для ускорения запросов ---")
        
        # Создаем индексы с проверкой `IF NOT EXISTS`, чтобы избежать ошибок
        indexes_to_create = [
            'CREATE INDEX IF NOT EXISTS idx_events_session ON proctoring_events(session_id)',
            'CREATE INDEX IF NOT EXISTS idx_events_type ON proctoring_events(event_type)',
            'CREATE INDEX IF NOT EXISTS idx_events_timestamp ON proctoring_events(event_timestamp)',
            'CREATE INDEX IF NOT EXISTS idx_events_persistent_id ON proctoring_events(persistent_id)',
            'CREATE INDEX IF NOT EXISTS idx_events_ip ON proctoring_events(client_ip)',
            'CREATE INDEX IF NOT EXISTS idx_certs_date ON certificates(issue_date)',
        ]
        
        for index_sql in indexes_to_create:
            index_name = index_sql.split(" ")[4]
            print(f"Проверка/создание индекса: {index_name}...")
            cursor.execute(index_sql)
            
        conn.commit()
        print("\nМиграция базы данных успешно завершена!")

    except sqlite3.Error as e:
        print(f"\nПроизошла ошибка SQLite: {e}")
    finally:
        if conn:
            conn.close()
            print("Соединение с базой данных закрыто.")

if __name__ == '__main__':
    migrate_database()
