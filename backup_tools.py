import base64
import hashlib
import json
import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

import database


BACKUP_HEADER = b"OSIRIS-BACKUP-v1\n"
SCHEMA_VERSION = 1

DIRECT_TABLES = (
    ("reminders", "user_id"),
    ("expenses", "user_id"),
    ("activity_log", "user_id"),
    ("chat_history", "user_id"),
    ("task_lists", "user_id"),
    ("learning_patterns", "user_id"),
    ("memories", "user_id"),
    ("user_tokens", "user_id"),
    ("contacts", "owner_user_id"),
    ("user_preferences", "user_id"),
    ("feature_flags", "user_id"),
    ("action_history", "user_id"),
    ("inbox_items", "user_id"),
    ("routines", "user_id"),
    ("habits", "user_id"),
    ("goals", "user_id"),
    ("important_dates", "user_id"),
    ("documents", "user_id"),
    ("budgets", "user_id"),
    ("subscriptions", "user_id"),
    ("meetings", "user_id"),
    ("provider_usage", "user_id"),
)

PARENT_MAPS = {
    "reminders": "reminder",
    "expenses": "expense",
    "task_lists": "task_list",
    "routines": "routine",
    "habits": "habit",
    "goals": "goal",
    "documents": "document",
    "meetings": "meeting",
}

CHILD_TABLES = (
    ("task_items", "list_id", "task_list"),
    ("expense_items", "expense_id", "expense"),
    ("routine_steps", "routine_id", "routine"),
    ("habit_logs", "habit_id", "habit"),
    ("goal_steps", "goal_id", "goal"),
    ("document_chunks", "document_id", "document"),
    ("meeting_items", "meeting_id", "meeting"),
)


def _ph():
    return "%s" if database.DATABASE_URL else "?"


def _cipher():
    secret = (
        os.getenv("OSIRIS_BACKUP_KEY")
        or os.getenv("GOOGLE_TOKEN_ENCRYPTION_KEY")
        or os.getenv("DASHBOARD_SESSION_SECRET")
        or os.getenv("TELEGRAM_WEBHOOK_SECRET")
    )
    if not secret:
        raise RuntimeError("Configura OSIRIS_BACKUP_KEY para cifrar los respaldos")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _rows(cursor, sql, params=()):
    cursor.execute(sql, params)
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _rows_for_ids(cursor, table, column, ids):
    if not ids:
        return []
    placeholders = ", ".join([_ph()] * len(ids))
    return _rows(
        cursor,
        f"SELECT * FROM {table} WHERE {column} IN ({placeholders}) ORDER BY id",
        tuple(ids),
    )


def collect_backup_data(user_id):
    conn = database.get_conn()
    c = conn.cursor()
    tables = {}
    for table, user_column in DIRECT_TABLES:
        tables[table] = _rows(
            c,
            f"SELECT * FROM {table} WHERE {user_column}={_ph()}"
            + (" ORDER BY id" if table not in {"user_tokens", "user_preferences", "feature_flags"} else ""),
            (user_id,),
        )
    for table, parent_column, map_name in CHILD_TABLES:
        parent_table = next(name for name, key in PARENT_MAPS.items() if key == map_name)
        parent_ids = [row["id"] for row in tables[parent_table]]
        tables[table] = _rows_for_ids(c, table, parent_column, parent_ids)
    tables["shares"] = _rows(
        c,
        f"SELECT * FROM shares WHERE owner_user_id={_ph()} OR member_user_id={_ph()} ORDER BY id",
        (user_id, user_id),
    )
    conn.close()
    return {
        "schema_version": SCHEMA_VERSION,
        "owner_user_id": int(user_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tables": tables,
    }


def create_encrypted_backup(user_id):
    payload = collect_backup_data(user_id)
    raw = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
    encrypted = BACKUP_HEADER + _cipher().encrypt(raw)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"osiris_{user_id}_{stamp}.osirisbackup"
    count = sum(len(rows) for rows in payload["tables"].values())
    return encrypted, filename, count


def decrypt_backup(blob, expected_user_id):
    if not blob.startswith(BACKUP_HEADER):
        raise ValueError("El archivo no es un respaldo de Osiris compatible")
    try:
        raw = _cipher().decrypt(blob[len(BACKUP_HEADER):])
    except InvalidToken as exc:
        raise ValueError("No se pudo descifrar el respaldo con la clave actual") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("El respaldo esta danado") from exc
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("La version del respaldo no es compatible")
    if int(payload.get("owner_user_id", 0)) != int(expected_user_id):
        raise ValueError("Este respaldo pertenece a otro usuario")
    if not isinstance(payload.get("tables"), dict):
        raise ValueError("El respaldo no contiene tablas validas")
    return payload


def _delete_user_rows(cursor, user_id):
    p = _ph()
    cursor.execute(f"DELETE FROM shares WHERE owner_user_id={p} OR member_user_id={p}", (user_id, user_id))
    cursor.execute(f"DELETE FROM meeting_items WHERE meeting_id IN (SELECT id FROM meetings WHERE user_id={p})", (user_id,))
    cursor.execute(f"DELETE FROM document_chunks WHERE user_id={p}", (user_id,))
    cursor.execute(f"DELETE FROM goal_steps WHERE goal_id IN (SELECT id FROM goals WHERE user_id={p})", (user_id,))
    cursor.execute(f"DELETE FROM habit_logs WHERE user_id={p}", (user_id,))
    cursor.execute(f"DELETE FROM routine_steps WHERE routine_id IN (SELECT id FROM routines WHERE user_id={p})", (user_id,))
    cursor.execute(f"DELETE FROM expense_items WHERE expense_id IN (SELECT id FROM expenses WHERE user_id={p})", (user_id,))
    cursor.execute(f"DELETE FROM task_items WHERE list_id IN (SELECT id FROM task_lists WHERE user_id={p})", (user_id,))
    for table, user_column in reversed(DIRECT_TABLES):
        cursor.execute(f"DELETE FROM {table} WHERE {user_column}={p}", (user_id,))
    cursor.execute(f"DELETE FROM pending_actions WHERE user_id={p}", (user_id,))


def _insert_row(cursor, table, source_row, old_user_id, new_user_id, maps):
    row = dict(source_row)
    old_id = row.pop("id", None)
    for column in ("user_id", "owner_user_id", "member_user_id"):
        if column in row and row[column] == old_user_id:
            row[column] = new_user_id
    foreign_maps = {
        "list_id": "task_list",
        "expense_id": "expense",
        "routine_id": "routine",
        "habit_id": "habit",
        "goal_id": "goal",
        "document_id": "document",
        "meeting_id": "meeting",
    }
    for column, map_name in foreign_maps.items():
        if column in row:
            row[column] = maps[map_name][row[column]]
    if table == "action_history" and row.get("target_id") is not None:
        target_map = maps.get(row.get("target_type"))
        if target_map:
            row["target_id"] = target_map.get(row["target_id"])
    if table == "shares" and row.get("owner_user_id") == new_user_id:
        resource_map = maps.get(row.get("resource_type"))
        if resource_map:
            row["resource_id"] = resource_map.get(row["resource_id"], row["resource_id"])
    columns = list(row)
    placeholders = ", ".join([_ph()] * len(columns))
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    if old_id is not None and database.DATABASE_URL:
        cursor.execute(sql + " RETURNING id", tuple(row[column] for column in columns))
        return old_id, cursor.fetchone()[0]
    cursor.execute(sql, tuple(row[column] for column in columns))
    return (old_id, cursor.lastrowid) if old_id is not None else (None, None)


def restore_backup_payload(payload, user_id):
    tables = payload["tables"]
    old_user_id = int(payload["owner_user_id"])
    maps = {name: {} for name in PARENT_MAPS.values()}
    conn = database.get_conn()
    c = conn.cursor()
    try:
        _delete_user_rows(c, user_id)
        deferred = {"action_history", "shares"}
        for table, _ in DIRECT_TABLES:
            if table in deferred:
                continue
            for row in tables.get(table, []):
                old_id, new_id = _insert_row(c, table, row, old_user_id, user_id, maps)
                if table in PARENT_MAPS and old_id is not None:
                    maps[PARENT_MAPS[table]][old_id] = new_id
        for table, _, _ in CHILD_TABLES:
            for row in tables.get(table, []):
                _insert_row(c, table, row, old_user_id, user_id, maps)
        for table in ("action_history", "shares"):
            for row in tables.get(table, []):
                _insert_row(c, table, row, old_user_id, user_id, maps)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return sum(len(rows) for rows in tables.values())
