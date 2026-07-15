import os
import threading
from flask import Flask, request, render_template_string, redirect
from database import get_all_active, get_reminders, deactivate_by_id, update_datetime, get_task_lists, get_list_items, toggle_task_item, search_lists, delete_task_item

app = Flask(__name__)
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "osiris123")

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
<p class="text-secondary mb-4">Panel de control</p>

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
<th>ID</th><th>Usuario</th><th>Texto</th><th>Fecha</th><th>Recurrencia</th><th>Estado</th><th>Acci\u00f3n</th>
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
<td>
<a href="/edit/{{ r.id }}" class="btn btn-outline-light btn-sm">\u270f\ufe0f</a>
<a href="/delete/{{ r.id }}" class="btn btn-outline-danger btn-sm" onclick="return confirm('\u00bfEliminar?')">\U0001f5d1\ufe0f</a>
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
<a href="/task_toggle/{{ item.id }}?pwd={{ pwd }}" class="check-btn">{% if item.completed %}\u2705{% else %}\u26ab{% endif %}</a>
</td>
<td class="{% if item.completed %}text-decoration-line-through text-secondary{% endif %}">
{{ item.text }}
{% if item.priority == 1 %}<span class="badge bg-warning text-dark">\u203c\ufe0f</span>{% elif item.priority == 2 %}<span class="badge bg-danger">\U0001f6a8</span>{% endif %}
{% if item.tags %}<small class="text-secondary">#{{ item.tags }}</small>{% endif %}
</td>
<td style="width:40px">
<a href="/task_delete/{{ item.id }}?pwd={{ pwd }}" class="btn btn-outline-danger btn-sm py-0" onclick="return confirm('\u00bfEliminar?')">\U0001f5d1\ufe0f</a>
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

def check_auth():
    pwd = request.args.get("pwd") or request.form.get("pwd")
    return pwd == PASSWORD

@app.route("/health")
def health():
    return "OK", 200

@app.route("/")
def index():
    if not check_auth():
        return "Acceso denegado", 401
    tab = request.args.get("tab", "reminders")
    f = request.args.get("filter", "all")
    pwd = request.args.get("pwd") or request.form.get("pwd")
    if tab == "tasks":
        from database import DB_PATH
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
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
        return render_template_string(HTML, tab=tab, task_lists=task_lists, pwd=pwd or PASSWORD)
    import sqlite3
    from database import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, user_id, text, datetime, recurring, active FROM reminders ORDER BY datetime DESC LIMIT 100")
    rows = [dict(id=r[0], user_id=r[1], text=r[2], datetime=r[3], recurring=r[4], active=r[5]) for r in c.fetchall()]
    conn.close()
    if f == "active":
        rows = [r for r in rows if r["active"]]
    elif f == "inactive":
        rows = [r for r in rows if not r["active"]]
    return render_template_string(HTML, reminders=rows, f=f, tab=tab, task_lists=[])

@app.route("/task_toggle/<int:item_id>")
def task_toggle(item_id):
    if not check_auth():
        return "Acceso denegado", 401
    toggle_task_item(item_id)
    pwd = request.args.get("pwd") or PASSWORD
    return redirect(f"/?tab=tasks&pwd={pwd}")

@app.route("/task_delete/<int:item_id>")
def task_delete(item_id):
    if not check_auth():
        return "Acceso denegado", 401
    delete_task_item(item_id)
    pwd = request.args.get("pwd") or PASSWORD
    return redirect(f"/?tab=tasks&pwd={pwd}")

@app.route("/edit/<int:rid>", methods=["GET", "POST"])
def edit(rid):
    if not check_auth():
        return "Acceso denegado", 401
    from database import DB_PATH
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, text, datetime, recurring FROM reminders WHERE id=?", (rid,))
    row = c.fetchone()
    conn.close()
    if not row:
        return "No encontrado", 404
    r = {"id": row[0], "text": row[1], "datetime": row[2], "recurring": row[3]}
    if request.method == "POST":
        text = request.form["text"]
        dt = request.form["datetime"]
        recurring = request.form.get("recurring") or None
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE reminders SET text=?, datetime=?, recurring=? WHERE id=?", (text, dt, recurring, rid))
        conn.commit()
        conn.close()
        return redirect(f"/?pwd={PASSWORD}")
    return render_template_string(EDIT_HTML, r=r)

@app.route("/delete/<int:rid>")
def delete(rid):
    if not check_auth():
        return "Acceso denegado", 401
    deactivate_by_id(rid)
    return redirect(f"/?pwd={PASSWORD}")

def run_dashboard():
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)