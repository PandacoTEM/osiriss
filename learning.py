import json
import logging
from datetime import datetime
from database import get_conn, DATABASE_URL

def _placeholders(n):
    return "%s" if DATABASE_URL else "?"

def record_action(user_id, action_type, details=""):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if action_type == "crear_recordatorio":
        if "lead_minutes" in details:
            minutes = details.split("lead_minutes=")
            if len(minutes) > 1:
                try:
                    lead = int(minutes[1].split()[0])
                    if lead >= 120:
                        _upsert_pattern(c, user_id, "prioridad_alta", "lead>=120", str(lead), now, conn)
                    elif lead >= 60:
                        _upsert_pattern(c, user_id, "prioridad_media", "lead>=60", str(lead), now, conn)
                except ValueError:
                    pass
    elif action_type in ("registrar_gasto", "record_expense"):
        try:
            data = json.loads(details) if details.startswith("{") else {}
            if isinstance(data, dict):
                cat = data.get("category", "otros")
                _upsert_pattern(c, user_id, "gasto_categoria", cat, cat, now, conn)
        except (json.JSONDecodeError, AttributeError):
            pass
    elif action_type == "crear_evento":
        _upsert_pattern(c, user_id, "evento", "creado", "1", now, conn)
    elif action_type in ("buscar_internet", "search"):
        _upsert_pattern(c, user_id, "busqueda", "internet", "1", now, conn)
    conn.close()

def _upsert_pattern(c, user_id, ptype, pkey, pvalue, now, conn):
    if DATABASE_URL:
        c.execute("""
            INSERT INTO learning_patterns (user_id, pattern_type, pattern_key, pattern_value, frequency, last_observed)
            VALUES (%s, %s, %s, %s, 1, %s)
            ON CONFLICT (user_id, pattern_type, pattern_key)
            DO UPDATE SET frequency = learning_patterns.frequency + 1, last_observed = %s,
                          confidence = LEAST(learning_patterns.confidence + 0.05, 1.0)
        """, (user_id, ptype, pkey, pvalue, now, now))
    else:
        c.execute("SELECT confidence FROM learning_patterns WHERE user_id=? AND pattern_type=? AND pattern_key=?", (user_id, ptype, pkey))
        existing = c.fetchone()
        if existing:
            new_conf = min(existing[0] + 0.05, 1.0)
            c.execute("UPDATE learning_patterns SET frequency=frequency+1, last_observed=?, confidence=? WHERE user_id=? AND pattern_type=? AND pattern_key=?", (now, new_conf, user_id, ptype, pkey))
        else:
            c.execute("INSERT INTO learning_patterns (user_id, pattern_type, pattern_key, pattern_value, frequency, last_observed) VALUES (?, ?, ?, ?, 1, ?)", (user_id, ptype, pkey, pvalue, now))
    conn.commit()

def get_patterns(user_id, min_confidence=0.3):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"SELECT pattern_type, pattern_key, pattern_value, frequency, last_observed, confidence FROM learning_patterns WHERE user_id = {_placeholders(1)} AND confidence >= {_placeholders(2)} ORDER BY confidence DESC, frequency DESC", (user_id, min_confidence))
    rows = c.fetchall()
    conn.close()
    return rows

def get_insights(user_id):
    patterns = get_patterns(user_id, min_confidence=0.2)
    if not patterns:
        return "Todavía no tengo suficientes datos para encontrar patrones, jefe."
    lines = ["\U0001f9e0 *Esto he aprendido de vos:*\n"]
    for ptype, pkey, pval, freq, last_seen, conf in patterns[:10]:
        emoji = {"prioridad_alta": "\U0001f534", "prioridad_media": "\U0001f7e1", "gasto_categoria": "\U0001f4b0", "evento": "\U0001f4c5", "busqueda": "\U0001f50d"}.get(ptype, "\U0001f4ad")
        label = {"prioridad_alta": "Recordatorios importantes", "prioridad_media": "Recordatorios con aviso", "gasto_categoria": "Gastos frecuentes", "evento": "Eventos de calendario", "busqueda": "Búsquedas en internet"}.get(ptype, ptype)
        lines.append(f"{emoji} *{label}*: {pval} ({freq}v, {conf:.0%} confianza)")
    lines.append(f"\n\U0001f4ca *{len(patterns)} patrones detectados*")
    return "\n".join(lines)
