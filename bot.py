import os
import re
import hmac
import asyncio
import base64
import csv
import json
import tempfile
import threading
import logging
import time as time_module
from calendar import monthrange
from collections import defaultdict, deque
from datetime import datetime, timedelta, time
from io import BytesIO, StringIO
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters, ContextTypes

from database import init_db, add_reminder, get_all_active, get_reminders, deactivate_by_id, update_datetime, log_activity, get_today_activity, save_message, get_recent_history, create_task_list, add_task_item, get_task_lists, get_list_items, toggle_task_item, delete_task_list, delete_task_item, search_lists, is_task_list_owner, add_expense, get_today_expenses, authorize_user, deauthorize_user, is_authorized, get_authorized_user_ids, create_auth_code, redeem_auth_code, get_reminder_by_id, search_active_reminders, mark_delivery_attempt, mark_delivered, snooze_reminder, update_reminder_details, create_pending_action, consume_pending_action, remember, get_memories, forget_memory, save_contact, get_contact, get_contacts, delete_contact, delete_user_data, get_conn, DATABASE_URL
from ai_handler import analyze_message, transcribe_audio, answer_question, answer_from_documents, compose_text, summarize_content, analyze_image, ocr_image, generate_chat_response
from web_search import search_raw as web_search_raw
from music_recognizer import recognize as recognize_music
from auth import complete_auth, get_auth_url, is_authenticated, revoke_google_access
from google_tools import create_event, create_gmail_draft, list_events, search_youtube, search_drive
from dashboard import run_dashboard
from learning import record_action, get_insights
from pdf_generator import generate_expense_report, generate_text_pdf, generate_weekly_report
from updates import UPDATES
from backup_tools import collect_backup_data, create_encrypted_backup, decrypt_backup, restore_backup_payload
from features import (
    add_important_date, add_inbox_item, archive_inbox_item, create_goal, create_habit,
    create_routine, add_document, delete_document, feature_enabled, get_goals,
    add_expense_items, add_subscription, advance_subscription, find_duplicate_expense,
    get_budget_status, get_due_subscriptions, get_expense_export_rows, get_habits,
    get_inbox, get_monthly_expense_comparison, get_preference, get_preferences,
    get_routine, get_routines, get_subscriptions, get_upcoming_dates, list_documents,
    add_meeting_item, end_meeting, get_active_meeting, get_last_backup,
    get_shared_resources, get_system_status, log_habit, record_backup, record_history,
    record_system_check, search_documents, set_budget, set_meeting_summary,
    set_preference, share_resource, start_meeting, undo_last_action,
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")
WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL", "https://osiriss.onrender.com/webhook")
ZONE_STR = os.getenv("TIMEZONE") or os.getenv("TZ")
if ZONE_STR:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(ZONE_STR)
else:
    from tzlocal import get_localzone
    TZ = get_localzone()

BOT_USERNAME = "Orisis_diosa_bot"
CREATOR_ID = int(os.getenv("CREATOR_ID", 0))
_auth_notified = set()
_system_alerts = set()
_request_times = defaultdict(deque)

RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
MAX_DELIVERY_ATTEMPTS = int(os.getenv("MAX_DELIVERY_ATTEMPTS", "5"))
MISSED_REMINDER_GRACE_HOURS = int(os.getenv("MISSED_REMINDER_GRACE_HOURS", "24"))

MEMORY_ACTIONS = {"chat", "clarify", "create", "create_search", "create_friend_reminder", "delete", "create_event", "query", "learning_insights", "generate_pdf"}

def save_exchange(user_id, user_msg, bot_response, action):
    if action in MEMORY_ACTIONS and not get_preference(user_id, "private_mode", False):
        save_message(user_id, "user", user_msg)
        save_message(user_id, "assistant", bot_response[:300])

def strip_wake_word(text):
    text = re.sub(r'^(?:hey|oye|eh|ya|oi|ey)?[,:\s]*(?:[Oo]siris)[,:\s]*', '', text).strip()
    text = re.sub(r'@\w+', '', text).strip()
    return text

async def wake_greeting(update: Update):
    await update.message.reply_text("\U0001f9e0 *Osiris* presente, *Jefe*! Dime en qu\u00e9 puedo ayudarte.", parse_mode="Markdown")

def local_now():
    return datetime.now(TZ)

def parse_local(dt_str):
    return datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)

def fmt_local(dt):
    return dt.strftime("%Y-%m-%d %H:%M")

EARLIEST_ALARM_HOUR = 10  # Alarmas antes de esta hora → se mandan a las 9pm del día anterior

def smart_alarm(event_dt, lead_minutes):
    alarm = event_dt - timedelta(minutes=lead_minutes)
    if lead_minutes <= 60:
        return alarm
    if alarm.hour < EARLIEST_ALARM_HOUR:
        alarm = alarm.replace(hour=21, minute=0, second=0) - timedelta(days=1)
    return alarm


def within_rate_limit(user_id):
    now = time_module.monotonic()
    bucket = _request_times[user_id]
    while bucket and now - bucket[0] >= 60:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_PER_MINUTE:
        return False
    bucket.append(now)
    return True


def reminder_job_data(row, attempts=0):
    rid, uid, text, dt_str, recurring, search_q, friend_name, end_date, lead_minutes = row
    return {
        "rid": rid,
        "uid": uid,
        "text": text,
        "recurring": recurring,
        "dt_str": dt_str,
        "search_query": search_q,
        "friend_name": friend_name,
        "end_date": end_date,
        "lead_minutes": lead_minutes or 0,
        "attempts": attempts,
    }


def replace_reminder_job(job_queue, when, data):
    for existing in job_queue.get_jobs_by_name(str(data["rid"])):
        existing.schedule_removal()
    job_queue.run_once(send_reminder, when=when, data=data, name=str(data["rid"]))


def reminder_buttons(reminder_id, recurring=False):
    if recurring:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("Cancelar serie", callback_data=f"reminder:done:{reminder_id}"),
        ]])
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Posponer 10 min", callback_data=f"reminder:snooze:{reminder_id}:10"),
            InlineKeyboardButton("Completar", callback_data=f"reminder:done:{reminder_id}"),
        ]
    ])


def confirmation_buttons(token, confirm_label="Confirmar"):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(confirm_label, callback_data=f"confirm:{token}:yes"),
        InlineKeyboardButton("Cancelar", callback_data=f"confirm:{token}:no"),
    ]])


def parse_amount(raw_value):
    value = re.sub(r"[^\d,.]", "", str(raw_value))
    if not value:
        raise ValueError("monto vacio")
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        decimals = len(value) - value.rfind(",") - 1
        value = value.replace(",", ".") if decimals == 2 else value.replace(",", "")
    elif "." in value:
        decimals = len(value) - value.rfind(".") - 1
        if decimals == 3:
            value = value.replace(".", "")
    return float(value)


def budget_alert_text(user_id, category, currency):
    for budget_category, budget_currency, limit, alert_percent, spent in get_budget_status(user_id):
        if budget_category.lower() == (category or "otros").lower() and budget_currency == currency:
            percent = (spent / limit * 100) if limit else 0
            if percent >= 100:
                return f"\nPresupuesto superado: {spent:,.2f}/{limit:,.2f} {currency}."
            if percent >= alert_percent:
                return f"\nAlerta de presupuesto: llevas {percent:.0f}% en {budget_category}."
    return ""


def extract_receipt_items(text):
    items = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or re.search(r"\b(total|subtotal|impuesto|iva|cambio|pagar)\b", line, re.I):
            continue
        match = re.match(r"^(.{2,80}?)\s+[$\u20a1]?\s*([\d][\d.,]*)$", line)
        if not match:
            continue
        try:
            amount = parse_amount(match.group(2))
        except ValueError:
            continue
        if amount > 0:
            items.append({"description": match.group(1).strip(), "quantity": 1, "total": amount})
    return items[:100]


def synthesize_speech(text):
    from gtts import gTTS

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp:
        path = temp.name
    gTTS(text=text[:3000], lang="es").save(path)
    return path


async def send_voice_reply(update, text):
    path = None
    try:
        path = await asyncio.to_thread(synthesize_speech, text)
        with open(path, "rb") as audio:
            await update.message.reply_voice(voice=audio)
    except Exception as exc:
        logging.warning("No se pudo generar respuesta de voz: %s", exc)
    finally:
        if path and os.path.exists(path):
            os.remove(path)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    is_creator = uid == CREATOR_ID
    authorized = is_creator or is_authorized(uid)
    chat_type = update.effective_chat.type if update.effective_chat else "private"
    if not authorized:
        await update.message.reply_text(
            "\U0001f512 *Acceso denegado.* Necesit\u00e1s autorizaci\u00f3n del creador para usar este bot.\n\n"
            "Si ten\u00e9s un c\u00f3digo de acceso, us\u00e1: /register <c\u00f3digo>",
            parse_mode="Markdown"
        )
        return
    if chat_type in ("group", "supergroup"):
        await update.message.reply_text(
            "\U0001f9e0 *\u00a1Hola! Soy Osiris, tu asistente personal.*\n\n"
            "Estoy aqu\u00ed para ayudarte con recordatorios, tareas, gastos y m\u00e1s.\n\n"
            "\U0001f4ac Menci\u00f3name con @Osiris_bot o di 'Osiris' seguido de tu mensaje.\n"
            "Ej: 'Osiris recu\u00e9rdame X ma\u00f1ana'",
            parse_mode="Markdown"
        )
        return
    name = update.effective_user.first_name or ""
    if is_creator:
        msg = (
            "\U0001f9e0 *\u00a1Hola, Jefe! Soy Osiris.*\n\n"
            "Soy tu asistente personal de recordatorios, apuntes, gastos y m\u00e1s. "
            "Estoy programado para ayudarte a organizar tu d\u00eda a d\u00eda. "
            "\u00a1Es un placer trabajar con vos!\n\n"
            "\U0001f4ac *As\u00ed pod\u00e9s hablarme:*\n"
            '\u2022 "Recu\u00e9rdame llamar al dentista ma\u00f1ana a las 3pm"\n'
            '\u2022 "Cada lunes sacar la basura a las 8pm"\n'
            '\u2022 "C\u00f3mo termin\u00f3 Francia vs Espa\u00f1a?"\n'
            '\u2022 "Agenda cita con el dentista viernes a las 3pm"\n'
            '\u2022 "Compr\u00e9 galletas a 455 colones"\n'
            '\u2022 "Cre\u00e1 una lista de supermercado"\n'
            '\u2022 "Inicia una reuni\u00f3n del proyecto"\n'
            '\u2022 "Seg\u00fan mis documentos, \u00bfqu\u00e9 dice el contrato?"\n'
            '\u2022 "Extra\u00e9 el texto de esta factura" + foto\n\n'
            "\U0001f4a1 *Funciones principales:*\n"
            "\u2705 Recordatorios con fecha, recurrencia y prioridad\n"
            "\u2705 B\u00fasqueda en internet con resumen IA\n"
            "\u2705 Reconocimiento de m\u00fasica (mand\u00e1 un audio)\n"
            "\u2705 Visi\u00f3n en im\u00e1genes y OCR en facturas\n"
            "\u2705 Google Calendar, Gmail, YouTube y Drive con /auth\n"
            "\u2705 Listas compartidas, rutinas, h\u00e1bitos y metas\n"
            "\u2705 Documentos, minutas, presupuestos y suscripciones\n"
            "\u2705 Voz, modo privado, respaldo cifrado y autodiagn\u00f3stico\n"
            "\u2705 Dashboard web con /panel\n\n"
            "\U0001f4cb *Res\u00famenes autom\u00e1ticos:*\n"
            "\u2022 6:00 AM por defecto \u2192 Plan del d\u00eda\n"
            "\u2022 9:00 PM \u2192 Resumen de actividades y gastos\n"
            "\u2022 Domingo \u2192 Reporte semanal PDF\n\n"
            "\u00a1Estoy listo para ayudarte, *Jefe*!"
        )
    else:
        msg = (
            f"\U0001f9e0 *\u00a1Hola, {name}! Soy Osiris.*\n\n"
            "Soy un asistente personal creado para ayudarte con tus tareas diarias. "
            "Puedo gestionar recordatorios, apuntes, gastos y mucho m\u00e1s. "
            "\u00a1Es un placer ayudarte!\n\n"
            "\U0001f4ac *As\u00ed pod\u00e9s pedirme cosas:*\n"
            '\u2022 "Osiris recu\u00e9rdame ma\u00f1ana a las 3pm comprar leche" te lo recordar\u00e9\n'
            '\u2022 "Osiris busca el resultado del partido de anoche" y te lo busco\n'
            '\u2022 "Osiris gast\u00e9 5000 en el super" y lo registro\n'
            '\u2022 "Osiris cre\u00e1 una lista de pendientes" y la creo\n'
            '\u2022 "Osiris guarda esta idea" y va a tu bandeja\n'
            '\u2022 Enviame un PDF y luego preguntame por su contenido\n'
            '\u2022 Mandame una foto diciendo "extrae el texto" y lo leo\n\n'
            "\u00a1Solo dec\u00ed 'Osiris' seguido de lo que necesit\u00e1s y yo me encargo!"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != CREATOR_ID:
        await update.message.reply_text("Solo el creador puede conectar una cuenta de Google.")
        return
    if await asyncio.to_thread(is_authenticated, user_id):
        await update.message.reply_text("Ya est\u00e1s autenticado con Google \u2705")
        return
    try:
        url = get_auth_url(user_id)
        await update.message.reply_text(
            f"Abre este enlace e inicia sesi\u00f3n con Google:\n{url}\n\n"
            "Al terminar, Google volvera automaticamente a Osiris. El enlace vence en 15 minutos."
        )
    except Exception as e:
        logging.exception("No se pudo iniciar Google OAuth: %s", e)
        await update.message.reply_text("No pude iniciar la conexion con Google.")

async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != CREATOR_ID:
        await update.message.reply_text("\u26d4 Solo el creador puede abrir el panel.")
        return
    dashboard_url = os.getenv("DASHBOARD_URL")
    if not dashboard_url:
        dashboard_url = WEBHOOK_URL.removesuffix("/webhook") if DATABASE_URL else "http://localhost:5000"
    await update.message.reply_text(
        f"\U0001f9e0 *Panel Osiris*\n\nAbre tu navegador y visita:\n{dashboard_url}\n\n"
        "Inicia sesi\u00f3n con la contrase\u00f1a privada del panel.",
        parse_mode="Markdown"
    )

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(f"\U0001f464 Tu ID de Telegram: `{uid}`\n\nSi sos el creador, ponelo en la variable `CREATOR_ID` de Render.", parse_mode="Markdown")

async def authorize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != CREATOR_ID:
        await update.message.reply_text("\u26d4 Solo el creador puede autorizar usuarios.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Us\u00e1: /authorize <id>")
        return
    try:
        target = int(args[0])
        authorize_user(target)
        await update.message.reply_text(f"\u2705 Usuario {target} autorizado.")
    except ValueError:
        await update.message.reply_text("ID inv\u00e1lido.")

async def deauthorize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != CREATOR_ID:
        await update.message.reply_text("\u26d4 Solo el creador puede desautorizar usuarios.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Us\u00e1: /deauthorize <id>")
        return
    try:
        target = int(args[0])
        deauthorize_user(target)
        await update.message.reply_text(f"\U0001f5d1\ufe0f Usuario {target} desautorizado.")
    except ValueError:
        await update.message.reply_text("ID inv\u00e1lido.")

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid == CREATOR_ID or is_authorized(uid):
        await update.message.reply_text("Ya est\u00e1s autorizado.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Us\u00e1: /register <c\u00f3digo>")
        return
    result = redeem_auth_code(args[0], uid)
    msgs = {"ok": "\u2705 *Autorizado!* Ya pod\u00e9s usar el bot.", "invalido": "\u26d4 C\u00f3digo inv\u00e1lido.", "usado": "\U0001f512 Ese c\u00f3digo ya fue usado.", "expirado": "\u23f3 El c\u00f3digo expir\u00f3 (24h). Ped\u00ed uno nuevo al creador."}
    await update.message.reply_text(msgs.get(result, "\u26d4 Error."), parse_mode="Markdown")


async def export_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    user_id = update.effective_user.id
    data = await asyncio.to_thread(collect_backup_data, user_id)
    payload = json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    document = BytesIO(payload)
    document.name = f"osiris_datos_{user_id}.json"
    await update.message.reply_document(document=document, caption="Copia completa de tus datos en Osiris.")


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    user_id = update.effective_user.id
    try:
        blob, filename, count = await asyncio.to_thread(create_encrypted_backup, user_id)
        await asyncio.to_thread(record_backup, user_id, filename, "created")
        document = BytesIO(blob)
        document.name = filename
        await update.message.reply_document(
            document=document,
            filename=filename,
            caption=f"Respaldo cifrado de Osiris: {count} registros. Conserva la clave OSIRIS_BACKUP_KEY.",
        )
    except Exception as exc:
        logging.exception("No se pudo crear el respaldo: %s", exc)
        await update.message.reply_text("No pude crear el respaldo cifrado. Revisa OSIRIS_BACKUP_KEY.")


async def disconnect_google_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    token = create_pending_action(update.effective_user.id, "revoke_google", {})
    await update.message.reply_text(
        "Esto desconectara Calendar, Drive, YouTube y Gmail de Osiris. No borra datos en Google.",
        reply_markup=confirmation_buttons(token, "Desconectar Google"),
    )


def database_self_check():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT 1")
    row = c.fetchone()
    conn.close()
    if not row or row[0] != 1:
        raise RuntimeError("La base de datos no respondio correctamente")
    return True


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    user_id = update.effective_user.id
    lines = ["Estado de Osiris:"]
    try:
        await asyncio.to_thread(database_self_check)
        await asyncio.to_thread(record_system_check, "database", "ok", "Consulta SELECT 1")
        lines.append("- Base de datos: operativa")
    except Exception as exc:
        lines.append(f"- Base de datos: error ({str(exc)[:100]})")
    try:
        bot_user = await context.bot.get_me()
        lines.append(f"- Telegram: conectado como @{bot_user.username}")
    except Exception:
        lines.append("- Telegram: no respondio")
    providers = []
    if os.getenv("OPENROUTER_API_KEY"):
        providers.append("OpenRouter")
    if os.getenv("GROQ_API_KEY"):
        providers.append("Groq")
    lines.append(f"- IA configurada: {', '.join(providers) if providers else 'ningun proveedor'}")
    active_count = len(get_all_active())
    lines.append(f"- Recordatorios activos totales: {active_count}")
    last_backup = get_last_backup(user_id)
    lines.append(
        f"- Ultimo respaldo: {last_backup[2]} ({last_backup[1]})" if last_backup else
        "- Ultimo respaldo: aun no creado"
    )
    previous_checks = get_system_status()
    if previous_checks:
        lines.append("- Autodiagnostico: " + ", ".join(f"{name}={status}" for name, status, _, _ in previous_checks))
    await update.message.reply_text("\n".join(lines)[:3900])


async def delete_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    user_id = update.effective_user.id
    token = create_pending_action(user_id, "delete_user_data", {})
    await update.message.reply_text(
        "Esto eliminara recordatorios, tareas, gastos, memoria, historial y contactos. No se puede deshacer.",
        reply_markup=confirmation_buttons(token, "Borrar todos mis datos"),
    )


async def updates_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    latest = UPDATES.split("[17 Jul 2026]", 1)[0].strip()
    await update.message.reply_text(latest[:3900])


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    action = await asyncio.to_thread(undo_last_action, update.effective_user.id)
    if action:
        await update.message.reply_text(f"Deshice la ultima accion reversible: {action}.")
    else:
        await update.message.reply_text("No encontre una accion reciente que pueda deshacer con seguridad.")


async def private_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    user_id = update.effective_user.id
    current = bool(get_preference(user_id, "private_mode", False))
    requested = context.args[0].lower() if context.args else None
    if requested in {"on", "activar", "si", "1"}:
        enabled = True
    elif requested in {"off", "desactivar", "no", "0"}:
        enabled = False
    else:
        enabled = not current
    set_preference(user_id, "private_mode", enabled)
    await update.message.reply_text(
        "Modo privado activado: no guardare nuevas conversaciones ni patrones."
        if enabled else
        "Modo privado desactivado: la memoria conversacional vuelve a funcionar."
    )


async def inbox_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    category = context.args[0] if context.args else None
    rows = await asyncio.to_thread(get_inbox, update.effective_user.id, category)
    if not rows:
        await update.message.reply_text("Tu bandeja esta vacia.")
        return
    lines = ["Bandeja de entrada:"]
    for item_id, item_type, content, item_category, private, created_at in rows:
        lock = " [privado]" if private else ""
        lines.append(f"#{item_id} [{item_category}/{item_type}]{lock} {content[:120]}")
    await update.message.reply_text("\n".join(lines)[:3900])


async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    preferences = await asyncio.to_thread(get_preferences, update.effective_user.id)
    defaults = {
        "private_mode": False,
        "voice_replies": False,
        "morning_summary": True,
        "evening_summary": True,
        "weekly_pdf": True,
    }
    defaults.update(preferences)
    lines = ["Configuracion de Osiris:"]
    for key, value in defaults.items():
        lines.append(f"- {key}: {value}")
    await update.message.reply_text("\n".join(lines))


def build_day_plan(user_id):
    now = local_now()
    reminders = get_reminders(user_id, "all")
    today = now.strftime("%Y-%m-%d")
    today_reminders = [row for row in reminders if row[2].startswith(today)]
    overdue = [row for row in reminders if row[2] < fmt_local(now)]
    habits = get_habits(user_id)
    goals = get_goals(user_id)
    dates = get_upcoming_dates(user_id, 14)
    subscriptions = get_due_subscriptions(user_id, 7)
    lines = [f"Plan para {now.strftime('%d/%m/%Y')}:"]
    if today_reminders:
        lines.append("\nRecordatorios:")
        lines.extend(f"- {row[2][11:]} {row[1]}" for row in today_reminders[:10])
    if overdue:
        lines.append("\nPendientes atrasados:")
        lines.extend(f"- #{row[0]} {row[1]} ({row[2]})" for row in overdue[:5])
    if habits:
        lines.append("\nHabitos:")
        lines.extend(f"- {name}: {value:g}/{target}" for _, name, _, target, value in habits[:10])
    if goals:
        lines.append("\nMetas:")
        lines.extend(f"- {title}: {progress}% ({done}/{steps} pasos)" for _, title, _, _, progress, steps, done in goals[:5])
    if dates:
        lines.append("\nFechas proximas:")
        lines.extend(f"- {title}: {candidate} (faltan {delta} dias)" for _, title, _, _, _, candidate, delta in dates[:5])
    if subscriptions:
        lines.append("\nPagos proximos:")
        lines.extend(f"- {name}: {amount:g} {currency}, vence {due}" for _, name, amount, currency, _, due, _ in subscriptions[:5])
    if len(lines) == 1:
        lines.append("No tienes elementos pendientes para hoy.")
    return "\n".join(lines)


async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    plan = await asyncio.to_thread(build_day_plan, update.effective_user.id)
    await update.message.reply_text(plan[:3900])


async def habits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    habits = await asyncio.to_thread(get_habits, update.effective_user.id)
    if not habits:
        await update.message.reply_text("Aun no tienes habitos. Dime: crea el habito leer 20 minutos.")
        return
    lines = ["Habitos de hoy:"]
    lines.extend(f"#{habit_id} {name}: {value:g}/{target} ({frequency})" for habit_id, name, frequency, target, value in habits)
    await update.message.reply_text("\n".join(lines))


async def routines_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    routines = await asyncio.to_thread(get_routines, update.effective_user.id)
    if not routines:
        await update.message.reply_text("Aun no tienes rutinas guardadas.")
        return
    await update.message.reply_text(
        "Rutinas:\n" + "\n".join(f"#{rid} {name} ({steps} pasos)" for rid, name, active, steps in routines)
    )


async def goals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    goals = await asyncio.to_thread(get_goals, update.effective_user.id)
    if not goals:
        await update.message.reply_text("Aun no tienes metas activas.")
        return
    lines = ["Metas activas:"]
    lines.extend(f"#{gid} {title}: {progress}% ({done}/{steps} pasos), fecha {target or 'sin fecha'}" for gid, title, target, status, progress, steps, done in goals)
    await update.message.reply_text("\n".join(lines))


async def dates_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    dates = await asyncio.to_thread(get_upcoming_dates, update.effective_user.id, 60)
    if not dates:
        await update.message.reply_text("No tienes fechas importantes en los proximos 60 dias.")
        return
    lines = ["Fechas importantes:"]
    lines.extend(f"#{item_id} {title}: {candidate} (faltan {delta} dias)" for item_id, title, original, recurring, lead, candidate, delta in dates)
    await update.message.reply_text("\n".join(lines))


async def weekly_pdf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    path, caption = await asyncio.to_thread(generate_weekly_report, update.effective_user.id)
    try:
        with open(path, "rb") as document:
            await update.message.reply_document(document, filename=os.path.basename(path), caption=caption)
    finally:
        if os.path.exists(path):
            os.remove(path)


def extract_document_text(file_path, suffix):
    suffix = suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        max_pages = int(os.getenv("DOCUMENT_MAX_PAGES", "100"))
        parts = []
        for index, page in enumerate(reader.pages[:max_pages], 1):
            text = page.extract_text() or ""
            if text.strip():
                parts.append(f"[Pagina {index}]\n{text.strip()}")
        return "\n\n".join(parts)
    if suffix == ".docx":
        from docx import Document

        document = Document(file_path)
        return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
    if suffix in {".txt", ".md", ".csv", ".json"}:
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return Path(file_path).read_text(encoding="latin-1")
    raise ValueError("tipo de documento no compatible")


async def documents_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    rows = await asyncio.to_thread(list_documents, update.effective_user.id)
    if not rows:
        await update.message.reply_text("Tu biblioteca esta vacia. Enviame un PDF, DOCX o TXT.")
        return
    lines = ["Biblioteca personal:"]
    for doc_id, title, file_type, private, created_at, chunks in rows:
        lock = " [privado]" if private else ""
        lines.append(f"#{doc_id} {title} ({file_type}, {chunks} fragmentos){lock}")
    await update.message.reply_text("\n".join(lines)[:3900])


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    user_id = update.effective_user.id
    document = update.message.document
    filename = document.file_name or f"documento_{document.file_unique_id}.txt"
    suffix = Path(filename).suffix.lower()
    if suffix == ".osirisbackup":
        max_backup_bytes = int(os.getenv("BACKUP_MAX_MB", "20")) * 1024 * 1024
        if document.file_size and document.file_size > max_backup_bytes:
            await update.message.reply_text("El respaldo supera el limite configurado.")
            return
        temp_path = None
        try:
            telegram_file = await context.bot.get_file(document.file_id)
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
                temp_path = temp.name
            await telegram_file.download_to_drive(temp_path)
            blob = Path(temp_path).read_bytes()
            payload = await asyncio.to_thread(decrypt_backup, blob, user_id)
            count = sum(len(rows) for rows in payload["tables"].values())
            token = create_pending_action(
                user_id,
                "restore_backup",
                {"blob": base64.b64encode(blob).decode("ascii"), "filename": filename},
            )
            await update.message.reply_text(
                f"Respaldo valido del {payload['created_at']} con {count} registros. "
                "Restaurarlo reemplazara tus datos actuales.",
                reply_markup=confirmation_buttons(token, "Restaurar respaldo"),
            )
        except Exception as exc:
            logging.warning("Respaldo rechazado: %s", exc)
            await update.message.reply_text(f"No pude validar ese respaldo: {str(exc)[:180]}")
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
        return
    if not feature_enabled(user_id, "documents", True):
        await update.message.reply_text("La biblioteca documental esta desactivada en tu configuracion.")
        return
    max_bytes = int(os.getenv("DOCUMENT_MAX_MB", "10")) * 1024 * 1024
    if document.file_size and document.file_size > max_bytes:
        await update.message.reply_text("El documento supera el limite configurado.")
        return
    if suffix not in {".pdf", ".docx", ".txt", ".md", ".csv", ".json"}:
        await update.message.reply_text("Puedo archivar PDF, DOCX, TXT, Markdown, CSV y JSON.")
        return
    temp_path = None
    try:
        telegram_file = await context.bot.get_file(document.file_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
            temp_path = temp.name
        await telegram_file.download_to_drive(temp_path)
        content = await asyncio.to_thread(extract_document_text, temp_path, suffix)
        max_chars = int(os.getenv("DOCUMENT_MAX_CHARS", "100000"))
        content = content[:max_chars].strip()
        if not content:
            await update.message.reply_text("No pude extraer texto de ese documento.")
            return
        doc_id, created = await asyncio.to_thread(
            add_document,
            user_id,
            filename,
            suffix.lstrip("."),
            document.file_id,
            content,
            bool(get_preference(user_id, "private_mode", False)),
        )
        await update.message.reply_text(
            f"Documento #{doc_id} archivado con {len(content):,} caracteres."
            if created else
            f"Ese documento ya estaba guardado como #{doc_id}."
        )
    except Exception as exc:
        logging.exception("Error procesando documento: %s", exc)
        await update.message.reply_text("No pude procesar ese documento.")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


async def budgets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    rows = await asyncio.to_thread(get_budget_status, update.effective_user.id)
    if not rows:
        await update.message.reply_text("Aun no tienes presupuestos configurados.")
        return
    lines = ["Presupuestos del mes:"]
    for category, currency, limit, alert_percent, spent in rows:
        percent = (spent / limit * 100) if limit else 0
        lines.append(f"- {category}: {spent:,.2f}/{limit:,.2f} {currency} ({percent:.0f}%)")
    await update.message.reply_text("\n".join(lines))


async def subscriptions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    rows = await asyncio.to_thread(get_subscriptions, update.effective_user.id)
    if not rows:
        await update.message.reply_text("Aun no tienes suscripciones registradas.")
        return
    lines = ["Suscripciones:"]
    lines.extend(f"#{sid} {name}: {amount:g} {currency}, proximo {due} ({frequency})" for sid, name, amount, currency, category, due, frequency in rows)
    await update.message.reply_text("\n".join(lines))


async def expenses_csv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    rows = await asyncio.to_thread(get_expense_export_rows, update.effective_user.id)
    if not rows:
        await update.message.reply_text("No tienes gastos para exportar.")
        return
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["fecha", "descripcion", "categoria", "monto", "moneda"])
    writer.writerows(rows)
    document = BytesIO(stream.getvalue().encode("utf-8-sig"))
    document.name = f"gastos_osiris_{update.effective_user.id}.csv"
    await update.message.reply_document(document=document, caption="Gastos compatibles con Excel.")


async def voice_mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    user_id = update.effective_user.id
    current = bool(get_preference(user_id, "voice_replies", False))
    enabled = not current
    if context.args:
        enabled = context.args[0].lower() in {"on", "si", "1", "activar"}
    set_preference(user_id, "voice_replies", enabled)
    await update.message.reply_text(
        "Respuestas de voz activadas." if enabled else "Respuestas de voz desactivadas."
    )


async def shared_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    rows = await asyncio.to_thread(get_shared_resources, update.effective_user.id)
    if not rows:
        await update.message.reply_text("No tienes recursos compartidos contigo.")
        return
    lines = ["Compartidos contigo:"]
    lines.extend(f"#{share_id} {resource_type} {name or resource_id} ({permission})" for share_id, resource_type, resource_id, permission, owner, name in rows)
    await update.message.reply_text("\n".join(lines))


def build_proactive_insights(user_id):
    now = local_now()
    overdue = [row for row in get_reminders(user_id, "all") if row[2] < fmt_local(now)]
    budget_risks = []
    for category, currency, limit, alert_percent, spent in get_budget_status(user_id):
        percent = (spent / limit * 100) if limit else 0
        if percent >= alert_percent:
            budget_risks.append((category, currency, percent))
    subscriptions = get_due_subscriptions(user_id, 3)
    dates = [row for row in get_upcoming_dates(user_id, 14) if row[6] <= row[4]]
    lines = []
    if overdue:
        lines.append(f"- Tienes {len(overdue)} recordatorio(s) atrasado(s).")
    if budget_risks:
        details = ", ".join(f"{category} {percent:.0f}% {currency}" for category, currency, percent in budget_risks[:3])
        lines.append(f"- Presupuestos en alerta: {details}.")
    if subscriptions:
        lines.append(f"- Vencen {len(subscriptions)} suscripcion(es) en los proximos 3 dias.")
    if dates:
        lines.append(f"- Hay {len(dates)} fecha(s) importante(s) dentro de su periodo de aviso.")
    if not lines:
        return "No detecte riesgos ni pendientes urgentes en este momento."
    return "Sugerencias de Osiris:\n" + "\n".join(lines) + "\n\nNo hice cambios automaticamente."


async def finish_active_meeting(user_id):
    ended = await asyncio.to_thread(end_meeting, user_id)
    if not ended:
        return None
    meeting_id, title, items = ended
    if not items:
        summary = f"Minuta de {title}: reunion cerrada sin notas registradas."
    else:
        source = "\n".join(
            f"[{item_type}] {content}"
            + (f" | responsable: {assignee}" if assignee else "")
            + (f" | fecha: {due_date}" if due_date else "")
            for item_type, content, assignee, due_date in items
        )
        try:
            summary = await asyncio.to_thread(summarize_content, source, "ejecutivo")
        except Exception:
            logging.exception("No se pudo generar la minuta con IA")
            summary = f"Minuta de {title}:\n" + source
    await asyncio.to_thread(set_meeting_summary, user_id, meeting_id, summary)
    return meeting_id, title, summary


async def meeting_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    user_id = update.effective_user.id
    args = context.args or []
    command = args[0].lower() if args else "estado"
    if command in {"iniciar", "inicio", "start"}:
        if get_active_meeting(user_id):
            await update.message.reply_text("Ya tienes una reunion activa. Cierrala antes de iniciar otra.")
            return
        title = " ".join(args[1:]).strip() or "Reunion"
        meeting_id = start_meeting(user_id, title[:200])
        await update.message.reply_text(f"Reunion #{meeting_id} iniciada: {title}.")
        return
    if command in {"terminar", "cerrar", "fin", "end"}:
        result = await finish_active_meeting(user_id)
        await update.message.reply_text(
            f"Reunion #{result[0]} cerrada.\n\n{result[2]}" if result else "No hay una reunion activa."
        )
        return
    active = get_active_meeting(user_id)
    await update.message.reply_text(
        f"Reunion activa #{active[0]}: {active[1]}." if active else
        "No hay una reunion activa. Usa /reunion iniciar Titulo."
    )


async def insights_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    message = await asyncio.to_thread(build_proactive_insights, update.effective_user.id)
    await update.message.reply_text(message)


def schedule_from_db(app):
    reminders = get_all_active()
    now = local_now()
    for row in reminders:
        try:
            rid, uid, text, dt_str, recurring, search_q, friend_name, end_date, lead_minutes = row
            dt = parse_local(dt_str)
            alarm_dt = smart_alarm(dt, lead_minutes or 0)
            current = get_reminder_by_id(rid)
            delivery_status = current[10] if current else "pending"
            attempts = current[11] if current and current[11] else 0
            if delivery_status == "failed" and attempts >= MAX_DELIVERY_ATTEMPTS:
                logging.error("Recordatorio %s requiere intervencion manual", rid)
                continue
            if alarm_dt <= now:
                if not recurring:
                    if delivery_status != "retrying" and now - dt > timedelta(hours=MISSED_REMINDER_GRACE_HOURS):
                        deactivate_by_id(rid)
                        logging.warning("Recordatorio %s expirado durante una caida prolongada", rid)
                        continue
                    alarm_dt = now + timedelta(seconds=5)
                while alarm_dt <= now:
                    nxt = calc_next(dt, recurring, end_date)
                    if nxt is None:
                        deactivate_by_id(rid)
                        break
                    dt = nxt
                    alarm_dt = smart_alarm(dt, lead_minutes or 0)
                if alarm_dt <= now:
                    continue
                update_datetime(rid, fmt_local(dt))
            data = reminder_job_data(
                (rid, uid, text, fmt_local(dt), recurring, search_q, friend_name, end_date, lead_minutes),
                attempts=attempts,
            )
            replace_reminder_job(app.job_queue, alarm_dt, data)
        except Exception as e:
            logging.error(f"Error scheduling reminder {row[0] if row else '?'}: {e}")

def calc_next(dt, recurring, end_date=None):
    if recurring == "daily":
        nxt = dt + timedelta(days=1)
    elif recurring == "weekly":
        nxt = dt + timedelta(weeks=1)
    elif recurring == "monthly":
        month = dt.month + 1
        year = dt.year
        if month > 12:
            month = 1
            year += 1
        day = min(dt.day, monthrange(year, month)[1])
        nxt = dt.replace(year=year, month=month, day=day)
    elif recurring == "weekdays":
        nxt = dt + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
    else:
        nxt = dt + timedelta(days=1)
    if end_date:
        end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=TZ).date()
        if nxt.date() > end:
            return None
    return nxt

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data
    current = get_reminder_by_id(data["rid"])
    if not current or not current[9]:
        logging.info("Recordatorio %s cancelado antes de entregarse", data["rid"])
        return
    if current[3] != data["dt_str"]:
        refreshed_data = reminder_job_data(current[:9], attempts=current[11] or 0)
        refreshed_dt = parse_local(current[3])
        replace_reminder_job(
            context.job_queue,
            max(smart_alarm(refreshed_dt, current[8] or 0), local_now() + timedelta(seconds=1)),
            refreshed_data,
        )
        return
    logging.info(f"Recordatorio {data['rid']}: {data['text']}")
    lead = data.get("lead_minutes") or 0
    if data.get("friend_name"):
        contact = get_contact(data["uid"], data["friend_name"])
        if contact and is_authorized(contact[1]):
            target_chat_id = contact[1]
            prefix = "Recordatorio compartido por uno de tus contactos: "
        else:
            target_chat_id = data["uid"]
            prefix = f"Para {data['friend_name']} (sin contacto de Telegram configurado): "
    elif lead:
        target_chat_id = data["uid"]
        prefix = f"\u23f0 *Jefe*, recuerda que a las {data['dt_str'].split()[1]} tienes: "
    else:
        target_chat_id = data["uid"]
        prefix = "\u23f0 *Jefe*, "
    plain_prefix = re.sub(r"[*_`]", "", prefix)
    try:
        await context.bot.send_message(
            chat_id=target_chat_id,
            text=f"{plain_prefix}{data['text']}",
            reply_markup=reminder_buttons(data["rid"], bool(data.get("recurring"))) if target_chat_id == data["uid"] else None,
        )
        if target_chat_id != data["uid"]:
            await context.bot.send_message(
                chat_id=data["uid"],
                text=f"Recordatorio enviado a {data['friend_name']}: {data['text']}",
                reply_markup=reminder_buttons(data["rid"], bool(data.get("recurring"))),
            )
        mark_delivered(data["rid"])
    except Exception as exc:
        attempts = int(data.get("attempts") or 0) + 1
        mark_delivery_attempt(data["rid"], exc, final=attempts >= MAX_DELIVERY_ATTEMPTS)
        logging.exception("Fallo entregando recordatorio %s, intento %s", data["rid"], attempts)
        if attempts < MAX_DELIVERY_ATTEMPTS:
            retry_seconds = min(60 * (2 ** (attempts - 1)), 3600)
            context.job_queue.run_once(
                send_reminder,
                when=timedelta(seconds=retry_seconds),
                data=dict(data, attempts=attempts),
                name=str(data["rid"]),
            )
        elif CREATOR_ID:
            try:
                await context.bot.send_message(
                    chat_id=CREATOR_ID,
                    text=f"No pude entregar el recordatorio #{data['rid']} despues de {attempts} intentos.",
                )
            except Exception:
                logging.exception("No se pudo notificar el fallo definitivo")
        return
    if data.get("search_query"):
        logging.info(f"Buscando: {data['search_query']}")
        try:
            answer = await asyncio.to_thread(answer_question, data['text'], data['search_query'])
            await context.bot.send_message(chat_id=data["uid"], text=answer)
        except Exception as e:
            logging.error(f"Search error en recordatorio: {e}")
            await context.bot.send_message(chat_id=data["uid"], text="No pude obtener la informaci\u00f3n.")
    if data.get("recurring"):
        current_dt = parse_local(data["dt_str"])
        next_dt = calc_next(current_dt, data["recurring"], data.get("end_date"))
        if next_dt is None:
            await context.bot.send_message(
                chat_id=data["uid"],
                text=f"\U0001f4cc Jefe, este fue el ultimo recordatorio de '{data['text']}' (periodo terminado).",
            )
            deactivate_by_id(data["rid"])
            return
        next_str = fmt_local(next_dt)
        update_datetime(data["rid"], next_str)
        next_alarm = smart_alarm(next_dt, data.get("lead_minutes") or 0)
        replace_reminder_job(
            context.job_queue,
            next_alarm,
            dict(data, dt_str=next_str, attempts=0),
        )
    else:
        deactivate_by_id(data["rid"])


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if user_id != CREATOR_ID and not is_authorized(user_id):
        await query.edit_message_text("Acceso denegado.")
        return

    parts = (query.data or "").split(":")
    if len(parts) >= 3 and parts[0] == "reminder":
        action = parts[1]
        try:
            reminder_id = int(parts[2])
        except ValueError:
            await query.edit_message_text("Recordatorio invalido.")
            return
        reminder = get_reminder_by_id(reminder_id, user_id)
        if not reminder:
            await query.edit_message_text("Este recordatorio ya no existe.")
            return
        if action == "done":
            deactivate_by_id(reminder_id, user_id)
            for job in context.job_queue.get_jobs_by_name(str(reminder_id)):
                job.schedule_removal()
            await query.edit_message_text(f"Completado: {reminder[2]}")
            return
        if action == "snooze" and len(parts) == 4:
            try:
                minutes = max(1, min(int(parts[3]), 1440))
            except ValueError:
                await query.edit_message_text("Tiempo de posposicion invalido.")
                return
            new_dt = local_now() + timedelta(minutes=minutes)
            new_str = fmt_local(new_dt)
            snooze_reminder(reminder_id, user_id, new_str)
            refreshed = get_reminder_by_id(reminder_id, user_id)
            data = reminder_job_data(refreshed[:9])
            replace_reminder_job(context.job_queue, new_dt, data)
            await query.edit_message_text(f"Pospuesto {minutes} minutos: {reminder[2]}\nNueva hora: {new_str}")
            return

    if len(parts) == 3 and parts[0] == "confirm":
        pending = consume_pending_action(parts[1], user_id)
        if not pending:
            await query.edit_message_text("Esta confirmacion expiro o ya fue utilizada.")
            return
        if parts[2] != "yes":
            await query.edit_message_text("Accion cancelada.")
            return
        action_type, payload = pending
        if action_type == "delete_reminder":
            reminder_id = int(payload["reminder_id"])
            reminder = get_reminder_by_id(reminder_id, user_id)
            if not reminder:
                await query.edit_message_text("El recordatorio ya no existe.")
                return
            deactivate_by_id(reminder_id, user_id)
            for job in context.job_queue.get_jobs_by_name(str(reminder_id)):
                job.schedule_removal()
            log_activity(user_id, "eliminar_recordatorio", reminder[2])
            await query.edit_message_text(f"Eliminado: {reminder[2]}")
            return
        if action_type == "record_expense":
            amount = float(payload["amount"])
            currency = payload.get("currency", "CRC")
            description = payload.get("description", "factura")
            category = payload.get("category", "otros")
            expense_id = add_expense(user_id, amount, description, category, currency)
            add_expense_items(expense_id, payload.get("items") or [])
            record_history(
                user_id,
                "record_expense",
                "expense",
                expense_id,
                after={"amount": amount, "description": description, "currency": currency},
                reversible=True,
            )
            log_activity(user_id, "registrar_gasto", f"{description}: {amount} {currency}")
            alert = budget_alert_text(user_id, category, currency)
            await query.edit_message_text(f"Gasto registrado: {description} - {amount:g} {currency}{alert}")
            return
        if action_type == "create_commitment":
            reminder_text = str(payload.get("text") or "Compromiso").strip()[:1000]
            dt_str = str(payload.get("datetime") or "")
            try:
                dt = parse_local(dt_str)
            except ValueError:
                await query.edit_message_text("La fecha del compromiso ya no es valida.")
                return
            if dt <= local_now():
                await query.edit_message_text("La fecha del compromiso ya paso. Crealo de nuevo con otra hora.")
                return
            lead_minutes = int(payload.get("lead_minutes") or 0)
            reminder_id = add_reminder(
                user_id,
                reminder_text,
                dt_str,
                lead_minutes=lead_minutes,
            )
            record_history(
                user_id,
                "create_commitment",
                "reminder",
                reminder_id,
                after={"text": reminder_text, "datetime": dt_str},
                reversible=True,
            )
            replace_reminder_job(
                context.job_queue,
                max(smart_alarm(dt, lead_minutes), local_now() + timedelta(seconds=1)),
                reminder_job_data(
                    (reminder_id, user_id, reminder_text, dt_str, None, None, None, None, lead_minutes)
                ),
            )
            log_activity(user_id, "crear_compromiso", f"{reminder_text} - {dt_str}")
            await query.edit_message_text(f"Compromiso convertido en recordatorio: {reminder_text}\n{dt_str}")
            return
        if action_type == "delete_task":
            delete_task_item(int(payload["item_id"]))
            log_activity(user_id, "eliminar_tarea", payload["text"])
            await query.edit_message_text(f"Tarea eliminada: {payload['text']}")
            return
        if action_type == "delete_task_list":
            delete_task_list(int(payload["list_id"]))
            log_activity(user_id, "eliminar_lista", payload["name"])
            await query.edit_message_text(f"Lista eliminada: {payload['name']}")
            return
        if action_type == "delete_document":
            removed = delete_document(user_id, int(payload["document_id"]))
            await query.edit_message_text(
                "Documento eliminado de la biblioteca." if removed else "No encontre ese documento."
            )
            return
        if action_type == "revoke_google":
            existed, remotely_revoked = await asyncio.to_thread(revoke_google_access, user_id)
            if not existed:
                message = "No habia una conexion de Google guardada."
            elif remotely_revoked:
                message = "El acceso fue revocado en Google y la conexion local fue eliminada."
            else:
                message = (
                    "La conexion local fue eliminada. Google no confirmo la revocacion remota; "
                    "puedes revisar los permisos de tu cuenta de Google."
                )
            await query.edit_message_text(message)
            return
        if action_type == "restore_backup":
            try:
                blob = base64.b64decode(payload["blob"], validate=True)
                backup_payload = await asyncio.to_thread(decrypt_backup, blob, user_id)
                restored = await asyncio.to_thread(restore_backup_payload, backup_payload, user_id)
                for job in context.job_queue.jobs():
                    if job.data and job.data.get("uid") == user_id:
                        job.schedule_removal()
                schedule_from_db(context.application)
                await asyncio.to_thread(
                    record_backup,
                    user_id,
                    payload.get("filename", "respaldo.osirisbackup"),
                    "restored",
                )
                await query.edit_message_text(
                    f"Respaldo restaurado correctamente: {restored} registros. "
                    "Los recordatorios activos fueron reprogramados."
                )
            except Exception as exc:
                logging.exception("Fallo restaurando respaldo: %s", exc)
                await query.edit_message_text(
                    "No pude restaurar el respaldo. La transaccion fue revertida y tus datos actuales siguen intactos."
                )
            return
        if action_type == "delete_user_data":
            for job in context.job_queue.jobs():
                if job.data and job.data.get("uid") == user_id:
                    job.schedule_removal()
            delete_user_data(user_id, remove_authorization=user_id != CREATOR_ID)
            await query.edit_message_text("Tus datos fueron eliminados de Osiris.")
            return

    await query.edit_message_text("Accion no reconocida o expirada.")

async def process_action(update, context, text, result, user_id, history=None, memories=None):
    action = result.get("action")
    if action == "create":
        reminder_text = result.get("text", text)
        dt_str = result.get("datetime")
        recurring = result.get("recurring")
        end_date = result.get("until") or result.get("end_date")
        if not dt_str:
            await update.message.reply_text("No entend\u00ed la fecha. Ejemplo: 'Recu\u00e9rdame X ma\u00f1ana a las 3pm'")
            return
        try:
            dt = parse_local(dt_str)
        except ValueError:
            await update.message.reply_text("No entend\u00ed la fecha. Ejemplo: 'Recu\u00e9rdame X ma\u00f1ana a las 3pm'")
            return
        if dt <= local_now():
            await update.message.reply_text("Esa fecha ya paso. Indica una hora futura.")
            return
        rid = add_reminder(user_id, reminder_text, dt_str, recurring, end_date=end_date, lead_minutes=result.get("lead_minutes", 0))
        record_history(user_id, "create_reminder", "reminder", rid, after={"text": reminder_text, "datetime": dt_str}, reversible=True)
        log_activity(user_id, "crear_recordatorio", f"{reminder_text} - {dt_str}{f' ({recurring})' if recurring else ''}{f' hasta {end_date}' if end_date else ''}")
        now = local_now()
        alarm_dt = smart_alarm(dt, result.get("lead_minutes", 0))
        replace_reminder_job(
            context.job_queue,
            max(alarm_dt, now + timedelta(seconds=1)),
            reminder_job_data((rid, user_id, reminder_text, dt_str, recurring, None, None, end_date, result.get("lead_minutes", 0))),
        )
        msg = f"\u2705 *Jefe*, recordatorio guardado:\n\n'{reminder_text}'\n\U0001f4c5 {dt_str}"
        if recurring:
            msg += f"\n\U0001f504 Repite: {recurring}"
        if end_date:
            msg += f"\n\u23f3 Hasta: {end_date}"
        if result.get("lead_minutes"):
            actual = smart_alarm(dt, result.get("lead_minutes", 0))
            if actual.date() < dt.date():
                msg += f"\n\u23f0 Te avisar\u00e9 *{result['lead_minutes']} min antes* → {actual.strftime('%d/%m a las %H:%M')} (d\u00eda anterior)"
            else:
                msg += f"\n\u23f0 Te avisar\u00e9 {result['lead_minutes']} minutos antes ({actual.strftime('%H:%M')})"
        conflicts = [row for row in get_reminders(user_id, "all") if row[0] != rid and row[2] == dt_str]
        if conflicts:
            msg += f"\nAviso: coincide con {len(conflicts)} recordatorio(s) existente(s)."
        await update.message.reply_text(msg)
        save_exchange(user_id, text, msg, action)

    elif action == "create_search":
        reminder_text = result.get("text", text)
        dt_str = result.get("datetime")
        query = result.get("query", reminder_text)
        recurring = result.get("recurring")
        if not dt_str:
            await update.message.reply_text("No entend\u00ed la fecha. Ejemplo: 'Recu\u00e9rdame ma\u00f1ana buscar partidos...'")
            return
        try:
            dt = parse_local(dt_str)
        except ValueError:
            await update.message.reply_text("No entend\u00ed la fecha.")
            return
        if dt <= local_now():
            await update.message.reply_text("Esa fecha ya paso. Indica una hora futura.")
            return
        rid = add_reminder(user_id, reminder_text, dt_str, recurring, search_query=query, end_date=result.get("until") or result.get("end_date"), lead_minutes=result.get("lead_minutes", 0))
        record_history(user_id, "create_search_reminder", "reminder", rid, after={"text": reminder_text, "datetime": dt_str}, reversible=True)
        log_activity(user_id, "crear_recordatorio_busqueda", f"{reminder_text} - {dt_str} (buscar: {query})")
        now = local_now()
        alarm_dt = smart_alarm(dt, result.get("lead_minutes", 0))
        replace_reminder_job(
            context.job_queue,
            max(alarm_dt, now + timedelta(seconds=1)),
            reminder_job_data((rid, user_id, reminder_text, dt_str, recurring, query, None, result.get("until") or result.get("end_date"), result.get("lead_minutes", 0))),
        )
        await update.message.reply_text(f"\u2705 *Jefe*, recordatorio con b\u00fasqueda guardado:\n\n'{reminder_text}'\n\U0001f4c5 {dt_str}\n\U0001f50d Buscar\u00e9: {query}")
        save_exchange(user_id, text, f"Recordatorio con b\u00fasqueda: {reminder_text}", action)

    elif action == "create_friend_reminder":
        reminder_text = result.get("text", text)
        friend_name = result.get("friend_name", "alguien")
        dt_str = result.get("datetime")
        recurring = result.get("recurring")
        if not dt_str:
            await update.message.reply_text("No entend\u00ed la fecha.")
            return
        try:
            dt = parse_local(dt_str)
        except ValueError:
            await update.message.reply_text("No entend\u00ed la fecha.")
            return
        if dt <= local_now():
            await update.message.reply_text("Esa fecha ya paso. Indica una hora futura.")
            return
        rid = add_reminder(user_id, reminder_text, dt_str, recurring, friend_name=friend_name, end_date=result.get("until") or result.get("end_date"), lead_minutes=result.get("lead_minutes", 0))
        record_history(user_id, "create_friend_reminder", "reminder", rid, after={"text": reminder_text, "friend": friend_name}, reversible=True)
        log_activity(user_id, "crear_recordatorio_amigo", f"Para {friend_name}: {reminder_text} - {dt_str}")
        now = local_now()
        alarm_dt = smart_alarm(dt, result.get("lead_minutes", 0))
        replace_reminder_job(
            context.job_queue,
            max(alarm_dt, now + timedelta(seconds=1)),
            reminder_job_data((rid, user_id, reminder_text, dt_str, recurring, None, friend_name, result.get("until") or result.get("end_date"), result.get("lead_minutes", 0))),
        )
        contact = get_contact(user_id, friend_name)
        delivery_note = "Se lo enviare directamente por Telegram." if contact else "No tiene contacto de Telegram; te avisare a ti para que se lo recuerdes."
        await update.message.reply_text(
            f"Recordatorio para {friend_name} guardado:\n\n{reminder_text}\n{dt_str}\n{delivery_note}"
        )
        save_exchange(user_id, text, f"Recordatorio para {friend_name}: {reminder_text}", action)

    elif action == "identify_music":
        context.user_data["music_pending"] = True
        await update.message.reply_text("\U0001f3b5 Manda el audio de la canci\u00f3n y la identifico.")

    elif action == "daily_summary":
        rows = get_today_activity(user_id)
        reminder_count = sum(1 for r in rows if r[0] in ("crear_recordatorio", "crear_recordatorio_busqueda", "crear_recordatorio_amigo"))
        search_count = sum(1 for r in rows if r[0] == "buscar_internet")
        music_count = sum(1 for r in rows if r[0] == "identificar_cancion")
        event_count = sum(1 for r in rows if r[0] == "crear_evento")
        yt_count = sum(1 for r in rows if r[0] == "buscar_youtube")
        drive_count = sum(1 for r in rows if r[0] == "buscar_drive")
        delete_count = sum(1 for r in rows if r[0] == "eliminar_recordatorio")
        lines = ["\U0001f4cb *Resumen del d\u00eda, Jefe!*\n"]
        total = len(rows)
        if total == 0:
            lines.append("A\u00fan no has hecho nada hoy \U0001f634")
        else:
            lines.append(f"En total hiciste {total} cosas:\n")
            if reminder_count:
                lines.append(f"\U0001f514 *{reminder_count}* recordatorio(s) agendado(s)")
            if music_count:
                lines.append(f"\U0001f3b5 *{music_count}* canci\u00f3n(es) identificada(s)")
            if search_count:
                lines.append(f"\U0001f50d *{search_count}* b\u00fasqueda(s) en internet")
            if event_count:
                lines.append(f"\U0001f4c5 *{event_count}* evento(s) de calendario")
            if yt_count:
                lines.append(f"\U0001f4fa *{yt_count}* b\u00fasqueda(s) en YouTube")
            if drive_count:
                lines.append(f"\U0001f4c1 *{drive_count}* b\u00fasqueda(s) en Drive")
            if delete_count:
                lines.append(f"\U0001f5d1\ufe0f *{delete_count}* recordatorio(s) eliminado(s)")
            lines.append("\n\U0001f4c4 Ultimas actividades:")
            for r in rows[-5:]:
                aname = {"crear_recordatorio":"\u2795 Recordatorio","crear_recordatorio_busqueda":"\u2795 Recordatorio + b\u00fasqueda","crear_recordatorio_amigo":"\u2795 Recordatorio amigo","buscar_internet":"\U0001f50d Busc\u00f3","identificar_cancion":"\U0001f3b5 Canci\u00f3n","crear_evento":"\U0001f4c5 Evento","buscar_youtube":"\U0001f4fa YouTube","buscar_drive":"\U0001f4c1 Drive","eliminar_recordatorio":"\U0001f5d1\ufe0f Elimin\u00f3"}.get(r[0], r[0])
                lines.append(f"  \u2022 {aname}{': '+r[1][:60] if r[1] else ''} \u2014 {r[2].split()[1]}")
        await update.message.reply_text("\n".join(lines))

    elif action == "create_event":
        if user_id != CREATOR_ID:
            await update.message.reply_text("Google Calendar solo esta disponible para el creador.")
            return
        if not await asyncio.to_thread(is_authenticated, user_id):
            await update.message.reply_text("Primero usa /auth para conectar Google Calendar.")
            return
        summary = result.get("summary", "")
        dt_str = result.get("datetime")
        duration = result.get("duration", 60)
        if not dt_str:
            await update.message.reply_text("No entend\u00ed la fecha y hora.")
            return
        try:
            link = await asyncio.to_thread(create_event, user_id, summary, dt_str, duration)
            if link:
                log_activity(user_id, "crear_evento", f"{summary} - {dt_str}")
                await update.message.reply_text(
                    f"\U0001f4c5 Evento creado: {summary}\n\U0001f517 Ver en Calendar: {link}"
                )
                save_exchange(user_id, text, f"Evento creado: {summary}", action)
            else:
                await update.message.reply_text("No se pudo crear el evento.")
        except Exception as e:
            logging.exception("Error al crear evento: %s", e)
            await update.message.reply_text("No pude crear el evento en este momento.")

    elif action == "list_calendar":
        if user_id != CREATOR_ID:
            await update.message.reply_text("Google Calendar solo esta disponible para el creador.")
            return
        if not await asyncio.to_thread(is_authenticated, user_id):
            await update.message.reply_text("Primero usa /auth para conectar Google Calendar.")
            return
        try:
            events = await asyncio.to_thread(list_events, user_id, result.get("date"))
            await update.message.reply_text("\n".join(events) if events else "No hay eventos para esa fecha.")
        except Exception as exc:
            logging.exception("Error consultando Calendar: %s", exc)
            await update.message.reply_text("No pude consultar Google Calendar en este momento.")

    elif action == "search_youtube":
        if user_id != CREATOR_ID:
            await update.message.reply_text("La cuenta de Google solo esta disponible para el creador.")
            return
        if not await asyncio.to_thread(is_authenticated, user_id):
            await update.message.reply_text("Primero usa /auth para conectar YouTube.")
            return
        query = result.get("query", text)
        msg = await update.message.reply_text("\U0001f50d Buscando en YouTube...")
        try:
            videos = await asyncio.to_thread(search_youtube, user_id, query)
            if videos:
                log_activity(user_id, "buscar_youtube", query)
                await msg.edit_text("\n\n".join(videos), disable_web_page_preview=True)
            else:
                await msg.edit_text("No se encontraron videos.")
        except Exception as e:
            logging.exception("Error buscando en YouTube: %s", e)
            await msg.edit_text("No pude buscar en YouTube en este momento.")

    elif action == "search_drive":
        if user_id != CREATOR_ID:
            await update.message.reply_text("Google Drive solo esta disponible para el creador.")
            return
        if not await asyncio.to_thread(is_authenticated, user_id):
            await update.message.reply_text("Primero usa /auth para conectar Google Drive.")
            return
        query = result.get("query", text)
        msg = await update.message.reply_text("\U0001f50d Buscando en Drive...")
        try:
            files = await asyncio.to_thread(search_drive, user_id, query)
            if files:
                log_activity(user_id, "buscar_drive", query)
                await msg.edit_text("\n\n".join(files), disable_web_page_preview=True)
            else:
                await msg.edit_text("No se encontraron archivos.")
        except Exception as e:
            logging.exception("Error buscando en Drive: %s", e)
            await msg.edit_text("No pude buscar en Drive en este momento.")

    elif action == "search":
        query = result.get("query", text)
        msg = await update.message.reply_text("\U0001f50d Buscando informaci\u00f3n...")
        try:
            answer = await asyncio.to_thread(answer_question, text, query)
            log_activity(user_id, "buscar_internet", query)
            await msg.edit_text(answer)
        except Exception as e:
            logging.error(f"Search error: {e}")
            await msg.edit_text("No pude obtener la informaci\u00f3n en este momento.")

    elif action == "query":
        date_filter = result.get("filter", "all")
        reminders = get_reminders(user_id, date_filter)
        if not reminders:
            await update.message.reply_text("No tienes recordatorios para esa fecha.")
            return
        lines = ["\U0001f4cb Jefe, tus recordatorios:\n"]
        for r in reminders:
            rid, rtext, rdt_str, recurring = r
            line = f"\u2022 {rtext} \u2014 \U0001f4c5 {rdt_str}"
            if recurring:
                line += f" \U0001f504 {recurring}"
            lines.append(line)
        await update.message.reply_text("\n".join(lines))
        save_exchange(user_id, text, "Consult\u00f3 sus recordatorios", action)

    elif action == "update_reminder":
        target = (result.get("target") or result.get("text") or "").strip()
        matches = search_active_reminders(user_id, target) if target else []
        if len(matches) != 1:
            if not matches:
                await update.message.reply_text("No encontre el recordatorio que deseas cambiar.")
            else:
                options = "\n".join(f"#{rid} - {rtext} ({rdt})" for rid, rtext, rdt, _ in matches)
                await update.message.reply_text(f"Encontre varios. Indica uno con mas detalle:\n{options}")
            return
        reminder_id = matches[0][0]
        new_dt_str = result.get("datetime")
        if new_dt_str:
            try:
                new_dt = parse_local(new_dt_str)
            except ValueError:
                await update.message.reply_text("No entendi la nueva fecha.")
                return
            if new_dt <= local_now():
                await update.message.reply_text("La nueva fecha debe estar en el futuro.")
                return
        changed = update_reminder_details(
            reminder_id,
            user_id,
            text=result.get("new_text"),
            dt=new_dt_str,
            recurring=result.get("recurring"),
            end_date=result.get("until") or result.get("end_date"),
            lead_minutes=result.get("lead_minutes"),
        )
        if not changed:
            await update.message.reply_text("No pude actualizar ese recordatorio.")
            return
        refreshed = get_reminder_by_id(reminder_id, user_id)
        event_dt = parse_local(refreshed[3])
        alarm_dt = max(smart_alarm(event_dt, refreshed[8] or 0), local_now() + timedelta(seconds=1))
        replace_reminder_job(context.job_queue, alarm_dt, reminder_job_data(refreshed[:9]))
        await update.message.reply_text(f"Recordatorio actualizado: {refreshed[2]} - {refreshed[3]}")

    elif action == "delete":
        search_text = result.get("text", "")
        if not search_text:
            await update.message.reply_text("\u00bfQu\u00e9 recordatorio quieres eliminar?")
            return
        matches = search_active_reminders(user_id, search_text)
        if not matches:
            await update.message.reply_text(f"No encontr\u00e9 ning\u00fan recordatorio con '{search_text}'")
            return
        lines = ["Confirma cual recordatorio deseas eliminar:"]
        keyboard = []
        for reminder_id, reminder_text, reminder_dt, _ in matches:
            token = create_pending_action(
                user_id,
                "delete_reminder",
                {"reminder_id": reminder_id},
            )
            lines.append(f"#{reminder_id} - {reminder_text} ({reminder_dt})")
            keyboard.append([
                InlineKeyboardButton(
                    f"Eliminar #{reminder_id}",
                    callback_data=f"confirm:{token}:yes",
                )
            ])
        await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))

    elif action == "chat":
        try:
            msg = await asyncio.to_thread(generate_chat_response, text, history, memories)
        except Exception:
            msg = result.get("message", "\U0001f60a")
        await update.message.reply_text(msg)
        if get_preference(user_id, "voice_replies", False):
            await send_voice_reply(update, msg)
        save_exchange(user_id, text, msg, action)

    elif action == "clarify":
        msg = result.get("message", "Jefe, no entendi bien. Podes repetirlo de forma mas especifica?")
        await update.message.reply_text(msg)
        save_exchange(user_id, text, msg, action)

    elif action == "learning_insights":
        msg = await asyncio.to_thread(get_insights, user_id)
        await update.message.reply_text(msg, parse_mode="Markdown")
        save_exchange(user_id, text, msg, action)

    elif action == "generate_pdf":
        ptype = result.get("type", "content")
        if ptype == "expenses":
            path, msg = await asyncio.to_thread(generate_expense_report, user_id, "all")
        else:
            query = result.get("query", "")
            title = result.get("title", "Documento Osiris")
            search_result = await asyncio.to_thread(web_search_raw, query)
            content = f"Tema: {query}\n\n{search_result if search_result else 'Sin resultados.'}"
            path = await asyncio.to_thread(generate_text_pdf, title, content, "informe")
            msg = "PDF generado."
        if path:
            try:
                with open(path, "rb") as f:
                    await update.message.reply_document(f, filename=os.path.basename(path), caption=msg)
            finally:
                if os.path.exists(path):
                    os.remove(path)
        else:
            await update.message.reply_text(msg)
        log_activity(user_id, "generar_pdf", ptype)
        save_exchange(user_id, text, f"PDF: {ptype}", action)

    elif action == "create_task_list":
        name = result.get("name", "lista")
        create_task_list(user_id, name)
        log_activity(user_id, "crear_lista", name)
        save_exchange(user_id, text, f"Lista '{name}' creada", action)
        await update.message.reply_text(f"\U0001f4cb *Jefe*, lista '{name}' creada. Puedes agregar tareas con 'agrega X a {name}'")

    elif action == "add_task":
        list_name = result.get("list", "")
        task_text = result.get("text", "")
        priority = result.get("priority", 0)
        if not list_name or not task_text:
            await update.message.reply_text("No entend\u00ed a qu\u00e9 lista agregar.")
            return
        lists = search_lists(user_id, list_name)
        if not lists:
            await update.message.reply_text(f"No encontr\u00e9 la lista '{list_name}'.")
            return
        lid = lists[0][0]
        add_task_item(lid, task_text, priority, result.get("tags"))
        log_activity(user_id, "agregar_tarea", f"{task_text} -> {list_name}")
        save_exchange(user_id, text, f"Tarea agregada: {task_text}", action)
        await update.message.reply_text(f"\u2705 Agregue {task_text} a la lista '{lists[0][1]}'")

    elif action == "list_tasks":
        list_name = result.get("list", "")
        lists = search_lists(user_id, list_name) if list_name else get_task_lists(user_id)
        if not lists:
            await update.message.reply_text("No tienes listas creadas. Usa 'crea una lista de...'")
            return
        if not list_name:
            msg = "\U0001f4cb Jefe, tus listas:\n"
            for lid, lname, _ in lists:
                items = get_list_items(lid)
                done = sum(1 for i in items if i[2])
                total = len(items)
                msg += f"\n\u2022 {lname} ({done}/{total})"
            await update.message.reply_text(msg)
            return
        lid = lists[0][0]
        lname = lists[0][1]
        items = get_list_items(lid)
        if not items:
            await update.message.reply_text(f"La lista '{lname}' est\u00e1 vac\u00eda.")
            return
        lines = [f"\U0001f4cb {lname}\n"]
        for iid, itext, completed, priority, tags in items:
            status = "\u2705" if completed else "\u26ab"
            p = {0: "", 1: " \u203c\ufe0f", 2: " \U0001f6a8"}.get(priority, "")
            tag = f" [#{tags}]" if tags else ""
            lines.append(f"{status} {itext}{p}{tag}")
        await update.message.reply_text("\n".join(lines))

    elif action == "toggle_task":
        list_name = result.get("list", "")
        task_text = result.get("text", "")
        lists = search_lists(user_id, list_name) if list_name else get_task_lists(user_id)
        if not lists:
            await update.message.reply_text(f"No encontr\u00e9 la lista '{list_name}'.")
            return
        lid = lists[0][0]
        items = get_list_items(lid)
        found = None
        for item in items:
            if task_text.lower() in item[1].lower():
                found = item
                break
        if not found:
            await update.message.reply_text(f"No encontr\u00e9 '{task_text}' en la lista.")
            return
        new_status = toggle_task_item(found[0])
        estado = "marcada como hecha" if new_status else "desmarcada"
        log_activity(user_id, "toggle_tarea", f"{found[1]} -> {estado}")
        await update.message.reply_text(f"{'\u2705' if new_status else '\u26ab'} Tarea *{found[1]}* {estado}")

    elif action == "delete_task":
        list_name = result.get("list", "")
        task_text = result.get("text", "")
        lists = search_lists(user_id, list_name) if list_name else get_task_lists(user_id)
        if not lists:
            await update.message.reply_text(f"No encontr\u00e9 la lista '{list_name}'.")
            return
        lid = lists[0][0]
        items = get_list_items(lid)
        found = None
        for item in items:
            if task_text.lower() in item[1].lower():
                found = item
                break
        if not found:
            await update.message.reply_text(f"No encontr\u00e9 '{task_text}' en la lista.")
            return
        token = create_pending_action(
            user_id,
            "delete_task",
            {"item_id": found[0], "text": found[1]},
        )
        await update.message.reply_text(
            f"Eliminar la tarea '{found[1]}'?",
            reply_markup=confirmation_buttons(token, "Eliminar tarea"),
        )

    elif action == "delete_task_list":
        name = result.get("name", "")
        lists = search_lists(user_id, name) if name else get_task_lists(user_id)
        if not lists:
            await update.message.reply_text(f"No encontr\u00e9 la lista '{name}'.")
            return
        if not is_task_list_owner(user_id, lists[0][0]):
            await update.message.reply_text("Solo el propietario puede eliminar una lista compartida.")
            return
        token = create_pending_action(
            user_id,
            "delete_task_list",
            {"list_id": lists[0][0], "name": lists[0][1]},
        )
        await update.message.reply_text(
            f"Eliminar la lista completa '{lists[0][1]}'?",
            reply_markup=confirmation_buttons(token, "Eliminar lista"),
        )

    elif action == "ocr_image":
        context.user_data["ocr_pending"] = True
        await update.message.reply_text("\U0001f5bc *Jefe*, env\u00edame la foto y extraigo el texto.", parse_mode="Markdown")

    elif action == "record_expense":
        try:
            amount = float(result.get("amount", 0))
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            await update.message.reply_text("El monto del gasto debe ser mayor que cero.")
            return
        description = result.get("description", "")
        category = result.get("category")
        currency = str(result.get("currency") or "CRC").upper()
        if currency not in {"CRC", "USD", "EUR"}:
            currency = "CRC"
        duplicate = find_duplicate_expense(user_id, amount, description, currency)
        if duplicate:
            token = create_pending_action(
                user_id,
                "record_expense",
                {
                    "amount": amount,
                    "description": description,
                    "category": category,
                    "currency": currency,
                    "items": result.get("items") or [],
                },
            )
            await update.message.reply_text(
                f"Parece duplicado de un gasto registrado el {duplicate[2]}. Deseas guardarlo otra vez?",
                reply_markup=confirmation_buttons(token, "Registrar de todos modos"),
            )
            return
        expense_id = add_expense(user_id, amount, description, category, currency)
        add_expense_items(expense_id, result.get("items") or [])
        record_history(
            user_id,
            "record_expense",
            "expense",
            expense_id,
            after={"amount": amount, "description": description, "currency": currency},
            reversible=True,
        )
        log_activity(user_id, "registrar_gasto", f"{description}: {amount} {currency}")
        save_exchange(user_id, text, f"Gasto registrado: {description} {amount}", action)
        total = sum(row[0] for row in get_today_expenses(user_id) if row[3] == currency)
        cat_icon = {"comida":"\U0001f34e","transporte":"\U0001f697","servicios":"\U0001f4a1","ocio":"\U0001f3ac","salud":"\U0001f48a","hogar":"\U0001f3e0","otros":"\U0001f4b0"}.get(category, "\U0001f4b0")
        alert = budget_alert_text(user_id, category, currency)
        await update.message.reply_text(f"{cat_icon} Registrado: {description} - {amount:g} {currency}\nGastos del dia en {currency}: {total:g} {currency}{alert}")

    elif action == "expense_summary":
        expenses = get_today_expenses(user_id)
        if not expenses:
            await update.message.reply_text("\U0001f4b0 *Jefe*, no has gastado nada hoy.", parse_mode="Markdown")
            return
        lines = ["\U0001f4ca Gastos de hoy:\n"]
        for amt, desc, cat, cur in expenses:
            icon = {"comida":"\U0001f34e","transporte":"\U0001f697","servicios":"\U0001f4a1","ocio":"\U0001f3ac","salud":"\U0001f48a","hogar":"\U0001f3e0","otros":"\U0001f4b0"}.get(cat, "\U0001f4b0")
            lines.append(f"{icon} {desc} \u2014 {amt} {cur}")
        totals = {}
        for amount, _, _, currency in expenses:
            totals[currency] = totals.get(currency, 0) + amount
        totals_text = " / ".join(f"{amount:g} {currency}" for currency, amount in sorted(totals.items()))
        lines.append("\nTotales: " + totals_text)
        await update.message.reply_text("\n".join(lines))
        save_exchange(user_id, text, f"Consulto gastos del dia: {totals_text}", action)

    elif action == "create_routine":
        name = (result.get("name") or "rutina").strip()[:100]
        steps = result.get("steps") or []
        if not isinstance(steps, list) or not steps:
            await update.message.reply_text("Indica al menos un paso para la rutina.")
            return
        routine_id = create_routine(user_id, name, steps[:30])
        record_history(user_id, "create_routine", "routine", routine_id, after={"name": name}, reversible=False)
        await update.message.reply_text(f"Rutina '{name}' creada con {len(steps[:30])} pasos.")

    elif action == "list_routines":
        routines = get_routines(user_id)
        if not routines:
            await update.message.reply_text("Aun no tienes rutinas.")
            return
        await update.message.reply_text(
            "Rutinas:\n" + "\n".join(f"#{rid} {name} ({count} pasos)" for rid, name, active, count in routines)
        )

    elif action == "run_routine":
        name = (result.get("name") or "").strip()
        routine = get_routine(user_id, name)
        if not routine:
            await update.message.reply_text(f"No encontre la rutina '{name}'.")
            return
        routine_id, routine_name, steps = routine
        list_id = create_task_list(user_id, f"Rutina {routine_name} {local_now().strftime('%d-%m')}")
        for _, step_type, content, at_time in steps:
            add_task_item(list_id, content)
        record_history(user_id, "run_routine", "task_list", list_id, after={"routine_id": routine_id}, reversible=False)
        lines = [f"Rutina '{routine_name}' activada:"]
        lines.extend(f"- {content}" for _, _, content, _ in steps)
        await update.message.reply_text("\n".join(lines)[:3900])

    elif action == "create_habit":
        name = (result.get("name") or "").strip()[:120]
        if not name:
            await update.message.reply_text("Indica el nombre del habito.")
            return
        habit_id = create_habit(
            user_id,
            name,
            result.get("frequency") or "daily",
            max(1, int(result.get("target_count") or 1)),
        )
        record_history(user_id, "create_habit", "habit", habit_id, after={"name": name}, reversible=False)
        await update.message.reply_text(f"Habito creado: {name}.")

    elif action == "log_habit":
        name = (result.get("name") or "").strip()
        log_id = log_habit(user_id, name, float(result.get("value") or 1), result.get("note"))
        await update.message.reply_text(
            f"Progreso registrado para '{name}'." if log_id else f"No encontre el habito '{name}'."
        )

    elif action == "list_habits":
        habits = get_habits(user_id)
        if not habits:
            await update.message.reply_text("Aun no tienes habitos.")
            return
        lines = ["Habitos de hoy:"]
        lines.extend(f"- {name}: {value:g}/{target}" for _, name, _, target, value in habits)
        await update.message.reply_text("\n".join(lines))

    elif action == "create_goal":
        title = (result.get("title") or "").strip()[:200]
        if not title:
            await update.message.reply_text("Indica que meta deseas alcanzar.")
            return
        goal_id = create_goal(user_id, title, result.get("target_date"), result.get("steps") or [])
        await update.message.reply_text(f"Meta #{goal_id} creada: {title}.")

    elif action == "list_goals":
        goals = get_goals(user_id)
        if not goals:
            await update.message.reply_text("Aun no tienes metas activas.")
            return
        lines = ["Metas activas:"]
        lines.extend(f"#{gid} {title}: {progress}% ({done}/{steps} pasos)" for gid, title, target, status, progress, steps, done in goals)
        await update.message.reply_text("\n".join(lines))

    elif action == "add_important_date":
        title = (result.get("title") or "").strip()[:160]
        event_date = result.get("date")
        try:
            datetime.strptime(event_date or "", "%Y-%m-%d")
        except ValueError:
            await update.message.reply_text("La fecha debe incluir dia, mes y ano.")
            return
        date_id = add_important_date(
            user_id,
            title,
            event_date,
            bool(result.get("recurring", True)),
            int(result.get("lead_days") or 7),
        )
        await update.message.reply_text(f"Fecha importante #{date_id} guardada: {title}, {event_date}.")

    elif action == "list_important_dates":
        dates = get_upcoming_dates(user_id, int(result.get("days") or 60))
        if not dates:
            await update.message.reply_text("No tienes fechas importantes proximas.")
            return
        lines = ["Fechas importantes:"]
        lines.extend(f"- {title}: {candidate} (faltan {delta} dias)" for _, title, _, _, _, candidate, delta in dates)
        await update.message.reply_text("\n".join(lines))

    elif action == "plan_day":
        await update.message.reply_text(build_day_plan(user_id)[:3900])

    elif action == "configure_briefing":
        changed = []
        for key in ("morning_summary", "evening_summary", "weekly_pdf"):
            if key in result and result[key] is not None:
                set_preference(user_id, key, bool(result[key]))
                changed.append(f"{key}={bool(result[key])}")
        if "morning_hour" in result and result["morning_hour"] is not None:
            hour = min(23, max(0, int(result["morning_hour"])))
            set_preference(user_id, "morning_hour", hour)
            changed.append(f"morning_hour={hour}")
        await update.message.reply_text(
            "Configuracion actualizada: " + ", ".join(changed)
            if changed else
            "No indicaste que parte del resumen deseas cambiar."
        )

    elif action == "weekly_summary_pdf":
        path, caption = await asyncio.to_thread(generate_weekly_report, user_id)
        try:
            with open(path, "rb") as document:
                await update.message.reply_document(document, filename=os.path.basename(path), caption=caption)
        finally:
            if os.path.exists(path):
                os.remove(path)

    elif action == "list_documents":
        rows = list_documents(user_id)
        if not rows:
            await update.message.reply_text("Tu biblioteca esta vacia.")
            return
        lines = ["Biblioteca personal:"]
        lines.extend(f"#{doc_id} {title} ({file_type}, {chunks} fragmentos)" for doc_id, title, file_type, private, created_at, chunks in rows)
        await update.message.reply_text("\n".join(lines)[:3900])

    elif action == "query_documents":
        query = (result.get("query") or text).strip()
        chunks = search_documents(user_id, query)
        if not chunks:
            await update.message.reply_text("No encontre esa informacion en tus documentos.")
            return
        answer = await asyncio.to_thread(answer_from_documents, query, chunks)
        await update.message.reply_text(answer[:3900])

    elif action == "delete_document":
        try:
            document_id = int(result.get("id"))
        except (TypeError, ValueError):
            await update.message.reply_text("Indica el numero del documento que deseas eliminar.")
            return
        token = create_pending_action(user_id, "delete_document", {"document_id": document_id})
        await update.message.reply_text(
            f"Eliminar completamente el documento #{document_id}?",
            reply_markup=confirmation_buttons(token, "Eliminar documento"),
        )

    elif action == "capture_audio_document":
        context.user_data["document_audio_pending"] = True
        await update.message.reply_text("Enviame el audio y guardare su transcripcion en tu biblioteca.")

    elif action == "capture_image_document":
        context.user_data["document_image_pending"] = True
        await update.message.reply_text("Enviame la imagen y guardare el texto extraido en tu biblioteca.")

    elif action == "set_budget":
        category = (result.get("category") or "otros").strip().lower()
        currency = str(result.get("currency") or "CRC").upper()
        try:
            limit = float(result.get("monthly_limit") or 0)
            alert_percent = int(result.get("alert_percent") or 80)
        except (TypeError, ValueError):
            limit = 0
            alert_percent = 80
        if limit <= 0 or currency not in {"CRC", "USD", "EUR"}:
            await update.message.reply_text("Indica un limite positivo y una moneda valida.")
            return
        set_budget(user_id, category, currency, limit, min(100, max(1, alert_percent)))
        await update.message.reply_text(f"Presupuesto: {category} = {limit:g} {currency} al mes.")

    elif action == "list_budgets":
        rows = get_budget_status(user_id)
        if not rows:
            await update.message.reply_text("Aun no tienes presupuestos.")
            return
        lines = ["Presupuestos del mes:"]
        for category, currency, limit, alert_percent, spent in rows:
            percent = spent / limit * 100 if limit else 0
            lines.append(f"- {category}: {spent:,.2f}/{limit:,.2f} {currency} ({percent:.0f}%)")
        await update.message.reply_text("\n".join(lines))

    elif action == "add_subscription":
        try:
            amount = float(result.get("amount") or 0)
            datetime.strptime(result.get("next_due") or "", "%Y-%m-%d")
        except (TypeError, ValueError):
            await update.message.reply_text("Indica monto y proxima fecha de cobro.")
            return
        subscription_id = add_subscription(
            user_id,
            (result.get("name") or "suscripcion").strip()[:120],
            amount,
            str(result.get("currency") or "CRC").upper(),
            result["next_due"],
            result.get("frequency") or "monthly",
            result.get("category") or "servicios",
        )
        await update.message.reply_text(f"Suscripcion #{subscription_id} registrada.")

    elif action == "list_subscriptions":
        rows = get_subscriptions(user_id)
        if not rows:
            await update.message.reply_text("Aun no tienes suscripciones.")
            return
        lines = ["Suscripciones:"]
        lines.extend(f"#{sid} {name}: {amount:g} {currency}, {due}" for sid, name, amount, currency, category, due, frequency in rows)
        await update.message.reply_text("\n".join(lines))

    elif action == "mark_subscription_paid":
        try:
            subscription_id = int(result.get("id"))
        except (TypeError, ValueError):
            await update.message.reply_text("Indica el numero de la suscripcion.")
            return
        subscription = next((row for row in get_subscriptions(user_id) if row[0] == subscription_id), None)
        if not subscription:
            await update.message.reply_text("No encontre esa suscripcion.")
            return
        _, name, amount, currency, category, due, frequency = subscription
        expense_id = add_expense(user_id, amount, name, category, currency)
        record_history(user_id, "pay_subscription", "expense", expense_id, reversible=True)
        next_due = advance_subscription(user_id, subscription_id)
        await update.message.reply_text(f"Pago registrado. Proximo cobro de {name}: {next_due}.")

    elif action == "expense_comparison":
        comparison = get_monthly_expense_comparison(user_id)
        currencies = sorted(set(comparison["current"]) | set(comparison["previous"]))
        if not currencies:
            await update.message.reply_text("No hay gastos para comparar.")
            return
        lines = ["Comparacion mensual:"]
        for currency in currencies:
            current = comparison["current"].get(currency, 0)
            previous = comparison["previous"].get(currency, 0)
            delta = current - previous
            lines.append(f"- {currency}: actual {current:,.2f}, anterior {previous:,.2f}, diferencia {delta:+,.2f}")
        await update.message.reply_text("\n".join(lines))

    elif action == "export_expenses_csv":
        rows = get_expense_export_rows(user_id)
        if not rows:
            await update.message.reply_text("No tienes gastos para exportar.")
            return
        stream = StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["fecha", "descripcion", "categoria", "monto", "moneda"])
        writer.writerows(rows)
        document = BytesIO(stream.getvalue().encode("utf-8-sig"))
        document.name = f"gastos_osiris_{user_id}.csv"
        await update.message.reply_document(document=document, caption="Gastos compatibles con Excel.")

    elif action == "set_voice_replies":
        enabled = bool(result.get("enabled", True))
        set_preference(user_id, "voice_replies", enabled)
        await update.message.reply_text(
            "Respuestas de voz activadas." if enabled else "Respuestas de voz desactivadas."
        )

    elif action == "summarize_text":
        content = (result.get("content") or text).strip()
        summary = await asyncio.to_thread(summarize_content, content, result.get("style") or "breve")
        await update.message.reply_text(summary[:3900])

    elif action == "summarize_next_audio":
        context.user_data["summary_audio_pending"] = True
        await update.message.reply_text("Enviame el audio y te devolvere un resumen.")

    elif action == "draft_message":
        instructions = result.get("instructions") or text
        draft = await asyncio.to_thread(compose_text, instructions, result.get("tone") or "natural")
        await update.message.reply_text("Borrador:\n\n" + draft[:3800])

    elif action == "create_gmail_draft":
        if user_id != CREATOR_ID:
            await update.message.reply_text("Gmail solo esta disponible para el creador.")
            return
        if not await asyncio.to_thread(is_authenticated, user_id):
            await update.message.reply_text("Primero usa /auth para conectar Google.")
            return
        to_address = (result.get("to") or "").strip()
        subject = (result.get("subject") or "Borrador de Osiris").strip()
        body = (result.get("body") or "").strip()
        if not to_address or "@" not in to_address:
            await update.message.reply_text("Indica una direccion de correo valida.")
            return
        if not body:
            body = await asyncio.to_thread(compose_text, text, result.get("tone") or "formal")
        try:
            draft_id = await asyncio.to_thread(create_gmail_draft, user_id, to_address, subject, body)
            await update.message.reply_text(
                f"Borrador de Gmail creado: {draft_id}. Revisalo en Gmail antes de enviarlo."
            )
        except Exception as exc:
            logging.exception("Error creando borrador Gmail: %s", exc)
            await update.message.reply_text("No pude crear el borrador. Puede ser necesario volver a usar /auth.")

    elif action == "share_task_list":
        list_name = (result.get("list") or "").strip()
        contact_name = (result.get("contact") or "").strip()
        lists = search_lists(user_id, list_name)
        contact = get_contact(user_id, contact_name)
        if not lists or not contact:
            await update.message.reply_text("No encontre la lista o el contacto.")
            return
        list_id = lists[0][0]
        if not is_task_list_owner(user_id, list_id):
            await update.message.reply_text("Solo el propietario puede compartir esa lista.")
            return
        if not is_authorized(contact[1]):
            await update.message.reply_text("Ese contacto debe estar autorizado en Osiris.")
            return
        share_resource(user_id, "task_list", list_id, contact[1], result.get("permission") or "edit")
        await update.message.reply_text(f"Lista '{lists[0][1]}' compartida con {contact[0]}.")
        await context.bot.send_message(
            chat_id=contact[1],
            text=f"{update.effective_user.first_name or 'Un contacto'} compartio contigo la lista '{lists[0][1]}'.",
        )

    elif action == "list_shared":
        rows = get_shared_resources(user_id)
        if not rows:
            await update.message.reply_text("No tienes recursos compartidos contigo.")
            return
        lines = ["Compartidos contigo:"]
        lines.extend(f"- {resource_type}: {name or resource_id} ({permission})" for _, resource_type, resource_id, permission, owner, name in rows)
        await update.message.reply_text("\n".join(lines))

    elif action == "capture_inbox":
        content = (result.get("content") or result.get("text") or text).strip()[:4000]
        category = (result.get("category") or "inbox").strip()[:50]
        item_type = (result.get("item_type") or "note").strip()[:30]
        private = bool(result.get("private") or get_preference(user_id, "private_mode", False))
        item_id = add_inbox_item(user_id, content, category, item_type, private=private)
        await update.message.reply_text(f"Guardado en tu bandeja como #{item_id} [{category}].")

    elif action == "list_inbox":
        rows = get_inbox(user_id, result.get("category"))
        if not rows:
            await update.message.reply_text("Tu bandeja esta vacia.")
            return
        lines = ["Bandeja de entrada:"]
        for item_id, item_type, content, category, private, created_at in rows:
            lines.append(f"#{item_id} [{category}/{item_type}] {content[:120]}")
        await update.message.reply_text("\n".join(lines)[:3900])

    elif action == "archive_inbox":
        try:
            item_id = int(result.get("id"))
        except (TypeError, ValueError):
            await update.message.reply_text("Indica el numero del elemento que deseas archivar.")
            return
        changed = archive_inbox_item(user_id, item_id)
        await update.message.reply_text("Elemento archivado." if changed else "No encontre ese elemento.")

    elif action == "undo":
        undone = undo_last_action(user_id)
        await update.message.reply_text(
            f"Deshice la ultima accion reversible: {undone}."
            if undone else
            "No encontre una accion reciente que pueda deshacer con seguridad."
        )

    elif action == "set_private_mode":
        enabled = bool(result.get("enabled", True))
        set_preference(user_id, "private_mode", enabled)
        await update.message.reply_text("Modo privado activado." if enabled else "Modo privado desactivado.")

    elif action == "remember_fact":
        key = (result.get("key") or "dato").strip()[:100]
        value = (result.get("value") or "").strip()[:1000]
        if not value:
            await update.message.reply_text("No entendi que dato deseas que recuerde.")
            return
        memory_id = remember(
            user_id,
            key,
            value,
            ttl_days=result.get("ttl_days"),
            sensitive=bool(result.get("sensitive", False)),
        )
        record_history(user_id, "remember_fact", "memory", memory_id, after={"key": key}, reversible=False)
        await update.message.reply_text(f"Lo recordare: {key} = {value}")
        save_exchange(user_id, text, f"Memoria guardada: {key}", action)

    elif action == "recall_memory":
        query_text = (result.get("query") or "").strip()
        memories = get_memories(user_id, query_text or None, limit=10)
        if not memories:
            await update.message.reply_text("No tengo ningun recuerdo que coincida.")
            return
        lines = ["Esto recuerdo:"]
        lines.extend(f"- {key}: {value}" for key, value, _ in memories)
        await update.message.reply_text("\n".join(lines))

    elif action == "forget_memory":
        query_text = (result.get("query") or "").strip()
        if not query_text:
            await update.message.reply_text("Indica que dato deseas que olvide.")
            return
        removed = forget_memory(user_id, query_text)
        await update.message.reply_text(
            f"Olvide {removed} dato(s)." if removed else "No encontre ese dato en mi memoria."
        )

    elif action == "save_contact":
        name = (result.get("name") or "").strip()[:100]
        try:
            telegram_user_id = int(result.get("telegram_user_id"))
        except (TypeError, ValueError):
            await update.message.reply_text("Necesito el ID numerico de Telegram del contacto.")
            return
        if not name:
            await update.message.reply_text("Necesito un nombre para el contacto.")
            return
        if telegram_user_id != CREATOR_ID and not is_authorized(telegram_user_id):
            await update.message.reply_text("Ese usuario primero debe iniciar el bot y ser autorizado.")
            return
        save_contact(user_id, name, telegram_user_id)
        await update.message.reply_text(f"Contacto guardado: {name} ({telegram_user_id}).")

    elif action == "list_contacts":
        contacts = get_contacts(user_id)
        if not contacts:
            await update.message.reply_text("No tienes contactos guardados.")
            return
        await update.message.reply_text(
            "Contactos:\n" + "\n".join(f"- {name}: {telegram_id}" for name, telegram_id in contacts)
        )

    elif action == "delete_contact":
        name = (result.get("name") or "").strip()
        removed = delete_contact(user_id, name) if name else 0
        await update.message.reply_text(
            f"Contacto eliminado: {name}." if removed else "No encontre ese contacto."
        )

    elif action == "propose_commitment":
        commitment = (result.get("text") or text).strip()[:1000]
        dt_str = result.get("datetime")
        if not dt_str:
            await update.message.reply_text(
                "Detecte un posible compromiso, pero necesito saber cuando deberia recordartelo."
            )
            return
        try:
            dt = parse_local(dt_str)
        except ValueError:
            await update.message.reply_text("No entendi la fecha de ese posible compromiso.")
            return
        if dt <= local_now():
            await update.message.reply_text("Ese posible compromiso tiene una fecha pasada. Indica otra hora.")
            return
        token = create_pending_action(
            user_id,
            "create_commitment",
            {
                "text": commitment,
                "datetime": dt_str,
                "lead_minutes": max(0, int(result.get("lead_minutes") or 0)),
            },
        )
        await update.message.reply_text(
            f"Detecte este compromiso:\n{commitment}\n{dt_str}\n\nDeseas convertirlo en recordatorio?",
            reply_markup=confirmation_buttons(token, "Crear recordatorio"),
        )

    elif action == "start_meeting":
        active = get_active_meeting(user_id)
        if active:
            await update.message.reply_text(f"Ya esta activa la reunion #{active[0]}: {active[1]}.")
            return
        title = (result.get("title") or "Reunion").strip()[:200]
        meeting_id = start_meeting(user_id, title)
        await update.message.reply_text(
            f"Reunion #{meeting_id} iniciada: {title}. Puedes dictarme notas, decisiones y tareas."
        )

    elif action == "add_meeting_note":
        content = (result.get("content") or text).strip()[:4000]
        item_type = (result.get("item_type") or "note").strip().lower()
        if item_type not in {"note", "decision", "task"}:
            item_type = "note"
        item_id = add_meeting_item(
            user_id,
            content,
            item_type,
            (result.get("assignee") or "").strip()[:200] or None,
            result.get("due_date"),
        )
        await update.message.reply_text(
            f"Anotado como {item_type} en la reunion." if item_id else
            "No hay una reunion activa. Di: inicia una reunion de proyecto."
        )

    elif action == "end_meeting":
        ended = await finish_active_meeting(user_id)
        await update.message.reply_text(
            f"Reunion #{ended[0]} cerrada.\n\n{ended[2]}" if ended else
            "No hay una reunion activa."
        )

    elif action == "proactive_insights":
        await update.message.reply_text(build_proactive_insights(user_id))

    elif action == "generate_auth_code":
        if user_id != CREATOR_ID:
            await update.message.reply_text("\u26d4 Solo el creador puede generar c\u00f3digos.")
            return
        code = create_auth_code()
        log_activity(user_id, "generar_codigo", code)
        await update.message.reply_text(f"\U0001f511 *C\u00f3digo generado:* `{code}`\n\nCompartilo con tu amigo. V\u00e1lido por 24 horas.\nTu amigo debe usar: `/register {code}`", parse_mode="Markdown")

    else:
        logging.warning("Accion de IA no soportada: %s", action)
        await update.message.reply_text("No reconoci esa accion. Puedes expresarlo de otra forma?")

async def process_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user_id = update.effective_user.id
    private_mode = bool(get_preference(user_id, "private_mode", False))
    history = [] if private_mode else get_recent_history(user_id, limit=6)
    memories = get_memories(user_id, limit=10)
    result = await asyncio.to_thread(analyze_message, text, history, memories)
    logging.info("Acciones detectadas: %s", [item.get("action") for item in result.get("actions", [result])])
    actions_list = result.get("actions") or [result]
    for act in actions_list:
        action = act.get("action")
        if not action:
            logging.warning(f"No action in: {act}")
            try:
                msg = await asyncio.to_thread(generate_chat_response, text, history, memories)
            except Exception:
                msg = "\U0001f60a"
            await update.message.reply_text(msg)
            save_exchange(user_id, text, msg, "chat")
            continue
        try:
            await process_action(update, context, text, act, user_id, history, memories)
            if not private_mode:
                await asyncio.to_thread(record_action, user_id, act.get("action", "unknown"), act)
        except Exception as exc:
            logging.exception("Fallo ejecutando accion %s: %s", action, exc)
            await update.message.reply_text(f"No pude completar la accion '{action}', pero continue con las demas.")

async def check_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid == CREATOR_ID or is_authorized(uid):
        return True
    if uid not in _auth_notified:
        _auth_notified.add(uid)
        name = update.effective_user.first_name or "Alguien"
        await update.message.reply_text("\U0001f512 *Acceso denegado*. Necesit\u00e1s autorizaci\u00f3n del creador.", parse_mode="Markdown")
        if CREATOR_ID:
            await context.bot.send_message(
                chat_id=CREATOR_ID,
                text=f"\U0001f514 *Nuevo usuario:* {name} (ID: `{uid}`)\n\u00bfLo autorizas? Us\u00e1:\n/authorize {uid}",
                parse_mode="Markdown"
            )
    return False

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    if not within_rate_limit(update.effective_user.id):
        await update.message.reply_text("Estas enviando demasiadas solicitudes. Espera un momento.")
        return
    chat_type = update.effective_chat.type if update.effective_chat else "private"
    raw = update.message.text or ""

    if chat_type in ("group", "supergroup"):
        bot_username = (await context.bot.get_me()).username.lower()
        mentioned = bot_username in raw.lower() or re.search(r'(?:^|\s)[Oo]siris(?:\s|$|[ ,.!?])', raw)
        if not mentioned:
            return
        if bot_username in raw.lower():
            raw = raw.replace(f"@{bot_username}", "").strip()

    text = strip_wake_word(raw)
    if not text:
        await wake_greeting(update)
        return
    try:
        await process_text(update, context, text)
    except Exception as e:
        await update.message.reply_text("No pude procesar esa solicitud. El error quedo registrado.")
        logging.exception("process_text error: %s", e)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    if not within_rate_limit(update.effective_user.id):
        await update.message.reply_text("Estas enviando demasiadas solicitudes. Espera un momento.")
        return
    user_id = update.effective_user.id
    msg = await update.message.reply_text("\U0001f3a4 Procesando audio...")
    file_path = None
    try:
        file = await update.message.voice.get_file()
        file_path = f"voice_{update.message.message_id}.ogg"
        await file.download_to_drive(file_path)

        if context.user_data.pop("music_pending", False):
            song = await asyncio.to_thread(recognize_music, file_path)
            if song:
                log_activity(user_id, "identificar_cancion", song.split('\n')[0][:100])
                await msg.edit_text(song)
            else:
                await msg.edit_text("No pude identificar la cancion.")
            return

        transcription = await asyncio.to_thread(transcribe_audio, file_path)
        if not transcription.strip():
            await msg.edit_text("No escuch\u00e9 nada claro.")
            return
        t = strip_wake_word(transcription)
        await msg.edit_text(f"\U0001f4dd Transcrib\u00ed: \"{t}\"")
        if context.user_data.pop("summary_audio_pending", False):
            summary = await asyncio.to_thread(summarize_content, transcription, "breve y accionable")
            await msg.edit_text("Resumen del audio:\n\n" + summary[:3800])
            return
        if context.user_data.pop("document_audio_pending", False):
            doc_id, created = await asyncio.to_thread(
                add_document,
                user_id,
                f"Audio {local_now().strftime('%Y-%m-%d %H:%M')}",
                "audio",
                update.message.voice.file_id,
                transcription,
                bool(get_preference(user_id, "private_mode", False)),
            )
            await msg.edit_text(
                f"Audio transcrito y archivado como documento #{doc_id}."
                if created else
                f"Ese audio ya estaba archivado como documento #{doc_id}."
            )
            return
        if not t:
            await wake_greeting(update)
            return
        await process_text(update, context, t)
    except Exception as e:
        await msg.edit_text("No pude procesar el audio en este momento.")
        logging.exception("Voice error: %s", e)
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    if not within_rate_limit(update.effective_user.id):
        await update.message.reply_text("Estas enviando demasiadas solicitudes. Espera un momento.")
        return
    user_id = update.effective_user.id
    caption = strip_wake_word(update.message.caption or "")
    msg = await update.message.reply_text("\U0001f5bc Procesando...")
    file_path = None
    try:
        file = await update.message.photo[-1].get_file()
        ext = "jpg"
        file_path = f"photo_{update.message.message_id}.{ext}"
        await file.download_to_drive(file_path)

        ocr_keywords = ["extrae", "texto", "ocr", "lee", "escribe", "saca", "transcribe", "factura", "flyer"]
        document_pending = context.user_data.pop("document_image_pending", False)
        is_ocr = any(kw in (caption or "").lower() for kw in ocr_keywords) or context.user_data.pop("ocr_pending", False) or document_pending

        if is_ocr:
            text = await asyncio.to_thread(ocr_image, file_path)
            log_activity(user_id, "ocr_imagen", text[:100])
            if document_pending:
                doc_id, created = await asyncio.to_thread(
                    add_document,
                    user_id,
                    caption or f"Imagen {local_now().strftime('%Y-%m-%d %H:%M')}",
                    "image",
                    update.message.photo[-1].file_id,
                    text,
                    bool(get_preference(user_id, "private_mode", False)),
                )
                await msg.edit_text(
                    f"Imagen leida y archivada como documento #{doc_id}."
                    if created else
                    f"Esa imagen ya estaba archivada como documento #{doc_id}."
                )
                return
            amounts = re.findall(r'(?:total|monto|pagar|importe|neto)[:\s]*\$\s*([\d,.]+)', text, re.I)
            if not amounts:
                amounts = re.findall(r'(?:total|monto|pagar|importe|neto)[:\s]*([\d,.]+)', text, re.I)
            if not amounts:
                amounts = re.findall(r'\$\s*([\d,.]+)', text)
            if amounts:
                total = parse_amount(amounts[-1])
                currency = "USD" if "$" in text and "CRC" not in text.upper() and "\u20a1" not in text else "CRC"
                items = extract_receipt_items(text)
                token = create_pending_action(
                    user_id,
                    "record_expense",
                    {
                        "amount": total,
                        "currency": currency,
                        "description": "factura",
                        "category": "otros",
                        "items": items,
                    },
                )
                await msg.edit_text(
                    f"Texto extraido:\n\n{text}\n\nTotal detectado: {total:g} {currency}. "
                    f"Productos detectados: {len(items)}. Deseas registrarlo como gasto?",
                    reply_markup=confirmation_buttons(token, "Registrar gasto"),
                )
            else:
                await msg.edit_text(f"Texto extraido:\n\n{text}")
        else:
            prompt = caption or "Describe esta imagen en detalle."
            desc = await asyncio.to_thread(analyze_image, file_path, prompt)
            log_activity(user_id, "vision_imagen", desc[:100])
            await msg.edit_text(f"Lo que veo:\n\n{desc}")
    except Exception as e:
        await msg.edit_text("No pude procesar la imagen en este momento.")
        logging.exception("Photo error: %s", e)
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

async def post_init(app):
    schedule_from_db(app)
    t21 = time(hour=21, minute=0, tzinfo=TZ)
    t2030 = time(hour=20, minute=30, tzinfo=TZ)
    t1815 = time(hour=18, minute=15, tzinfo=TZ)
    for hour in range(24):
        app.job_queue.run_daily(
            daily_reminders,
            time=time(hour=hour, minute=0, tzinfo=TZ),
            name=f"daily_reminders_{hour}",
        )
    app.job_queue.run_daily(evening_summary, time=t21, name="evening_summary")
    app.job_queue.run_daily(weekly_summary, time=t2030, name="weekly_summary")
    app.job_queue.run_daily(proactive_review, time=t1815, name="proactive_review")
    app.job_queue.run_repeating(reconcile_reminders, interval=300, first=300, name="reconcile_reminders")
    app.job_queue.run_repeating(system_self_check, interval=1800, first=60, name="system_self_check")
    logging.info("Resumen matutino configurado por usuario (6:00 por defecto)")
    logging.info("Resumen nocturno agendado a las 21:00")


async def reconcile_reminders(context: ContextTypes.DEFAULT_TYPE):
    schedule_from_db(context.application)


async def error_handler(update, context):
    logging.error("Error no controlado procesando una actualizacion", exc_info=context.error)
    message = getattr(update, "effective_message", None) if update else None
    if message:
        try:
            await message.reply_text("Ocurrio un error interno. Lo registre para revisarlo.")
        except Exception:
            logging.exception("No se pudo enviar el mensaje de error")

async def daily_reminders(context: ContextTypes.DEFAULT_TYPE):
    reminders = get_all_active()
    users = set(get_authorized_user_ids())
    users.update(row[1] for row in reminders)
    if CREATOR_ID:
        users.add(CREATOR_ID)
    today = local_now().strftime("%Y-%m-%d")
    current_hour = local_now().hour
    for uid in users:
        if not get_preference(uid, "morning_summary", True):
            continue
        try:
            preferred_hour = max(0, min(int(get_preference(uid, "morning_hour", 6)), 23))
        except (TypeError, ValueError):
            preferred_hour = 6
        if preferred_hour != current_hour:
            continue
        if get_preference(uid, "last_morning_summary_date") == today:
            continue
        message = "Buenos dias, Jefe.\n\n" + build_day_plan(uid)
        try:
            await context.bot.send_message(chat_id=uid, text=message[:3900])
            set_preference(uid, "last_morning_summary_date", today)
        except Exception as e:
            logging.error(f"Error daily reminders for user {uid}: {e}")


async def proactive_review(context: ContextTypes.DEFAULT_TYPE):
    users = set(get_authorized_user_ids())
    if CREATOR_ID:
        users.add(CREATOR_ID)
    for uid in users:
        if not get_preference(uid, "proactive_suggestions", True):
            continue
        message = build_proactive_insights(uid)
        if message.startswith("No detecte"):
            continue
        try:
            await context.bot.send_message(chat_id=uid, text=message)
        except Exception as exc:
            logging.error("Error enviando sugerencias proactivas a %s: %s", uid, exc)


async def system_self_check(context: ContextTypes.DEFAULT_TYPE):
    try:
        previous = {name: status for name, status, _, _ in get_system_status()}
    except Exception:
        previous = {}
    failures = []
    try:
        await asyncio.to_thread(database_self_check)
        await asyncio.to_thread(record_system_check, "database", "ok", "Consulta SELECT 1")
    except Exception as exc:
        failures.append(("database", str(exc)))
    scheduler_ok = bool(context.job_queue.get_jobs_by_name("reconcile_reminders"))
    try:
        await asyncio.to_thread(
            record_system_check,
            "scheduler",
            "ok" if scheduler_ok else "error",
            "Reconciliacion activa" if scheduler_ok else "Falta el trabajo reconcile_reminders",
        )
    except Exception as exc:
        if not any(name == "database" for name, _ in failures):
            failures.append(("database", str(exc)))
    if not scheduler_ok:
        failures.append(("scheduler", "Falta la reconciliacion de recordatorios"))
    failing_names = {name for name, _ in failures}
    _system_alerts.difference_update(set(previous) - failing_names)
    new_failures = [
        (name, details)
        for name, details in failures
        if previous.get(name) != "error" and name not in _system_alerts
    ]
    _system_alerts.update(name for name, _ in failures)
    if CREATOR_ID and new_failures:
        try:
            await context.bot.send_message(
                chat_id=CREATOR_ID,
                text="Autodiagnostico de Osiris:\n" + "\n".join(
                    f"- {name}: {details[:180]}" for name, details in new_failures
                ),
            )
        except Exception:
            logging.exception("No se pudo notificar el fallo de autodiagnostico")

async def evening_summary(context: ContextTypes.DEFAULT_TYPE):
    users = set(get_authorized_user_ids())
    if CREATOR_ID:
        users.add(CREATOR_ID)
    for uid in users:
        if not get_preference(uid, "evening_summary", True):
            continue
        rows = get_today_activity(uid)
        if not rows:
            continue
        reminder_count = sum(1 for r in rows if r[0] in ("crear_recordatorio", "crear_recordatorio_busqueda", "crear_recordatorio_amigo"))
        search_count = sum(1 for r in rows if r[0] == "buscar_internet")
        music_count = sum(1 for r in rows if r[0] == "identificar_cancion")
        event_count = sum(1 for r in rows if r[0] == "crear_evento")
        yt_count = sum(1 for r in rows if r[0] == "buscar_youtube")
        drive_count = sum(1 for r in rows if r[0] == "buscar_drive")
        delete_count = sum(1 for r in rows if r[0] == "eliminar_recordatorio")
        expenses = get_today_expenses(uid)
        totals_spent = {}
        for amount, _, _, currency in expenses:
            totals_spent[currency] = totals_spent.get(currency, 0) + amount
        lines = ["\U0001f303 *Buenas noches, Jefe!* Resumen del d\u00eda:\n"]
        if reminder_count:
            lines.append(f"\U0001f514 {reminder_count} recordatorio(s)")
        if music_count:
            lines.append(f"\U0001f3b5 {music_count} canci\u00f3n(es)")
        if search_count:
            lines.append(f"\U0001f50d {search_count} b\u00fasqueda(s)")
        if event_count:
            lines.append(f"\U0001f4c5 {event_count} evento(s)")
        if yt_count:
            lines.append(f"\U0001f4fa {yt_count} b\u00fasqueda(s) en YouTube")
        if drive_count:
            lines.append(f"\U0001f4c1 {drive_count} b\u00fasqueda(s) en Drive")
        if delete_count:
            lines.append(f"\U0001f5d1\ufe0f {delete_count} eliminaci\u00f3n(es)")
        if expenses:
            totals_text = " / ".join(f"{amount:g} {currency}" for currency, amount in sorted(totals_spent.items()))
            lines.append(f"\U0001f4b0 Gastos: {totals_text} ({len(expenses)} compras)")
        lines.append(f"\n\u2728 *{len(rows)}* actividad(es) en total")
        try:
            await context.bot.send_message(chat_id=uid, text="\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Error evening summary for user {uid}: {e}")


async def weekly_summary(context: ContextTypes.DEFAULT_TYPE):
    if local_now().weekday() != 6:
        return
    users = set(get_authorized_user_ids())
    if CREATOR_ID:
        users.add(CREATOR_ID)
    for uid in users:
        if not get_preference(uid, "weekly_pdf", True):
            continue
        path = None
        try:
            path, caption = await asyncio.to_thread(generate_weekly_report, uid)
            with open(path, "rb") as document:
                await context.bot.send_document(
                    chat_id=uid,
                    document=document,
                    filename=os.path.basename(path),
                    caption=caption,
                )
        except Exception as exc:
            logging.exception("Error enviando resumen semanal a %s: %s", uid, exc)
        finally:
            if path and os.path.exists(path):
                os.remove(path)

def main():
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    init_db()
    ptb_app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("auth", auth))
    ptb_app.add_handler(CommandHandler("panel", panel))
    ptb_app.add_handler(CommandHandler("myid", myid))
    ptb_app.add_handler(CommandHandler("authorize", authorize))
    ptb_app.add_handler(CommandHandler("deauthorize", deauthorize))
    ptb_app.add_handler(CommandHandler("register", register))
    ptb_app.add_handler(CommandHandler("exportar", export_data_command))
    ptb_app.add_handler(CommandHandler("backup", backup_command))
    ptb_app.add_handler(CommandHandler("borrardatos", delete_data_command))
    ptb_app.add_handler(CommandHandler("desconectargoogle", disconnect_google_command))
    ptb_app.add_handler(CommandHandler("estado", status_command))
    ptb_app.add_handler(CommandHandler("actualizaciones", updates_command))
    ptb_app.add_handler(CommandHandler("deshacer", undo_command))
    ptb_app.add_handler(CommandHandler("privado", private_command))
    ptb_app.add_handler(CommandHandler("inbox", inbox_command))
    ptb_app.add_handler(CommandHandler("config", config_command))
    ptb_app.add_handler(CommandHandler("plan", plan_command))
    ptb_app.add_handler(CommandHandler("habitos", habits_command))
    ptb_app.add_handler(CommandHandler("rutinas", routines_command))
    ptb_app.add_handler(CommandHandler("metas", goals_command))
    ptb_app.add_handler(CommandHandler("fechas", dates_command))
    ptb_app.add_handler(CommandHandler("resumensemanal", weekly_pdf_command))
    ptb_app.add_handler(CommandHandler("documentos", documents_command))
    ptb_app.add_handler(CommandHandler("presupuestos", budgets_command))
    ptb_app.add_handler(CommandHandler("suscripciones", subscriptions_command))
    ptb_app.add_handler(CommandHandler("gastoscsv", expenses_csv_command))
    ptb_app.add_handler(CommandHandler("voz", voice_mode_command))
    ptb_app.add_handler(CommandHandler("compartidos", shared_command))
    ptb_app.add_handler(CommandHandler("reunion", meeting_command))
    ptb_app.add_handler(CommandHandler("sugerencias", insights_command))
    ptb_app.add_handler(CallbackQueryHandler(handle_callback))
    ptb_app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    ptb_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    ptb_app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    ptb_app.add_error_handler(error_handler)

    if DATABASE_URL:
        if not WEBHOOK_SECRET:
            raise RuntimeError("TELEGRAM_WEBHOOK_SECRET es obligatorio en modo webhook")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", WEBHOOK_SECRET):
            raise RuntimeError("TELEGRAM_WEBHOOK_SECRET debe usar solo A-Z, a-z, 0-9, _ y -")
        from dashboard import app as flask_app
        from flask import request
        from telegram import Update as TgUpdate
        @flask_app.route("/webhook", methods=["POST"])
        def webhook():
            provided_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not hmac.compare_digest(provided_secret, WEBHOOK_SECRET):
                return "Forbidden", 403
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return "Bad Request", 400
            try:
                update = TgUpdate.de_json(payload, ptb_app.bot)
            except Exception:
                logging.warning("Webhook rechazado: actualizacion invalida")
                return "Bad Request", 400
            loop.call_soon_threadsafe(ptb_app.update_queue.put_nowait, update)
            return "OK", 200
        @flask_app.route("/oauth2/callback", methods=["GET"])
        def oauth2_callback():
            error = request.args.get("error")
            if error:
                logging.warning("Google OAuth rechazado: %s", error)
                return "Conexion con Google cancelada.", 400
            state = request.args.get("state", "")
            code = request.args.get("code", "")
            if not state or not code:
                return "Solicitud OAuth incompleta.", 400
            try:
                user_id = complete_auth(state, code)
                asyncio.run_coroutine_threadsafe(
                    ptb_app.bot.send_message(
                        chat_id=user_id,
                        text="Google conectado correctamente. Calendar, Drive y YouTube ya estan disponibles.",
                    ),
                    loop,
                )
                return "Google conectado correctamente. Ya puedes cerrar esta pagina.", 200
            except Exception as exc:
                logging.exception("Fallo completando Google OAuth: %s", exc)
                return "No se pudo completar la conexion con Google.", 400
        loop.run_until_complete(ptb_app.initialize())
        loop.run_until_complete(post_init(ptb_app))
        loop.run_until_complete(ptb_app.start())
        loop.run_until_complete(ptb_app.bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET,
            allowed_updates=Update.ALL_TYPES,
        ))
        logging.info("Webhook configurado en Render")
        t = threading.Thread(target=run_dashboard, daemon=True)
        t.start()
        loop.run_forever()
    else:
        t = threading.Thread(target=run_dashboard, daemon=True)
        t.start()
        ptb_app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
