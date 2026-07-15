import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reminders.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            datetime TEXT NOT NULL,
            recurring TEXT,
            search_query TEXT,
            created_at TEXT NOT NULL,
            active INTEGER DEFAULT 1
        )
    """)
    try:
        c.execute("ALTER TABLE reminders ADD COLUMN search_query TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE reminders ADD COLUMN friend_name TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE reminders ADD COLUMN end_date TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE reminders ADD COLUMN lead_minutes INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    c.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            timestamp TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS task_lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS task_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            completed INTEGER DEFAULT 0,
            priority INTEGER DEFAULT 0,
            tags TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (list_id) REFERENCES task_lists(id)
        )
    """)
    conn.commit()
    conn.close()

def add_reminder(user_id, text, dt, recurring=None, search_query=None, friend_name=None, end_date=None, lead_minutes=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO reminders (user_id, text, datetime, recurring, search_query, created_at, friend_name, end_date, lead_minutes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, text, dt, recurring, search_query, datetime.now().strftime("%Y-%m-%d %H:%M"), friend_name, end_date, lead_minutes)
    )
    conn.commit()
    reminder_id = c.lastrowid
    conn.close()
    return reminder_id

def get_all_active():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, user_id, text, datetime, recurring, search_query, friend_name, end_date, lead_minutes FROM reminders WHERE active = 1 ORDER BY datetime")
    rows = c.fetchall()
    conn.close()
    return rows

def get_reminders(user_id, date_filter=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if date_filter == "today":
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute("SELECT id, text, datetime, recurring FROM reminders WHERE user_id = ? AND active = 1 AND datetime LIKE ? ORDER BY datetime", (user_id, f"{today}%"))
    elif date_filter == "tomorrow":
        from datetime import timedelta
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        c.execute("SELECT id, text, datetime, recurring FROM reminders WHERE user_id = ? AND active = 1 AND datetime LIKE ? ORDER BY datetime", (user_id, f"{tomorrow}%"))
    elif date_filter and date_filter != "all":
        c.execute("SELECT id, text, datetime, recurring FROM reminders WHERE user_id = ? AND active = 1 AND datetime LIKE ? ORDER BY datetime", (user_id, f"{date_filter}%"))
    else:
        c.execute("SELECT id, text, datetime, recurring FROM reminders WHERE user_id = ? AND active = 1 ORDER BY datetime", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def deactivate_by_id(reminder_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE reminders SET active = 0 WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()

def deactivate_by_text(user_id, text_search):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE reminders SET active = 0 WHERE user_id = ? AND active = 1 AND LOWER(text) LIKE LOWER(?)", (user_id, f"%{text_search}%"))
    conn.commit()
    affected = c.rowcount
    conn.close()
    return affected

def update_datetime(reminder_id, new_dt):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE reminders SET datetime = ? WHERE id = ?", (new_dt, reminder_id))
    conn.commit()
    conn.close()

def log_activity(user_id, action, details=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO activity_log (user_id, action, details, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, action, details, datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    conn.commit()
    conn.close()

def get_today_activity(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute(
        "SELECT action, details, timestamp FROM activity_log WHERE user_id = ? AND timestamp LIKE ? ORDER BY timestamp",
        (user_id, f"{today}%")
    )
    rows = c.fetchall()
    conn.close()
    return rows

def save_message(user_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO chat_history (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, role, content, datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    conn.commit()
    conn.close()

def get_recent_history(user_id, limit=6):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit * 2)
    )
    rows = c.fetchall()
    conn.close()
    rows.reverse()
    return rows

def create_task_list(user_id, name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO task_lists (user_id, name, created_at) VALUES (?, ?, ?)",
              (user_id, name, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    list_id = c.lastrowid
    conn.close()
    return list_id

def add_task_item(list_id, text, priority=0, tags=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO task_items (list_id, text, completed, priority, tags, created_at) VALUES (?, ?, 0, ?, ?, ?)",
              (list_id, text, priority, tags, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    item_id = c.lastrowid
    conn.close()
    return item_id

def get_task_lists(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, created_at FROM task_lists WHERE user_id = ? ORDER BY id DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_list_items(list_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, text, completed, priority, tags FROM task_items WHERE list_id = ? ORDER BY priority DESC, id", (list_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def toggle_task_item(item_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT completed FROM task_items WHERE id = ?", (item_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    new_status = 0 if row[0] else 1
    c.execute("UPDATE task_items SET completed = ? WHERE id = ?", (new_status, item_id))
    conn.commit()
    conn.close()
    return new_status

def delete_task_list(list_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM task_items WHERE list_id = ?", (list_id,))
    c.execute("DELETE FROM task_lists WHERE id = ?", (list_id,))
    conn.commit()
    conn.close()

def delete_task_item(item_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM task_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()

def find_list_by_name(user_id, name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name FROM task_lists WHERE user_id = ? AND LOWER(name) = LOWER(?)", (user_id, name))
    row = c.fetchone()
    conn.close()
    return row

def search_lists(user_id, query):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name FROM task_lists WHERE user_id = ? AND LOWER(name) LIKE LOWER(?) ORDER BY id DESC", (user_id, f"%{query}%"))
    rows = c.fetchall()
    conn.close()
    return rows
