import os
import hmac
import logging
import secrets
import threading
from collections import defaultdict, deque
from time import monotonic
from dotenv import load_dotenv
from flask import Flask, request, render_template_string, redirect, session

load_dotenv()

from database import init_db, deactivate_by_id, get_task_lists, get_list_items, toggle_task_item, delete_task_item, get_conn, DATABASE_URL
from ai_handler import summarize_research
from web_search import search_results

app = Flask(__name__)
PASSWORD = os.getenv("DASHBOARD_PASSWORD")
EMA_API_KEY = os.getenv("EMA_API_KEY", "")
EMA_RATE_LIMIT = max(1, int(os.getenv("EMA_RATE_LIMIT_PER_MINUTE", "20")))
_ema_request_times = defaultdict(deque)
_ema_rate_lock = threading.Lock()
_ema_answer_cache = {}
_ema_cache_lock = threading.Lock()
EMA_CACHE_TTL_SECONDS = 1800
EMA_CACHE_MAX_ENTRIES = 100
app.secret_key = os.getenv("DASHBOARD_SESSION_SECRET") or os.getenv("TELEGRAM_WEBHOOK_SECRET") or secrets.token_bytes(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=bool(DATABASE_URL),
)

HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Osiris Dashboard</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
body { background: #0f0f1a; color: #e0e0e0; padding: 20px; }
.card { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 12px; }
.table { color: #e0e0e0; }
.table th { border-color: #2a2a4a; color: #888; }
.table td { border-color: #2a2a4a; vertical-align: middle; }
.badge-recurring { background: #2d6a4f; }
.badge-once { background: #5a5a7a; }
.badge-active { background: #1b5e20; }
.badge-inactive { background: #5a1a1a; }
.btn-sm { font-size: 0.8rem; }
h1 { color: #c0c0ff; font-weight: 300; }
.filter-btn { color: #aaa; text-decoration: none; margin-right: 10px; }
.filter-btn.active { color: #c0c0ff; font-weight: bold; }
.nav-tabs .nav-link { color: #888; }
.nav-tabs .nav-link.active { color: #c0c0ff; background: transparent; border-color: #2a2a4a; }
.nav-tabs { border-bottom-color: #2a2a4a; }
.check-btn { text-decoration: none; }
</style>
</head>
<body>
<div class="container">
<h1>\U0001f9e0 Osiris Dashboard</h1>
<div class="d-flex justify-content-between align-items-center mb-4">
<p class="text-secondary mb-0">Panel de control</p>
<form method="POST" action="/logout">
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">
<button class="btn btn-outline-light btn-sm" type="submit">Salir</button>
</form>
</div>

<ul class="nav nav-tabs mb-3">
<li class="nav-item"><a class="nav-link {% if tab=='reminders' %}active{% endif %}" href="/?tab=reminders">\U0001f514 Recordatorios</a></li>
<li class="nav-item"><a class="nav-link {% if tab=='tasks' %}active{% endif %}" href="/?tab=tasks">\U0001f4cb Tareas</a></li>
</ul>

{% if tab=='reminders' %}
<div class="mb-3">
<a href="/?tab=reminders&filter=all" class="filter-btn {% if f=='all' %}active{% endif %}">Todos</a>
<a href="/?tab=reminders&filter=active" class="filter-btn {% if f=='active' %}active{% endif %}">Activos</a>
<a href="/?tab=reminders&filter=inactive" class="filter-btn {% if f=='inactive' %}active{% endif %}">Inactivos</a>
</div>
<div class="card p-3">
<div class="table-responsive">
<table class="table table-sm mb-0">
<thead><tr>
<th>ID</th><th>Usuario</th><th>Texto</th><th>Fecha</th><th>Recurrencia</th><th>Estado</th><th>Entrega</th><th>Acci\u00f3n</th>
</tr></thead>
<tbody>
{% for r in reminders %}
<tr>
<td>{{ r.id }}</td>
<td>{{ r.user_id }}</td>
<td>{{ r.text[:50] }}{% if r.text|length>50 %}...{% endif %}</td>
<td>{{ r.datetime }}</td>
<td>{% if r.recurring %}<span class="badge badge-recurring">{{ r.recurring }}</span>{% else %}<span class="badge badge-once">\u00fanica</span>{% endif %}</td>
<td>{% if r.active %}<span class="badge badge-active">activo</span>{% else %}<span class="badge badge-inactive">inactivo</span>{% endif %}</td>
<td>{{ r.delivery_status or 'pending' }}{% if r.delivery_attempts %} ({{ r.delivery_attempts }}){% endif %}</td>
<td>
<a href="/edit/{{ r.id }}" class="btn btn-outline-light btn-sm">\u270f\ufe0f</a>
<form method="POST" action="/delete/{{ r.id }}" class="d-inline" onsubmit="return confirm('\u00bfEliminar?')">
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">
<button type="submit" class="btn btn-outline-danger btn-sm">\U0001f5d1\ufe0f</button>
</form>
</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
</div>
{% else %}
{% for lst in task_lists %}
<div class="card p-3 mb-3">
<h5 class="mb-3">{{ lst.name }} <small class="text-secondary">({{ lst.done }}/{{ lst.total }})</small></h5>
{% if lst.items %}
<table class="table table-sm mb-0">
<tbody>
{% for item in lst.items %}
<tr>
<td style="width:40px">
<form method="POST" action="/task_toggle/{{ item.id }}">
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">
<button type="submit" class="btn btn-link check-btn p-0">{% if item.completed %}\u2705{% else %}\u26ab{% endif %}</button>
</form>
</td>
<td class="{% if item.completed %}text-decoration-line-through text-secondary{% endif %}">
{{ item.text }}
{% if item.priority == 1 %}<span class="badge bg-warning text-dark">\u203c\ufe0f</span>{% elif item.priority == 2 %}<span class="badge bg-danger">\U0001f6a8</span>{% endif %}
{% if item.tags %}<small class="text-secondary">#{{ item.tags }}</small>{% endif %}
</td>
<td style="width:40px">
<form method="POST" action="/task_delete/{{ item.id }}" onsubmit="return confirm('\u00bfEliminar?')">
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">
<button type="submit" class="btn btn-outline-danger btn-sm py-0">\U0001f5d1\ufe0f</button>
</form>
</td>
</tr>
{% endfor %}
</tbody>
</table>
{% else %}
<p class="text-secondary mb-0">Vac\u00eda</p>
{% endif %}
</div>
{% endfor %}
{% endif %}
</div>
</body>
</html>"""

EDIT_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Editar recordatorio - Osiris</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
body { background: #0f0f1a; color: #e0e0e0; padding: 20px; }
.card { background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 12px; }
.form-control, .form-select { background: #0f0f1a; color: #e0e0e0; border-color: #2a2a4a; }
.form-control:focus, .form-select:focus { background: #0f0f1a; color: #e0e0e0; border-color: #6a6aff; box-shadow: none; }
</style>
</head>
<body>
<div class="container">
<h1>\u270f\ufe0f Editar recordatorio</h1>
<div class="card p-4 mt-3">
<form method="POST">
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">
<div class="mb-3">
<label class="form-label">Texto</label>
<input name="text" class="form-control" value="{{ r.text }}" required>
</div>
<div class="mb-3">
<label class="form-label">Fecha y hora (YYYY-MM-DD HH:MM)</label>
<input name="datetime" class="form-control" value="{{ r.datetime }}" required>
</div>
<div class="mb-3">
<label class="form-label">Recurrencia</label>
<select name="recurring" class="form-select">
<option value="">\u00danica</option>
<option {% if r.recurring=='daily' %}selected{% endif %} value="daily">Diaria</option>
<option {% if r.recurring=='weekly' %}selected{% endif %} value="weekly">Semanal</option>
<option {% if r.recurring=='monthly' %}selected{% endif %} value="monthly">Mensual</option>
<option {% if r.recurring=='weekdays' %}selected{% endif %} value="weekdays">D\u00edas de semana</option>
</select>
</div>
<button type="submit" class="btn btn-primary">Guardar</button>
<a href="/" class="btn btn-outline-light ms-2">Cancelar</a>
</form>
</div>
</div>
</body>
</html>"""

LOGIN_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Acceso - Osiris</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
body { background: #0f0f1a; color: #e0e0e0; min-height: 100vh; display: grid; place-items: center; }
.login-panel { width: min(92vw, 360px); }
.form-control { background: #1a1a2e; color: #e0e0e0; border-color: #2a2a4a; }
.form-control:focus { background: #1a1a2e; color: #e0e0e0; border-color: #6a6aff; box-shadow: none; }
</style>
</head>
<body>
<main class="login-panel">
<h1 class="h3 mb-3">Osiris Dashboard</h1>
{% if error %}<div class="alert alert-danger">{{ error }}</div>{% endif %}
<form method="POST">
<label class="form-label" for="password">Contrase\u00f1a</label>
<input id="password" name="password" type="password" class="form-control mb-3" required autofocus>
<button class="btn btn-primary w-100" type="submit">Entrar</button>
</form>
</main>
</body>
</html>"""

def check_auth():
    return bool(PASSWORD and session.get("authenticated"))

def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token

def check_csrf():
    expected = session.get("csrf_token", "")
    provided = request.form.get("csrf_token", "")
    return bool(expected and hmac.compare_digest(expected, provided))

def check_ema_api_auth():
    header = request.headers.get("Authorization", "")
    provided = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    return bool(EMA_API_KEY and provided and hmac.compare_digest(provided, EMA_API_KEY))

def within_ema_rate_limit(client_id):
    now = monotonic()
    with _ema_rate_lock:
        timestamps = _ema_request_times[client_id]
        while timestamps and now - timestamps[0] >= 60:
            timestamps.popleft()
        if len(timestamps) >= EMA_RATE_LIMIT:
            return False
        timestamps.append(now)
        return True

def format_ema_research_answer(report):
    if isinstance(report, str):
        return report.strip()[:6000]
    if not isinstance(report, dict):
        raise ValueError("Formato de respuesta de IA no soportado")

    summary = str(report.get("summary") or "").strip()
    points = report.get("key_points") or []
    limitations = str(report.get("limitations") or "").strip()
    if not summary:
        raise ValueError("La respuesta de IA no contiene resumen")

    sections = [summary]
    if isinstance(points, list):
        clean_points = [str(point).strip() for point in points if str(point).strip()]
        if clean_points:
            sections.append("Puntos clave:\n" + "\n".join(f"- {point}" for point in clean_points[:5]))
    if limitations:
        sections.append(f"Alcance: {limitations}")
    return "\n\n".join(sections)[:6000]

def get_cached_ema_answer(message):
    key = message.casefold()
    now = monotonic()
    with _ema_cache_lock:
        cached = _ema_answer_cache.get(key)
        if not cached:
            return None
        created_at, payload = cached
        if now - created_at >= EMA_CACHE_TTL_SECONDS:
            _ema_answer_cache.pop(key, None)
            return None
        return payload

def cache_ema_answer(message, payload):
    key = message.casefold()
    with _ema_cache_lock:
        if len(_ema_answer_cache) >= EMA_CACHE_MAX_ENTRIES:
            oldest_key = min(_ema_answer_cache, key=lambda item: _ema_answer_cache[item][0])
            _ema_answer_cache.pop(oldest_key, None)
        _ema_answer_cache[key] = (monotonic(), payload)

@app.route("/login", methods=["GET", "POST"])
def login():
    if not PASSWORD:
        return "Dashboard deshabilitado: configura DASHBOARD_PASSWORD", 503
    if request.method == "POST":
        provided = request.form.get("password", "")
        if hmac.compare_digest(provided, PASSWORD):
            session.clear()
            session["authenticated"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect("/")
        return render_template_string(LOGIN_HTML, error="Contrase\u00f1a incorrecta"), 401
    if check_auth():
        return redirect("/")
    return render_template_string(LOGIN_HTML, error=None)

@app.route("/logout", methods=["POST"])
def logout():
    if not check_auth() or not check_csrf():
        return "Acceso denegado", 403
    session.clear()
    return redirect("/login")

@app.route("/health")
def health():
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT 1")
        c.fetchone()
        conn.close()
        return {"status": "ok", "database": "ok"}, 200
    except Exception:
        return {"status": "degraded", "database": "error"}, 503

@app.route("/api/v1/ema/chat", methods=["POST"])
def ema_chat():
    if not check_ema_api_auth():
        return {"error": "unauthorized"}, 401
    client_id = request.headers.get("X-EMA-Device", request.remote_addr or "unknown")[:100]
    if not within_ema_rate_limit(client_id):
        return {"error": "rate_limit"}, 429

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return {"error": "invalid_json"}, 400
    message = " ".join(str(payload.get("message") or "").split())
    if not message or len(message) > 500:
        return {"error": "invalid_message"}, 400

    try:
        cached = get_cached_ema_answer(message)
        if cached:
            return cached, 200

        sources = search_results(message, max_results=3)
        if not sources:
            return {
                "answer": "No encontré fuentes suficientes para responder con confianza.",
                "sources": [],
            }, 200
        answer = format_ema_research_answer(
            summarize_research(message, sources, fast=True, prefer_google=True)
        )
        response_payload = {
            "answer": answer,
            "sources": [
                {
                    "title": source["title"][:200],
                    "url": source["href"],
                    "date": source.get("date") or None,
                }
                for source in sources
            ],
        }
        cache_ema_answer(message, response_payload)
        return response_payload, 200
    except Exception:
        logging.exception("Error procesando consulta de EMA")
        return {"error": "provider_unavailable"}, 503

@app.route("/")
def index():
    if not check_auth():
        return redirect("/login")
    tab = request.args.get("tab", "reminders")
    f = request.args.get("filter", "all")
    if tab == "tasks":
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT DISTINCT user_id FROM task_lists")
        user_ids = [r[0] for r in c.fetchall()]
        conn.close()
        task_lists = []
        for uid in user_ids:
            lists = get_task_lists(uid)
            for lid, lname, _ in lists:
                items = get_list_items(lid)
                items_data = [{"id": i[0], "text": i[1], "completed": bool(i[2]), "priority": i[3], "tags": i[4]} for i in items]
                done = sum(1 for i in items if i[2])
                task_lists.append({"name": lname, "items": items_data, "done": done, "total": len(items)})
        return render_template_string(
            HTML,
            tab=tab,
            task_lists=task_lists,
            csrf_token=get_csrf_token(),
        )
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, user_id, text, datetime, recurring, active, delivery_status, delivery_attempts FROM reminders ORDER BY datetime DESC LIMIT 100")
    rows = [dict(id=r[0], user_id=r[1], text=r[2], datetime=r[3], recurring=r[4], active=r[5], delivery_status=r[6], delivery_attempts=r[7]) for r in c.fetchall()]
    conn.close()
    if f == "active":
        rows = [r for r in rows if r["active"]]
    elif f == "inactive":
        rows = [r for r in rows if not r["active"]]
    return render_template_string(
        HTML,
        reminders=rows,
        f=f,
        tab=tab,
        task_lists=[],
        csrf_token=get_csrf_token(),
    )

@app.route("/task_toggle/<int:item_id>", methods=["POST"])
def task_toggle(item_id):
    if not check_auth():
        return redirect("/login")
    if not check_csrf():
        return "Solicitud invalida", 403
    toggle_task_item(item_id)
    return redirect("/?tab=tasks")

@app.route("/task_delete/<int:item_id>", methods=["POST"])
def task_delete(item_id):
    if not check_auth():
        return redirect("/login")
    if not check_csrf():
        return "Solicitud invalida", 403
    delete_task_item(item_id)
    return redirect("/?tab=tasks")

@app.route("/edit/<int:rid>", methods=["GET", "POST"])
def edit(rid):
    if not check_auth():
        return redirect("/login")
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, text, datetime, recurring FROM reminders WHERE id=%s" if DATABASE_URL else "SELECT id, text, datetime, recurring FROM reminders WHERE id=?", (rid,))
    row = c.fetchone()
    conn.close()
    if not row:
        return "No encontrado", 404
    r = {"id": row[0], "text": row[1], "datetime": row[2], "recurring": row[3]}
    if request.method == "POST":
        if not check_csrf():
            return "Solicitud invalida", 403
        text = request.form["text"]
        dt = request.form["datetime"]
        recurring = request.form.get("recurring") or None
        conn = get_conn()
        c = conn.cursor()
        c.execute("UPDATE reminders SET text=%s, datetime=%s, recurring=%s, delivery_status='pending', delivery_attempts=0, last_error=NULL WHERE id=%s" if DATABASE_URL else "UPDATE reminders SET text=?, datetime=?, recurring=?, delivery_status='pending', delivery_attempts=0, last_error=NULL WHERE id=?", (text, dt, recurring, rid))
        conn.commit()
        conn.close()
        return redirect("/")
    return render_template_string(EDIT_HTML, r=r, csrf_token=get_csrf_token())

@app.route("/delete/<int:rid>", methods=["POST"])
def delete(rid):
    if not check_auth():
        return redirect("/login")
    if not check_csrf():
        return "Solicitud invalida", 403
    deactivate_by_id(rid)
    return redirect("/")

def run_dashboard():
    init_db()
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
