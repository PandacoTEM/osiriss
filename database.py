import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reminders.db")
DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    if DATABASE_URL:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    import sqlite3
    return sqlite3.connect(DB_PATH)

def _last_id(c):
    if DATABASE_URL:
        return c.fetchone()[0]
    return c.lastrowid

def _placeholders(n):
    if DATABASE_URL:
        return "%s"
    return "?"

def init_db():
    conn = get_conn()
    c = conn.cursor()
    if DATABASE_URL:
        c.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                text TEXT NOT NULL,
                datetime TEXT NOT NULL,
                recurring TEXT,
                search_query TEXT,
                created_at TEXT NOT NULL,
                friend_name TEXT,
                end_date TEXT,
                lead_minutes INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount REAL NOT NULL,
                description TEXT,
                category TEXT,
                currency TEXT DEFAULT 'CRC',
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                timestamp TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS task_lists (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS task_items (
                id SERIAL PRIMARY KEY,
                list_id INTEGER NOT NULL REFERENCES task_lists(id),
                text TEXT NOT NULL,
                completed INTEGER DEFAULT 0,
                priority INTEGER DEFAULT 0,
                tags TEXT,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_tokens (
                user_id BIGINT PRIMARY KEY,
                token_data TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS authorized_users (
                user_id BIGINT PRIMARY KEY
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS auth_codes (
                code TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                used INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS learning_patterns (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                pattern_type TEXT NOT NULL,
                pattern_key TEXT NOT NULL,
                pattern_value TEXT NOT NULL,
                frequency INTEGER DEFAULT 1,
                last_observed TEXT NOT NULL,
                    confidence REAL DEFAULT 0.0,
                UNIQUE(user_id, pattern_type, pattern_key)
            )
        """)
    else:
        c.execute("CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, text TEXT NOT NULL, datetime TEXT NOT NULL, recurring TEXT, search_query TEXT, created_at TEXT NOT NULL, active INTEGER DEFAULT 1)")
        for col in ["search_query", "friend_name", "end_date", "lead_minutes INTEGER DEFAULT 0"]:
            try:
                c.execute(f"ALTER TABLE reminders ADD COLUMN {col}")
            except Exception:
                pass
        c.execute("CREATE TABLE IF NOT EXISTS expenses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount REAL NOT NULL, description TEXT, category TEXT, currency TEXT DEFAULT 'CRC', created_at TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS activity_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, action TEXT NOT NULL, details TEXT, timestamp TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, timestamp TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS task_lists (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS task_items (id INTEGER PRIMARY KEY AUTOINCREMENT, list_id INTEGER NOT NULL, text TEXT NOT NULL, completed INTEGER DEFAULT 0, priority INTEGER DEFAULT 0, tags TEXT, created_at TEXT NOT NULL, FOREIGN KEY (list_id) REFERENCES task_lists(id))")
        c.execute("CREATE TABLE IF NOT EXISTS user_tokens (user_id INTEGER PRIMARY KEY, token_data TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS authorized_users (user_id INTEGER PRIMARY KEY)")
        c.execute("CREATE TABLE IF NOT EXISTS auth_codes (code TEXT PRIMARY KEY, created_at TEXT NOT NULL, used INTEGER DEFAULT 0)")
        c.execute("CREATE TABLE IF NOT EXISTS learning_patterns (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, pattern_type TEXT NOT NULL, pattern_key TEXT NOT NULL, pattern_value TEXT NOT NULL, frequency INTEGER DEFAULT 1, last_observed TEXT NOT NULL, confidence REAL DEFAULT 0.0, UNIQUE(user_id, pattern_type, pattern_key))")
    conn.commit()
    conn.close()

def add_reminder(user_id, text, dt, recurring=None, search_query=None, friend_name=None, end_date=None, lead_minutes=0):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if DATABASE_URL:
        c.execute("INSERT INTO reminders (user_id, text, datetime, recurring, search_query, created_at, friend_name, end_date, lead_minutes) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                  (user_id, text, dt, recurring, search_query, now, friend_name, end_date, lead_minutes))
    else:
        c.execute("INSERT INTO reminders (user_id, text, datetime, recurring, search_query, created_at, friend_name, end_date, lead_minutes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (user_id, text, dt, recurring, search_query, now, friend_name, end_date, lead_minutes))
    rid = _last_id(c)
    conn.commit()
    conn.close()
    return rid

def get_all_active():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, user_id, text, datetime, recurring, search_query, friend_name, end_date, lead_minutes FROM reminders WHERE active = 1 ORDER BY datetime")
    rows = c.fetchall()
    conn.close()
    return rows

def get_reminders(user_id, date_filter=None):
    conn = get_conn()
    c = conn.cursor()
    if date_filter == "today":
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute(f"SELECT id, text, datetime, recurring FROM reminders WHERE user_id = {_placeholders(1)} AND active = 1 AND datetime LIKE {_placeholders(2)} ORDER BY datetime", (user_id, f"{today}%"))
    elif date_filter == "tomorrow":
        from datetime import timedelta
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        c.execute(f"SELECT id, text, datetime, recurring FROM reminders WHERE user_id = {_placeholders(1)} AND active = 1 AND datetime LIKE {_placeholders(2)} ORDER BY datetime", (user_id, f"{tomorrow}%"))
    elif date_filter and date_filter != "all":
        c.execute(f"SELECT id, text, datetime, recurring FROM reminders WHERE user_id = {_placeholders(1)} AND active = 1 AND datetime LIKE {_placeholders(2)} ORDER BY datetime", (user_id, f"{date_filter}%"))
    else:
        c.execute(f"SELECT id, text, datetime, recurring FROM reminders WHERE user_id = {_placeholders(1)} AND active = 1 ORDER BY datetime", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def deactivate_by_id(reminder_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"UPDATE reminders SET active = 0 WHERE id = {_placeholders(1)}", (reminder_id,))
    conn.commit()
    conn.close()

def deactivate_by_text(user_id, text_search):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"UPDATE reminders SET active = 0 WHERE user_id = {_placeholders(1)} AND active = 1 AND LOWER(text) LIKE LOWER({_placeholders(2)})", (user_id, f"%{text_search}%"))
    conn.commit()
    affected = c.rowcount
    conn.close()
    return affected

def update_datetime(reminder_id, new_dt):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"UPDATE reminders SET datetime = {_placeholders(1)} WHERE id = {_placeholders(2)}", (new_dt, reminder_id))
    conn.commit()
    conn.close()

def log_activity(user_id, action, details=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"INSERT INTO activity_log (user_id, action, details, timestamp) VALUES ({_placeholders(1)}, {_placeholders(2)}, {_placeholders(3)}, {_placeholders(4)})",
              (user_id, action, details, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

def get_today_activity(user_id):
    conn = get_conn()
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute(f"SELECT action, details, timestamp FROM activity_log WHERE user_id = {_placeholders(1)} AND timestamp LIKE {_placeholders(2)} ORDER BY timestamp",
              (user_id, f"{today}%"))
    rows = c.fetchall()
    conn.close()
    return rows

def save_message(user_id, role, content):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"INSERT INTO chat_history (user_id, role, content, timestamp) VALUES ({_placeholders(1)}, {_placeholders(2)}, {_placeholders(3)}, {_placeholders(4)})",
              (user_id, role, content, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

def get_recent_history(user_id, limit=6):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT role, content FROM chat_history WHERE user_id = {_placeholders(1)} ORDER BY id DESC LIMIT {_placeholders(2)}",
              (user_id, limit * 2))
    rows = c.fetchall()
    conn.close()
    rows.reverse()
    return rows

def create_task_list(user_id, name):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if DATABASE_URL:
        c.execute("INSERT INTO task_lists (user_id, name, created_at) VALUES (%s, %s, %s) RETURNING id", (user_id, name, now))
    else:
        c.execute("INSERT INTO task_lists (user_id, name, created_at) VALUES (?, ?, ?)", (user_id, name, now))
    lid = _last_id(c)
    conn.commit()
    conn.close()
    return lid

def add_task_item(list_id, text, priority=0, tags=None):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if DATABASE_URL:
        c.execute("INSERT INTO task_items (list_id, text, completed, priority, tags, created_at) VALUES (%s, %s, 0, %s, %s, %s) RETURNING id",
                  (list_id, text, priority, tags, now))
    else:
        c.execute("INSERT INTO task_items (list_id, text, completed, priority, tags, created_at) VALUES (?, ?, 0, ?, ?, ?)",
                  (list_id, text, priority, tags, now))
    iid = _last_id(c)
    conn.commit()
    conn.close()
    return iid

def get_task_lists(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT id, name, created_at FROM task_lists WHERE user_id = {_placeholders(1)} ORDER BY id DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_list_items(list_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT id, text, completed, priority, tags FROM task_items WHERE list_id = {_placeholders(1)} ORDER BY priority DESC, id", (list_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def toggle_task_item(item_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT completed FROM task_items WHERE id = {_placeholders(1)}", (item_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    new_status = 0 if row[0] else 1
    c.execute(f"UPDATE task_items SET completed = {_placeholders(1)} WHERE id = {_placeholders(2)}", (new_status, item_id))
    conn.commit()
    conn.close()
    return new_status

def delete_task_list(list_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"DELETE FROM task_items WHERE list_id = {_placeholders(1)}", (list_id,))
    c.execute(f"DELETE FROM task_lists WHERE id = {_placeholders(1)}", (list_id,))
    conn.commit()
    conn.close()

def delete_task_item(item_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"DELETE FROM task_items WHERE id = {_placeholders(1)}", (item_id,))
    conn.commit()
    conn.close()

def find_list_by_name(user_id, name):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT id, name FROM task_lists WHERE user_id = {_placeholders(1)} AND LOWER(name) = LOWER({_placeholders(2)})", (user_id, name))
    row = c.fetchone()
    conn.close()
    return row

def search_lists(user_id, query):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT id, name FROM task_lists WHERE user_id = {_placeholders(1)} AND LOWER(name) LIKE LOWER({_placeholders(2)}) ORDER BY id DESC", (user_id, f"%{query}%"))
    rows = c.fetchall()
    conn.close()
    return rows

def add_expense(user_id, amount, description=None, category=None, currency="CRC"):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if DATABASE_URL:
        c.execute("INSERT INTO expenses (user_id, amount, description, category, currency, created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                  (user_id, amount, description, category, currency, now))
    else:
        c.execute("INSERT INTO expenses (user_id, amount, description, category, currency, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                  (user_id, amount, description, category, currency, now))
    eid = _last_id(c)
    conn.commit()
    conn.close()
    return eid

def get_today_expenses(user_id):
    conn = get_conn()
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute(f"SELECT amount, description, category, currency FROM expenses WHERE user_id = {_placeholders(1)} AND created_at LIKE {_placeholders(2)} ORDER BY id", (user_id, f"{today}%"))
    rows = c.fetchall()
    conn.close()
    return rows

def get_today_total(user_id):
    rows = get_today_expenses(user_id)
    return sum(r[0] for r in rows) if rows else 0

def get_recent_expenses(user_id, limit=5):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT amount, description, category, currency, created_at FROM expenses WHERE user_id = {_placeholders(1)} ORDER BY id DESC LIMIT {_placeholders(2)}", (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return rows

def save_token(user_id, token_data):
    conn = get_conn()
    c = conn.cursor()
    if DATABASE_URL:
        c.execute("INSERT INTO user_tokens (user_id, token_data) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET token_data = EXCLUDED.token_data",
                  (user_id, token_data))
    else:
        c.execute("INSERT OR REPLACE INTO user_tokens (user_id, token_data) VALUES (?, ?)", (user_id, token_data))
    conn.commit()
    conn.close()

def get_token(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT token_data FROM user_tokens WHERE user_id = {_placeholders(1)}", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def authorize_user(user_id):
    conn = get_conn()
    c = conn.cursor()
    if DATABASE_URL:
        c.execute("INSERT INTO authorized_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    else:
        c.execute("INSERT OR IGNORE INTO authorized_users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def deauthorize_user(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"DELETE FROM authorized_users WHERE user_id = {_placeholders(1)}", (user_id,))
    conn.commit()
    conn.close()

def is_authorized(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT 1 FROM authorized_users WHERE user_id = {_placeholders(1)}", (user_id,))
    row = c.fetchone()
    conn.close()
    return row is not None

import secrets

def create_auth_code():
    code = secrets.token_hex(4).upper()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"INSERT INTO auth_codes (code, created_at, used) VALUES ({_placeholders(1)}, {_placeholders(2)}, 0)", (code, now))
    conn.commit()
    conn.close()
    return code

def redeem_auth_code(code, user_id):
    code = code.upper().strip()
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT used, created_at FROM auth_codes WHERE code = {_placeholders(1)}", (code,))
    row = c.fetchone()
    if not row:
        conn.close()
        return "invalido"
    if row[0]:
        conn.close()
        return "usado"
    from datetime import timedelta
    created = datetime.strptime(row[1], "%Y-%m-%d %H:%M")
    if datetime.now() - created > timedelta(hours=24):
        conn.close()
        return "expirado"
    c.execute(f"UPDATE auth_codes SET used = 1 WHERE code = {_placeholders(1)}", (code,))
    conn.commit()
    conn.close()
    authorize_user(user_id)
    return "ok"
