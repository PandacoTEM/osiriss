import hashlib
import json
from calendar import monthrange
from datetime import datetime, timedelta

import database


def _ph():
    return "%s" if database.DATABASE_URL else "?"


def _insert_id(cursor, sql, params):
    if database.DATABASE_URL:
        cursor.execute(sql + " RETURNING id", params)
        return cursor.fetchone()[0]
    cursor.execute(sql, params)
    return cursor.lastrowid


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str) if value is not None else None


def init_feature_schema():
    conn = database.get_conn()
    c = conn.cursor()
    identity = "SERIAL PRIMARY KEY" if database.DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    bigint = "BIGINT" if database.DATABASE_URL else "INTEGER"
    statements = [
        f"""CREATE TABLE IF NOT EXISTS user_preferences (
            user_id {bigint} NOT NULL,
            preference_key TEXT NOT NULL,
            preference_value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, preference_key)
        )""",
        f"""CREATE TABLE IF NOT EXISTS feature_flags (
            user_id {bigint} NOT NULL,
            feature_name TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, feature_name)
        )""",
        f"""CREATE TABLE IF NOT EXISTS action_history (
            id {identity},
            user_id {bigint} NOT NULL,
            action_type TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            before_data TEXT,
            after_data TEXT,
            reversible INTEGER DEFAULT 0,
            undone INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS inbox_items (
            id {identity},
            user_id {bigint} NOT NULL,
            item_type TEXT DEFAULT 'note',
            content TEXT NOT NULL,
            category TEXT DEFAULT 'inbox',
            source_file_id TEXT,
            private INTEGER DEFAULT 0,
            status TEXT DEFAULT 'open',
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS routines (
            id {identity},
            user_id {bigint} NOT NULL,
            name TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, name)
        )""",
        f"""CREATE TABLE IF NOT EXISTS routine_steps (
            id {identity},
            routine_id INTEGER NOT NULL,
            position INTEGER DEFAULT 0,
            step_type TEXT DEFAULT 'task',
            content TEXT NOT NULL,
            at_time TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS habits (
            id {identity},
            user_id {bigint} NOT NULL,
            name TEXT NOT NULL,
            frequency TEXT DEFAULT 'daily',
            target_count INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, name)
        )""",
        f"""CREATE TABLE IF NOT EXISTS habit_logs (
            id {identity},
            habit_id INTEGER NOT NULL,
            user_id {bigint} NOT NULL,
            log_date TEXT NOT NULL,
            value REAL DEFAULT 1,
            note TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(habit_id, log_date)
        )""",
        f"""CREATE TABLE IF NOT EXISTS goals (
            id {identity},
            user_id {bigint} NOT NULL,
            title TEXT NOT NULL,
            target_date TEXT,
            status TEXT DEFAULT 'active',
            progress INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS goal_steps (
            id {identity},
            goal_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            completed INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS important_dates (
            id {identity},
            user_id {bigint} NOT NULL,
            title TEXT NOT NULL,
            event_date TEXT NOT NULL,
            recurring INTEGER DEFAULT 1,
            lead_days INTEGER DEFAULT 7,
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS documents (
            id {identity},
            user_id {bigint} NOT NULL,
            title TEXT NOT NULL,
            file_type TEXT NOT NULL,
            telegram_file_id TEXT,
            content_hash TEXT NOT NULL,
            status TEXT DEFAULT 'ready',
            private INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, content_hash)
        )""",
        f"""CREATE TABLE IF NOT EXISTS document_chunks (
            id {identity},
            document_id INTEGER NOT NULL,
            user_id {bigint} NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS budgets (
            id {identity},
            user_id {bigint} NOT NULL,
            category TEXT NOT NULL,
            currency TEXT DEFAULT 'CRC',
            monthly_limit REAL NOT NULL,
            alert_percent INTEGER DEFAULT 80,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, category, currency)
        )""",
        f"""CREATE TABLE IF NOT EXISTS subscriptions (
            id {identity},
            user_id {bigint} NOT NULL,
            name TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT DEFAULT 'CRC',
            category TEXT DEFAULT 'servicios',
            next_due TEXT NOT NULL,
            frequency TEXT DEFAULT 'monthly',
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS expense_items (
            id {identity},
            expense_id INTEGER NOT NULL,
            description TEXT NOT NULL,
            quantity REAL DEFAULT 1,
            unit_price REAL,
            total REAL NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS shares (
            id {identity},
            owner_user_id {bigint} NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id INTEGER NOT NULL,
            member_user_id {bigint} NOT NULL,
            permission TEXT DEFAULT 'edit',
            created_at TEXT NOT NULL,
            UNIQUE(resource_type, resource_id, member_user_id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS meetings (
            id {identity},
            user_id {bigint} NOT NULL,
            title TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            started_at TEXT NOT NULL,
            ended_at TEXT,
            summary TEXT
        )""",
        f"""CREATE TABLE IF NOT EXISTS meeting_items (
            id {identity},
            meeting_id INTEGER NOT NULL,
            item_type TEXT DEFAULT 'note',
            content TEXT NOT NULL,
            assignee TEXT,
            due_date TEXT,
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS provider_usage (
            id {identity},
            user_id {bigint},
            provider TEXT NOT NULL,
            operation TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS ai_cache (
            cache_key TEXT PRIMARY KEY,
            response TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS backups (
            id {identity},
            user_id {bigint} NOT NULL,
            file_name TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS system_checks (
            id {identity},
            check_name TEXT NOT NULL,
            status TEXT NOT NULL,
            details TEXT,
            checked_at TEXT NOT NULL
        )""",
    ]
    for statement in statements:
        c.execute(statement)
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_inbox_user_status ON inbox_items(user_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_history_user_created ON action_history(user_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_chunks_user ON document_chunks(user_id, document_id)",
        "CREATE INDEX IF NOT EXISTS idx_dates_user_date ON important_dates(user_id, event_date)",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_due ON subscriptions(user_id, next_due)",
    ):
        c.execute(statement)
    if database.DATABASE_URL:
        c.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS expires_at TEXT")
        c.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS sensitive INTEGER DEFAULT 0")
    else:
        columns = {row[1] for row in c.execute("PRAGMA table_info(memories)").fetchall()}
        if "expires_at" not in columns:
            c.execute("ALTER TABLE memories ADD COLUMN expires_at TEXT")
        if "sensitive" not in columns:
            c.execute("ALTER TABLE memories ADD COLUMN sensitive INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


def set_preference(user_id, key, value):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    payload = _json(value)
    if database.DATABASE_URL:
        c.execute(
            f"""INSERT INTO user_preferences (user_id, preference_key, preference_value, updated_at)
                VALUES ({p}, {p}, {p}, {p})
                ON CONFLICT (user_id, preference_key)
                DO UPDATE SET preference_value=EXCLUDED.preference_value, updated_at=EXCLUDED.updated_at""",
            (user_id, key, payload, database.now_str()),
        )
    else:
        c.execute(
            """INSERT INTO user_preferences (user_id, preference_key, preference_value, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, preference_key)
               DO UPDATE SET preference_value=excluded.preference_value, updated_at=excluded.updated_at""",
            (user_id, key, payload, database.now_str()),
        )
    conn.commit()
    conn.close()


def get_preference(user_id, key, default=None):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"SELECT preference_value FROM user_preferences WHERE user_id={p} AND preference_key={p}",
        (user_id, key),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return row[0]


def get_preferences(user_id):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"SELECT preference_key, preference_value FROM user_preferences WHERE user_id={p}",
        (user_id,),
    )
    result = {}
    for key, value in c.fetchall():
        try:
            result[key] = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            result[key] = value
    conn.close()
    return result


def set_feature_flag(user_id, feature_name, enabled):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    params = (user_id, feature_name, int(bool(enabled)), database.now_str())
    if database.DATABASE_URL:
        c.execute(
            f"""INSERT INTO feature_flags (user_id, feature_name, enabled, updated_at)
                VALUES ({p}, {p}, {p}, {p})
                ON CONFLICT (user_id, feature_name)
                DO UPDATE SET enabled=EXCLUDED.enabled, updated_at=EXCLUDED.updated_at""",
            params,
        )
    else:
        c.execute(
            """INSERT INTO feature_flags (user_id, feature_name, enabled, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, feature_name)
               DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at""",
            params,
        )
    conn.commit()
    conn.close()


def feature_enabled(user_id, feature_name, default=True):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"SELECT enabled FROM feature_flags WHERE user_id={p} AND feature_name={p}",
        (user_id, feature_name),
    )
    row = c.fetchone()
    conn.close()
    return bool(row[0]) if row else default


def record_history(user_id, action_type, target_type=None, target_id=None, before=None, after=None, reversible=False):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    history_id = _insert_id(
        c,
        f"""INSERT INTO action_history
            (user_id, action_type, target_type, target_id, before_data, after_data, reversible, created_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})""",
        (
            user_id,
            action_type,
            target_type,
            target_id,
            _json(before),
            _json(after),
            int(bool(reversible)),
            database.now_str(),
        ),
    )
    conn.commit()
    conn.close()
    return history_id


def undo_last_action(user_id):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"""SELECT id, action_type, target_type, target_id, before_data
            FROM action_history
            WHERE user_id={p} AND reversible=1 AND undone=0
            ORDER BY id DESC LIMIT 1""",
        (user_id,),
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    history_id, action_type, target_type, target_id, before_raw = row
    before = json.loads(before_raw) if before_raw else None
    changed = False
    if target_type == "reminder" and target_id:
        c.execute(f"UPDATE reminders SET active=0 WHERE id={p} AND user_id={p}", (target_id, user_id))
        changed = c.rowcount > 0
    elif target_type == "expense" and target_id:
        c.execute(f"DELETE FROM expenses WHERE id={p} AND user_id={p}", (target_id, user_id))
        changed = c.rowcount > 0
    elif target_type == "inbox" and target_id:
        c.execute(f"DELETE FROM inbox_items WHERE id={p} AND user_id={p}", (target_id, user_id))
        changed = c.rowcount > 0
    elif target_type == "habit_log" and target_id:
        c.execute(f"DELETE FROM habit_logs WHERE id={p} AND user_id={p}", (target_id, user_id))
        changed = c.rowcount > 0
    elif target_type == "goal" and target_id:
        c.execute(f"DELETE FROM goals WHERE id={p} AND user_id={p}", (target_id, user_id))
        changed = c.rowcount > 0
    elif target_type == "preference" and before:
        key = before.get("key")
        if key:
            if before.get("exists"):
                set_value = _json(before.get("value"))
                c.execute(
                    f"UPDATE user_preferences SET preference_value={p}, updated_at={p} WHERE user_id={p} AND preference_key={p}",
                    (set_value, database.now_str(), user_id, key),
                )
            else:
                c.execute(
                    f"DELETE FROM user_preferences WHERE user_id={p} AND preference_key={p}",
                    (user_id, key),
                )
            changed = True
    if changed:
        c.execute(f"UPDATE action_history SET undone=1 WHERE id={p}", (history_id,))
        conn.commit()
    conn.close()
    return action_type if changed else None


def add_inbox_item(user_id, content, category="inbox", item_type="note", source_file_id=None, private=False):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    item_id = _insert_id(
        c,
        f"""INSERT INTO inbox_items
            (user_id, item_type, content, category, source_file_id, private, created_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})""",
        (user_id, item_type, content, category, source_file_id, int(bool(private)), database.now_str()),
    )
    conn.commit()
    conn.close()
    record_history(user_id, "capture_inbox", "inbox", item_id, after={"content": content}, reversible=True)
    return item_id


def get_inbox(user_id, category=None, status="open", limit=20):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    params = [user_id, status]
    sql = f"""SELECT id, item_type, content, category, private, created_at
              FROM inbox_items WHERE user_id={p} AND status={p}"""
    if category:
        sql += f" AND LOWER(category)=LOWER({p})"
        params.append(category)
    sql += " ORDER BY id DESC"
    if database.DATABASE_URL:
        sql += f" LIMIT {p}"
        params.append(limit)
    else:
        sql += " LIMIT ?"
        params.append(limit)
    c.execute(sql, tuple(params))
    rows = c.fetchall()
    conn.close()
    return rows


def archive_inbox_item(user_id, item_id):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"UPDATE inbox_items SET status='archived' WHERE id={p} AND user_id={p}",
        (item_id, user_id),
    )
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def create_routine(user_id, name, steps):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    routine_id = _insert_id(
        c,
        f"INSERT INTO routines (user_id, name, created_at) VALUES ({p}, {p}, {p})",
        (user_id, name, database.now_str()),
    )
    for position, step in enumerate(steps, 1):
        content = step.get("content") if isinstance(step, dict) else str(step)
        step_type = step.get("type", "task") if isinstance(step, dict) else "task"
        at_time = step.get("time") if isinstance(step, dict) else None
        c.execute(
            f"""INSERT INTO routine_steps (routine_id, position, step_type, content, at_time)
                VALUES ({p}, {p}, {p}, {p}, {p})""",
            (routine_id, position, step_type, content, at_time),
        )
    conn.commit()
    conn.close()
    return routine_id


def get_routines(user_id):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"""SELECT r.id, r.name, r.active, COUNT(s.id)
            FROM routines r LEFT JOIN routine_steps s ON s.routine_id=r.id
            WHERE r.user_id={p} GROUP BY r.id, r.name, r.active ORDER BY r.name""",
        (user_id,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def get_routine(user_id, name):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"SELECT id, name FROM routines WHERE user_id={p} AND LOWER(name)=LOWER({p}) AND active=1",
        (user_id, name),
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    c.execute(
        f"""SELECT position, step_type, content, at_time FROM routine_steps
            WHERE routine_id={p} ORDER BY position""",
        (row[0],),
    )
    steps = c.fetchall()
    conn.close()
    return row[0], row[1], steps


def create_habit(user_id, name, frequency="daily", target_count=1):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    habit_id = _insert_id(
        c,
        f"INSERT INTO habits (user_id, name, frequency, target_count, created_at) VALUES ({p}, {p}, {p}, {p}, {p})",
        (user_id, name, frequency, target_count, database.now_str()),
    )
    conn.commit()
    conn.close()
    return habit_id


def log_habit(user_id, name, value=1, note=None, log_date=None):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"SELECT id FROM habits WHERE user_id={p} AND LOWER(name)=LOWER({p}) AND active=1",
        (user_id, name),
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    day = log_date or database.local_now().strftime("%Y-%m-%d")
    if database.DATABASE_URL:
        c.execute(
            f"""INSERT INTO habit_logs (habit_id, user_id, log_date, value, note, created_at)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p})
                ON CONFLICT (habit_id, log_date)
                DO UPDATE SET value=habit_logs.value + EXCLUDED.value, note=EXCLUDED.note""",
            (row[0], user_id, day, value, note, database.now_str()),
        )
        c.execute(f"SELECT id FROM habit_logs WHERE habit_id={p} AND log_date={p}", (row[0], day))
        log_id = c.fetchone()[0]
    else:
        c.execute(
            """INSERT INTO habit_logs (habit_id, user_id, log_date, value, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(habit_id, log_date)
               DO UPDATE SET value=habit_logs.value + excluded.value, note=excluded.note""",
            (row[0], user_id, day, value, note, database.now_str()),
        )
        c.execute("SELECT id FROM habit_logs WHERE habit_id=? AND log_date=?", (row[0], day))
        log_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    record_history(user_id, "log_habit", "habit_log", log_id, reversible=True)
    return log_id


def get_habits(user_id, day=None):
    target_day = day or database.local_now().strftime("%Y-%m-%d")
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"""SELECT h.id, h.name, h.frequency, h.target_count, COALESCE(l.value, 0)
            FROM habits h LEFT JOIN habit_logs l ON l.habit_id=h.id AND l.log_date={p}
            WHERE h.user_id={p} AND h.active=1 ORDER BY h.name""",
        (target_day, user_id),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def create_goal(user_id, title, target_date=None, steps=None):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    goal_id = _insert_id(
        c,
        f"INSERT INTO goals (user_id, title, target_date, created_at) VALUES ({p}, {p}, {p}, {p})",
        (user_id, title, target_date, database.now_str()),
    )
    for step in steps or []:
        c.execute(
            f"INSERT INTO goal_steps (goal_id, text, created_at) VALUES ({p}, {p}, {p})",
            (goal_id, str(step), database.now_str()),
        )
    conn.commit()
    conn.close()
    record_history(user_id, "create_goal", "goal", goal_id, reversible=True)
    return goal_id


def get_goals(user_id):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"""SELECT g.id, g.title, g.target_date, g.status, g.progress,
                   COUNT(s.id), COALESCE(SUM(s.completed), 0)
            FROM goals g LEFT JOIN goal_steps s ON s.goal_id=g.id
            WHERE g.user_id={p} AND g.status='active'
            GROUP BY g.id, g.title, g.target_date, g.status, g.progress ORDER BY g.id DESC""",
        (user_id,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def add_important_date(user_id, title, event_date, recurring=True, lead_days=7):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    item_id = _insert_id(
        c,
        f"""INSERT INTO important_dates
            (user_id, title, event_date, recurring, lead_days, created_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p})""",
        (user_id, title, event_date, int(bool(recurring)), lead_days, database.now_str()),
    )
    conn.commit()
    conn.close()
    return item_id


def get_upcoming_dates(user_id, days=30):
    today = database.local_now().date()
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"SELECT id, title, event_date, recurring, lead_days FROM important_dates WHERE user_id={p}",
        (user_id,),
    )
    result = []
    for row in c.fetchall():
        try:
            original = datetime.strptime(row[2], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        candidate = original
        if row[3]:
            candidate = original.replace(year=today.year)
            if candidate < today:
                candidate = candidate.replace(year=today.year + 1)
        delta = (candidate - today).days
        if 0 <= delta <= days:
            result.append((*row, candidate.isoformat(), delta))
    conn.close()
    return sorted(result, key=lambda item: item[-1])


def add_document(user_id, title, file_type, telegram_file_id, text, private=False):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    try:
        document_id = _insert_id(
            c,
            f"""INSERT INTO documents
                (user_id, title, file_type, telegram_file_id, content_hash, private, created_at)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})""",
            (user_id, title, file_type, telegram_file_id, digest, int(bool(private)), database.now_str()),
        )
    except Exception:
        conn.rollback()
        c.execute(
            f"SELECT id FROM documents WHERE user_id={p} AND content_hash={p}",
            (user_id, digest),
        )
        row = c.fetchone()
        conn.close()
        return row[0] if row else None, False
    chunks = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= 1800:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, 1800)
        if split_at < 900:
            split_at = remaining.rfind(" ", 0, 1800)
        if split_at < 900:
            split_at = 1800
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    for index, chunk in enumerate(chunks):
        c.execute(
            f"INSERT INTO document_chunks (document_id, user_id, chunk_index, content) VALUES ({p}, {p}, {p}, {p})",
            (document_id, user_id, index, chunk),
        )
    conn.commit()
    conn.close()
    return document_id, True


def list_documents(user_id):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"""SELECT d.id, d.title, d.file_type, d.private, d.created_at, COUNT(c.id)
            FROM documents d LEFT JOIN document_chunks c ON c.document_id=d.id
            WHERE d.user_id={p} GROUP BY d.id, d.title, d.file_type, d.private, d.created_at
            ORDER BY d.id DESC""",
        (user_id,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def search_documents(user_id, query, limit=6):
    terms = [term.lower() for term in query.split() if len(term) > 2][:6]
    if not terms:
        return []
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    clauses = " OR ".join([f"LOWER(c.content) LIKE {p}" for _ in terms])
    params = [user_id] + [f"%{term}%" for term in terms]
    sql = f"""SELECT d.title, c.chunk_index, c.content
              FROM document_chunks c JOIN documents d ON d.id=c.document_id
              WHERE c.user_id={p} AND ({clauses}) ORDER BY d.id DESC, c.chunk_index LIMIT {int(limit)}"""
    c.execute(sql, tuple(params))
    rows = c.fetchall()
    conn.close()
    return rows


def delete_document(user_id, document_id):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(f"SELECT id FROM documents WHERE id={p} AND user_id={p}", (document_id, user_id))
    if not c.fetchone():
        conn.close()
        return False
    c.execute(f"DELETE FROM document_chunks WHERE document_id={p}", (document_id,))
    c.execute(f"DELETE FROM documents WHERE id={p} AND user_id={p}", (document_id, user_id))
    conn.commit()
    conn.close()
    return True


def set_budget(user_id, category, currency, monthly_limit, alert_percent=80):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    params = (user_id, category, currency, monthly_limit, alert_percent, database.now_str())
    if database.DATABASE_URL:
        c.execute(
            f"""INSERT INTO budgets (user_id, category, currency, monthly_limit, alert_percent, created_at)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p})
                ON CONFLICT (user_id, category, currency)
                DO UPDATE SET monthly_limit=EXCLUDED.monthly_limit, alert_percent=EXCLUDED.alert_percent, active=1""",
            params,
        )
    else:
        c.execute(
            """INSERT INTO budgets (user_id, category, currency, monthly_limit, alert_percent, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, category, currency)
               DO UPDATE SET monthly_limit=excluded.monthly_limit, alert_percent=excluded.alert_percent, active=1""",
            params,
        )
    conn.commit()
    conn.close()


def get_budget_status(user_id):
    month = database.local_now().strftime("%Y-%m")
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"""SELECT b.category, b.currency, b.monthly_limit, b.alert_percent,
                   COALESCE(SUM(e.amount), 0)
            FROM budgets b LEFT JOIN expenses e
              ON e.user_id=b.user_id AND LOWER(COALESCE(e.category, 'otros'))=LOWER(b.category)
              AND e.currency=b.currency AND e.created_at LIKE {p}
            WHERE b.user_id={p} AND b.active=1
            GROUP BY b.category, b.currency, b.monthly_limit, b.alert_percent
            ORDER BY b.category""",
        (f"{month}%", user_id),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def find_duplicate_expense(user_id, amount, description, currency, hours=24):
    since = (database.local_now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"""SELECT id, description, created_at FROM expenses
            WHERE user_id={p} AND amount={p} AND currency={p} AND created_at>={p}
              AND LOWER(COALESCE(description, ''))=LOWER({p})
            ORDER BY id DESC LIMIT 1""",
        (user_id, amount, currency, since, description),
    )
    row = c.fetchone()
    conn.close()
    return row


def add_subscription(user_id, name, amount, currency, next_due, frequency="monthly", category="servicios"):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    subscription_id = _insert_id(
        c,
        f"""INSERT INTO subscriptions
            (user_id, name, amount, currency, category, next_due, frequency, created_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})""",
        (user_id, name, amount, currency, category, next_due, frequency, database.now_str()),
    )
    conn.commit()
    conn.close()
    return subscription_id


def get_subscriptions(user_id, active=True):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"""SELECT id, name, amount, currency, category, next_due, frequency
            FROM subscriptions WHERE user_id={p} AND active={p} ORDER BY next_due""",
        (user_id, int(bool(active))),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def get_due_subscriptions(user_id, days=7):
    today = database.local_now().date()
    limit = today + timedelta(days=days)
    return [
        row for row in get_subscriptions(user_id)
        if today <= datetime.strptime(row[5], "%Y-%m-%d").date() <= limit
    ]


def advance_subscription(user_id, subscription_id):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"SELECT next_due, frequency FROM subscriptions WHERE id={p} AND user_id={p} AND active=1",
        (subscription_id, user_id),
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    current = datetime.strptime(row[0], "%Y-%m-%d").date()
    if row[1] == "weekly":
        next_due = current + timedelta(days=7)
    elif row[1] == "yearly":
        next_due = current.replace(year=current.year + 1)
    else:
        year = current.year + (1 if current.month == 12 else 0)
        month = 1 if current.month == 12 else current.month + 1
        next_due = current.replace(year=year, month=month, day=min(current.day, monthrange(year, month)[1]))
    c.execute(
        f"UPDATE subscriptions SET next_due={p} WHERE id={p} AND user_id={p}",
        (next_due.isoformat(), subscription_id, user_id),
    )
    conn.commit()
    conn.close()
    return next_due.isoformat()


def add_expense_items(expense_id, items):
    if not items:
        return
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    for item in items[:100]:
        try:
            total = float(item.get("total") or item.get("amount") or 0)
            quantity = float(item.get("quantity") or 1)
            unit_price = item.get("unit_price")
            unit_price = float(unit_price) if unit_price is not None else None
        except (TypeError, ValueError):
            continue
        if total <= 0:
            continue
        c.execute(
            f"""INSERT INTO expense_items (expense_id, description, quantity, unit_price, total)
                VALUES ({p}, {p}, {p}, {p}, {p})""",
            (expense_id, str(item.get("description") or "producto")[:200], quantity, unit_price, total),
        )
    conn.commit()
    conn.close()


def get_monthly_expense_comparison(user_id):
    now = database.local_now()
    current_month = now.strftime("%Y-%m")
    previous_date = (now.replace(day=1) - timedelta(days=1))
    previous_month = previous_date.strftime("%Y-%m")
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    result = {}
    for label, month in (("current", current_month), ("previous", previous_month)):
        c.execute(
            f"""SELECT currency, SUM(amount) FROM expenses
                WHERE user_id={p} AND created_at LIKE {p}
                GROUP BY currency""",
            (user_id, f"{month}%"),
        )
        result[label] = dict(c.fetchall())
    conn.close()
    return result


def get_expense_export_rows(user_id):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"""SELECT created_at, description, category, amount, currency
            FROM expenses WHERE user_id={p} ORDER BY created_at""",
        (user_id,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def share_resource(owner_user_id, resource_type, resource_id, member_user_id, permission="edit"):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    params = (owner_user_id, resource_type, resource_id, member_user_id, permission, database.now_str())
    if database.DATABASE_URL:
        c.execute(
            f"""INSERT INTO shares
                (owner_user_id, resource_type, resource_id, member_user_id, permission, created_at)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p})
                ON CONFLICT (resource_type, resource_id, member_user_id)
                DO UPDATE SET permission=EXCLUDED.permission""",
            params,
        )
    else:
        c.execute(
            """INSERT INTO shares
               (owner_user_id, resource_type, resource_id, member_user_id, permission, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(resource_type, resource_id, member_user_id)
               DO UPDATE SET permission=excluded.permission""",
            params,
        )
    conn.commit()
    conn.close()


def get_shared_resources(user_id):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"""SELECT s.id, s.resource_type, s.resource_id, s.permission, s.owner_user_id,
                   CASE WHEN s.resource_type='task_list' THEN t.name ELSE r.text END
            FROM shares s
            LEFT JOIN task_lists t ON s.resource_type='task_list' AND t.id=s.resource_id
            LEFT JOIN reminders r ON s.resource_type='reminder' AND r.id=s.resource_id
            WHERE s.member_user_id={p} ORDER BY s.id DESC""",
        (user_id,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def start_meeting(user_id, title):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    meeting_id = _insert_id(
        c,
        f"INSERT INTO meetings (user_id, title, started_at) VALUES ({p}, {p}, {p})",
        (user_id, title, database.now_str()),
    )
    conn.commit()
    conn.close()
    return meeting_id


def get_active_meeting(user_id):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"SELECT id, title, started_at FROM meetings "
        f"WHERE user_id={p} AND status='active' ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = c.fetchone()
    conn.close()
    return row


def add_meeting_item(user_id, content, item_type="note", assignee=None, due_date=None):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"SELECT id FROM meetings WHERE user_id={p} AND status='active' ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    meeting = c.fetchone()
    if not meeting:
        conn.close()
        return None
    item_id = _insert_id(
        c,
        f"""INSERT INTO meeting_items
            (meeting_id, item_type, content, assignee, due_date, created_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p})""",
        (meeting[0], item_type, content, assignee, due_date, database.now_str()),
    )
    conn.commit()
    conn.close()
    return item_id


def end_meeting(user_id, summary=None):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"SELECT id, title FROM meetings WHERE user_id={p} AND status='active' ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    meeting = c.fetchone()
    if not meeting:
        conn.close()
        return None
    c.execute(
        f"UPDATE meetings SET status='completed', ended_at={p}, summary={p} WHERE id={p}",
        (database.now_str(), summary, meeting[0]),
    )
    c.execute(
        f"""SELECT item_type, content, assignee, due_date FROM meeting_items
            WHERE meeting_id={p} ORDER BY id""",
        (meeting[0],),
    )
    items = c.fetchall()
    conn.commit()
    conn.close()
    return meeting[0], meeting[1], items


def set_meeting_summary(user_id, meeting_id, summary):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"UPDATE meetings SET summary={p} WHERE id={p} AND user_id={p}",
        (summary, meeting_id, user_id),
    )
    conn.commit()
    changed = c.rowcount
    conn.close()
    return changed


def record_provider_usage(provider, operation, status, user_id=None):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"""INSERT INTO provider_usage (user_id, provider, operation, status, created_at)
            VALUES ({p}, {p}, {p}, {p}, {p})""",
        (user_id, provider, operation, status, database.now_str()),
    )
    conn.commit()
    conn.close()


def get_cached_response(cache_key):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"SELECT response, expires_at FROM ai_cache WHERE cache_key={p}",
        (cache_key,),
    )
    row = c.fetchone()
    conn.close()
    if not row or row[1] < database.now_str():
        return None
    return row[0]


def cache_response(cache_key, response, ttl_minutes=60):
    expires_at = (database.local_now() + timedelta(minutes=ttl_minutes)).strftime("%Y-%m-%d %H:%M")
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    params = (cache_key, response, expires_at, database.now_str())
    if database.DATABASE_URL:
        c.execute(
            f"""INSERT INTO ai_cache (cache_key, response, expires_at, created_at)
                VALUES ({p}, {p}, {p}, {p})
                ON CONFLICT (cache_key)
                DO UPDATE SET response=EXCLUDED.response, expires_at=EXCLUDED.expires_at""",
            params,
        )
    else:
        c.execute(
            """INSERT INTO ai_cache (cache_key, response, expires_at, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(cache_key)
               DO UPDATE SET response=excluded.response, expires_at=excluded.expires_at""",
            params,
        )
    conn.commit()
    conn.close()


def record_backup(user_id, file_name, status="created"):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"INSERT INTO backups (user_id, file_name, status, created_at) VALUES ({p}, {p}, {p}, {p})",
        (user_id, file_name, status, database.now_str()),
    )
    conn.commit()
    conn.close()


def get_last_backup(user_id):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"SELECT file_name, status, created_at FROM backups WHERE user_id={p} ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = c.fetchone()
    conn.close()
    return row


def record_system_check(check_name, status, details=None):
    conn = database.get_conn()
    c = conn.cursor()
    p = _ph()
    c.execute(
        f"""INSERT INTO system_checks (check_name, status, details, checked_at)
            VALUES ({p}, {p}, {p}, {p})""",
        (check_name, status, details, database.now_str()),
    )
    conn.commit()
    conn.close()


def get_system_status():
    conn = database.get_conn()
    c = conn.cursor()
    c.execute(
        """SELECT s.check_name, s.status, s.details, s.checked_at
           FROM system_checks s
           JOIN (SELECT check_name, MAX(id) AS max_id FROM system_checks GROUP BY check_name) latest
             ON latest.max_id=s.id ORDER BY s.check_name"""
    )
    rows = c.fetchall()
    conn.close()
    return rows
