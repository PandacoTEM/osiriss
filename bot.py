import os
import re
import threading
import logging
from datetime import datetime, timedelta, time
from tzlocal import get_localzone
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from database import init_db, add_reminder, get_all_active, get_reminders, deactivate_by_id, deactivate_by_text, update_datetime, log_activity, get_today_activity, save_message, get_recent_history, create_task_list, add_task_item, get_task_lists, get_list_items, toggle_task_item, delete_task_list, delete_task_item, search_lists, add_expense, get_today_expenses, get_today_total, get_recent_expenses, authorize_user, deauthorize_user, is_authorized, create_auth_code, redeem_auth_code
from ai_handler import analyze_message, transcribe_audio, answer_question, analyze_image, ocr_image
from web_search import search as web_search
from music_recognizer import recognize as recognize_music
from auth import get_auth_url, exchange_code, is_authenticated
from google_tools import create_event, search_youtube, search_drive
from dashboard import run_dashboard

load_dotenv()
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TZ = get_localzone()

BOT_USERNAME = "Orisis_diosa_bot"
CREATOR_ID = int(os.getenv("CREATOR_ID", 0))
_auth_notified = set()

MEMORY_ACTIONS = {"create", "create_search", "create_friend_reminder", "delete", "create_event", "query"}

def save_exchange(user_id, user_msg, bot_response, action):
    if action in MEMORY_ACTIONS:
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
    if alarm.hour < EARLIEST_ALARM_HOUR:
        alarm = alarm.replace(hour=21, minute=0, second=0) - timedelta(days=1)
    return alarm

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
            '\u2022 "Extra\u00e9 el texto de esta factura" + foto\n\n'
            "\U0001f4a1 *Funciones principales:*\n"
            "\u2705 Recordatorios con fecha, recurrencia y prioridad\n"
            "\u2705 B\u00fasqueda en internet con resumen IA\n"
            "\u2705 Reconocimiento de m\u00fasica (mand\u00e1 un audio)\n"
            "\u2705 Visi\u00f3n en im\u00e1genes y OCR en facturas\n"
            "\u2705 Google Calendar, YouTube y Drive con /auth\n"
            "\u2705 Listas de tareas pendientes\n"
            "\u2705 Registro de gastos diarios\n"
            "\u2705 Dashboard web con /panel\n\n"
            "\U0001f4cb *Res\u00famenes autom\u00e1ticos:*\n"
            "\u2022 6:00 AM \u2192 Recordatorios del d\u00eda\n"
            "\u2022 9:00 PM \u2192 Resumen de actividades y gastos\n\n"
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
            '\u2022 Mandame una foto diciendo "extrae el texto" y lo leo\n\n'
            "\u00a1Solo dec\u00ed 'Osiris' seguido de lo que necesit\u00e1s y yo me encargo!"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_authenticated():
        await update.message.reply_text("Ya est\u00e1s autenticado con Google \u2705")
        return
    try:
        url, flow = get_auth_url()
        context.user_data["auth_flow"] = flow
        await update.message.reply_text(
            f"1. Abre este link:\n{url}\n\n"
            "2. Inicia sesi\u00f3n con tu Google\n"
            "3. Copia el c\u00f3digo que te da\n"
            "4. P\u00e9galo aqu\u00ed en el chat"
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pwd = os.getenv("DASHBOARD_PASSWORD", "osiris123")
    await update.message.reply_text(
        f"\U0001f9e0 *Panel Osiris*\n\nAbre tu navegador y visita:\n"
        f"http://localhost:5000/?pwd={pwd}\n\n"
        "Si est\u00e1s en Render, usa la URL externa.",
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

def schedule_from_db(app):
    reminders = get_all_active()
    now = local_now()
    for row in reminders:
        try:
            rid, uid, text, dt_str, recurring, search_q, friend_name, end_date, lead_minutes = row
            dt = parse_local(dt_str)
            alarm_dt = smart_alarm(dt, lead_minutes or 0)
            if alarm_dt <= now:
                if not recurring:
                    deactivate_by_id(rid)
                    continue
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
            app.job_queue.run_once(
                send_reminder,
                when=alarm_dt,
                data={"rid": rid, "uid": uid, "text": text, "recurring": recurring, "dt_str": fmt_local(dt), "search_query": search_q, "friend_name": friend_name, "end_date": end_date, "lead_minutes": lead_minutes or 0},
                name=str(rid)
            )
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
        try:
            nxt = dt.replace(year=year, month=month)
        except ValueError:
            nxt = dt + timedelta(days=30)
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
    logging.info(f"Recordatorio {data['rid']}: {data['text']}")
    lead = data.get("lead_minutes") or 0
    if data.get("friend_name"):
        prefix = f"\U0001f4e8 *Para {data['friend_name']}*, Jefe: "
    elif lead:
        prefix = f"\u23f0 *Jefe*, recuerda que a las {data['dt_str'].split()[1]} tienes: "
    else:
        prefix = "\u23f0 *Jefe*, "
    await context.bot.send_message(
        chat_id=data["uid"],
        text=f"{prefix}{data['text']}",
        parse_mode="Markdown"
    )
    if data.get("search_query"):
        logging.info(f"Buscando: {data['search_query']}")
        try:
            answer = answer_question(data['text'], data['search_query'])
            await context.bot.send_message(chat_id=data["uid"], text=answer, parse_mode="Markdown")
        except Exception as e:
            try:
                await context.bot.send_message(chat_id=data["uid"], text=answer)
            except Exception:
                await context.bot.send_message(chat_id=data["uid"], text="No pude obtener la informaci\u00f3n.")
            logging.error(f"Search error en recordatorio: {e}")
    if data.get("recurring"):
        current_dt = parse_local(data["dt_str"])
        next_dt = calc_next(current_dt, data["recurring"], data.get("end_date"))
        if next_dt is None:
            await context.bot.send_message(chat_id=data["uid"], text=f"\U0001f4cc *Jefe*, este fue el \u00faltimo recordatorio de '{data['text']}' (periodo terminado).", parse_mode="Markdown")
            deactivate_by_id(data["rid"])
            return
        next_str = fmt_local(next_dt)
        update_datetime(data["rid"], next_str)
        next_alarm = smart_alarm(next_dt, data.get("lead_minutes") or 0)
        context.job_queue.run_once(
            send_reminder,
            when=next_alarm,
            data={"rid": data["rid"], "uid": data["uid"], "text": data["text"], "recurring": data["recurring"], "dt_str": next_str, "search_query": data.get("search_query"), "friend_name": data.get("friend_name"), "end_date": data.get("end_date"), "lead_minutes": data.get("lead_minutes") or 0},
            name=str(data["rid"])
        )
    else:
        deactivate_by_id(data["rid"])

async def process_action(update, context, text, result, user_id):
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
        rid = add_reminder(user_id, reminder_text, dt_str, recurring, end_date=end_date, lead_minutes=result.get("lead_minutes", 0))
        log_activity(user_id, "crear_recordatorio", f"{reminder_text} - {dt_str}{f' ({recurring})' if recurring else ''}{f' hasta {end_date}' if end_date else ''}")
        now = local_now()
        alarm_dt = smart_alarm(dt, result.get("lead_minutes", 0))
        if alarm_dt > now:
            context.job_queue.run_once(
                send_reminder,
                when=alarm_dt,
                data={"rid": rid, "uid": user_id, "text": reminder_text, "recurring": recurring, "dt_str": dt_str, "search_query": None, "friend_name": None, "end_date": end_date, "lead_minutes": result.get("lead_minutes", 0)},
                name=str(rid)
            )
        elif dt > now:
            await context.bot.send_message(chat_id=user_id, text=f"\U0001f4ac *Jefe, recordado:* {reminder_text}", parse_mode="Markdown")
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
        rid = add_reminder(user_id, reminder_text, dt_str, recurring, search_query=query, end_date=result.get("until") or result.get("end_date"), lead_minutes=result.get("lead_minutes", 0))
        log_activity(user_id, "crear_recordatorio_busqueda", f"{reminder_text} - {dt_str} (buscar: {query})")
        now = local_now()
        alarm_dt = smart_alarm(dt, result.get("lead_minutes", 0))
        if alarm_dt > now:
            context.job_queue.run_once(
                send_reminder,
                when=alarm_dt,
                data={"rid": rid, "uid": user_id, "text": reminder_text, "recurring": recurring, "dt_str": dt_str, "search_query": query, "friend_name": None, "end_date": result.get("until") or result.get("end_date"), "lead_minutes": result.get("lead_minutes", 0)},
                name=str(rid)
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
        rid = add_reminder(user_id, reminder_text, dt_str, recurring, friend_name=friend_name, end_date=result.get("until") or result.get("end_date"), lead_minutes=result.get("lead_minutes", 0))
        log_activity(user_id, "crear_recordatorio_amigo", f"Para {friend_name}: {reminder_text} - {dt_str}")
        now = local_now()
        alarm_dt = smart_alarm(dt, result.get("lead_minutes", 0))
        if alarm_dt > now:
            context.job_queue.run_once(
                send_reminder,
                when=alarm_dt,
                data={"rid": rid, "uid": user_id, "text": reminder_text, "recurring": recurring, "dt_str": dt_str, "search_query": None, "friend_name": friend_name, "end_date": result.get("until") or result.get("end_date"), "lead_minutes": result.get("lead_minutes", 0)},
                name=str(rid)
            )
        await update.message.reply_text(f"\u2705 *Jefe*, recordatorio para *{friend_name}* guardado:\n\n'{reminder_text}'\n\U0001f4c5 {dt_str}", parse_mode="Markdown")
        save_exchange(user_id, text, f"Recordatorio para {friend_name}: {reminder_text}", action)

    elif action == "identify_music":
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
        lines = [f"\U0001f4cb *Resumen del d\u00eda, Jefe!*\n"]
        total = len(rows)
        if total == 0:
            lines.append("A\u00fan no has hecho nada hoy \U0001f634")
        else:
            lines.append(f"En total hiciste *{total}* cosas:\n")
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
            lines.append(f"\n\U0001f4c4 *\u00daltimas actividades:*")
            for r in rows[-5:]:
                aname = {"crear_recordatorio":"\u2795 Recordatorio","crear_recordatorio_busqueda":"\u2795 Recordatorio + b\u00fasqueda","crear_recordatorio_amigo":"\u2795 Recordatorio amigo","buscar_internet":"\U0001f50d Busc\u00f3","identificar_cancion":"\U0001f3b5 Canci\u00f3n","crear_evento":"\U0001f4c5 Evento","buscar_youtube":"\U0001f4fa YouTube","buscar_drive":"\U0001f4c1 Drive","eliminar_recordatorio":"\U0001f5d1\ufe0f Elimin\u00f3"}.get(r[0], r[0])
                lines.append(f"  \u2022 {aname}{': '+r[1][:60] if r[1] else ''} \u2014 {r[2].split()[1]}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    elif action == "create_event":
        if not is_authenticated():
            await update.message.reply_text("Primero usa /auth para conectar Google Calendar.")
            return
        summary = result.get("summary", "")
        dt_str = result.get("datetime")
        duration = result.get("duration", 60)
        if not dt_str:
            await update.message.reply_text("No entend\u00ed la fecha y hora.")
            return
        try:
            link = create_event(summary, dt_str, duration)
            if link:
                log_activity(user_id, "crear_evento", f"{summary} - {dt_str}")
                await update.message.reply_text(f"\U0001f4c5 *Evento creado:* {summary}\n\U0001f517 [Ver en Calendar]({link})", parse_mode="Markdown")
                save_exchange(user_id, text, f"Evento creado: {summary}", action)
            else:
                await update.message.reply_text("No se pudo crear el evento.")
        except Exception as e:
            await update.message.reply_text(f"Error al crear evento: {e}")

    elif action == "search_youtube":
        if not is_authenticated():
            await update.message.reply_text("Primero usa /auth para conectar YouTube.")
            return
        query = result.get("query", text)
        msg = await update.message.reply_text(f"\U0001f50d Buscando en YouTube...")
        try:
            videos = search_youtube(query)
            if videos:
                log_activity(user_id, "buscar_youtube", query)
                await msg.edit_text("\n\n".join(videos), parse_mode="Markdown", disable_web_page_preview=True)
            else:
                await msg.edit_text("No se encontraron videos.")
        except Exception as e:
            await msg.edit_text(f"Error: {e}")

    elif action == "search_drive":
        if not is_authenticated():
            await update.message.reply_text("Primero usa /auth para conectar Google Drive.")
            return
        query = result.get("query", text)
        msg = await update.message.reply_text(f"\U0001f50d Buscando en Drive...")
        try:
            files = search_drive(query)
            if files:
                log_activity(user_id, "buscar_drive", query)
                await msg.edit_text("\n\n".join(files), parse_mode="Markdown", disable_web_page_preview=True)
            else:
                await msg.edit_text("No se encontraron archivos.")
        except Exception as e:
            await msg.edit_text(f"Error: {e}")

    elif action == "search":
        query = result.get("query", text)
        msg = await update.message.reply_text(f"\U0001f50d Buscando informaci\u00f3n...")
        try:
            answer = answer_question(text, query)
            log_activity(user_id, "buscar_internet", query)
            await msg.edit_text(answer, parse_mode="Markdown")
        except Exception as e:
            try:
                await msg.edit_text(answer)
            except Exception:
                await msg.edit_text("No pude obtener la informaci\u00f3n en este momento.")
            logging.error(f"Search error: {e}")

    elif action == "query":
        date_filter = result.get("filter", "all")
        reminders = get_reminders(user_id, date_filter)
        if not reminders:
            await update.message.reply_text("No tienes recordatorios para esa fecha.")
            return
        lines = ["\U0001f4cb *Jefe, tus recordatorios:*\n"]
        for r in reminders:
            rid, rtext, rdt_str, recurring = r
            line = f"\u2022 {rtext} \u2014 \U0001f4c5 {rdt_str}"
            if recurring:
                line += f" \U0001f504 {recurring}"
            lines.append(line)
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        save_exchange(user_id, text, "Consult\u00f3 sus recordatorios", action)

    elif action == "delete":
        search_text = result.get("text", "")
        if not search_text:
            await update.message.reply_text("\u00bfQu\u00e9 recordatorio quieres eliminar?")
            return
        affected = deactivate_by_text(user_id, search_text)
        if affected > 0:
            log_activity(user_id, "eliminar_recordatorio", f"{search_text} ({affected})")
            await update.message.reply_text(f"\U0001f5d1\ufe0f *Jefe*, elimin\u00e9 {affected} recordatorio(s) con '{search_text}'")
            save_exchange(user_id, text, f"Elimin\u00e9 recordatorio(s): {search_text}", action)
            for job in context.job_queue.jobs():
                if job.name and job.data.get("uid") == user_id:
                    if search_text.lower() in job.data.get("text", "").lower():
                        job.schedule_removal()
        else:
            await update.message.reply_text(f"No encontr\u00e9 ning\u00fan recordatorio con '{search_text}'")

    elif action == "chat":
        await update.message.reply_text(result.get("message", "\U0001f60a"))

    elif action == "create_task_list":
        name = result.get("name", "lista")
        list_id = create_task_list(user_id, name)
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
        await update.message.reply_text(f"\u2705 Agregu\u00e9 *{task_text}* a la lista '{lists[0][1]}'")

    elif action == "list_tasks":
        list_name = result.get("list", "")
        lists = search_lists(user_id, list_name) if list_name else get_task_lists(user_id)
        if not lists:
            await update.message.reply_text("No tienes listas creadas. Usa 'crea una lista de...'")
            return
        if not list_name:
            msg = "\U0001f4cb *Jefe, tus listas:*\n"
            for lid, lname, _ in lists:
                items = get_list_items(lid)
                done = sum(1 for i in items if i[2])
                total = len(items)
                msg += f"\n\u2022 *{lname}* ({done}/{total})"
            await update.message.reply_text(msg, parse_mode="Markdown")
            return
        lid = lists[0][0]
        lname = lists[0][1]
        items = get_list_items(lid)
        if not items:
            await update.message.reply_text(f"La lista '{lname}' est\u00e1 vac\u00eda.")
            return
        lines = [f"\U0001f4cb *{lname}*\n"]
        for iid, itext, completed, priority, tags in items:
            status = "\u2705" if completed else f"\u26ab"
            p = {0: "", 1: " \u203c\ufe0f", 2: " \U0001f6a8"}.get(priority, "")
            tag = f" [#{tags}]" if tags else ""
            lines.append(f"{status} {itext}{p}{tag}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

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
        delete_task_item(found[0])
        log_activity(user_id, "eliminar_tarea", found[1])
        save_exchange(user_id, text, f"Tarea eliminada: {found[1]}", action)
        await update.message.reply_text(f"\U0001f5d1\ufe0f Elimin\u00e9 *{found[1]}* de la lista")

    elif action == "delete_task_list":
        name = result.get("name", "")
        lists = search_lists(user_id, name) if name else get_task_lists(user_id)
        if not lists:
            await update.message.reply_text(f"No encontr\u00e9 la lista '{name}'.")
            return
        delete_task_list(lists[0][0])
        log_activity(user_id, "eliminar_lista", lists[0][1])
        save_exchange(user_id, text, f"Lista eliminada: {lists[0][1]}", action)
        await update.message.reply_text(f"\U0001f5d1\ufe0f Lista '{lists[0][1]}' eliminada")

    elif action == "ocr_image":
        context.user_data["ocr_pending"] = True
        await update.message.reply_text("\U0001f5bc *Jefe*, env\u00edame la foto y extraigo el texto.", parse_mode="Markdown")

    elif action == "record_expense":
        amount = result.get("amount", 0)
        description = result.get("description", "")
        category = result.get("category")
        add_expense(user_id, amount, description, category)
        log_activity(user_id, "registrar_gasto", f"{description}: {amount}")
        save_exchange(user_id, text, f"Gasto registrado: {description} {amount}", action)
        total = get_today_total(user_id)
        cat_icon = {"comida":"\U0001f34e","transporte":"\U0001f697","servicios":"\U0001f4a1","ocio":"\U0001f3ac","salud":"\U0001f48a","hogar":"\U0001f3e0","otros":"\U0001f4b0"}.get(category, "\U0001f4b0")
        await update.message.reply_text(f"{cat_icon} *Jefe*, registrado: {description} \u2014 {amount} CRC\n\U0001f4ca Gastos del d\u00eda: *{total} CRC*", parse_mode="Markdown")

    elif action == "expense_summary":
        expenses = get_today_expenses(user_id)
        total = sum(r[0] for r in expenses)
        if not expenses:
            await update.message.reply_text("\U0001f4b0 *Jefe*, no has gastado nada hoy.", parse_mode="Markdown")
            return
        lines = [f"\U0001f4ca *Gastos de hoy:*\n"]
        for amt, desc, cat, cur in expenses:
            icon = {"comida":"\U0001f34e","transporte":"\U0001f697","servicios":"\U0001f4a1","ocio":"\U0001f3ac","salud":"\U0001f48a","hogar":"\U0001f3e0","otros":"\U0001f4b0"}.get(cat, "\U0001f4b0")
            lines.append(f"{icon} {desc} \u2014 {amt} {cur}")
        lines.append(f"\n\u2795 *Total: {total} CRC*")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        save_exchange(user_id, text, f"Consult\u00f3 gastos del d\u00eda: {total}", action)

    elif action == "generate_auth_code":
        if user_id != CREATOR_ID:
            await update.message.reply_text("\u26d4 Solo el creador puede generar c\u00f3digos.")
            return
        code = create_auth_code()
        log_activity(user_id, "generar_codigo", code)
        await update.message.reply_text(f"\U0001f511 *C\u00f3digo generado:* `{code}`\n\nCompartilo con tu amigo. V\u00e1lido por 24 horas.\nTu amigo debe usar: `/register {code}`", parse_mode="Markdown")

async def process_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user_id = update.effective_user.id
    history = get_recent_history(user_id, limit=6)
    result = analyze_message(text, history)
    logging.info(f"AI result: {result}")
    actions_list = result.get("actions") or [result]
    for act in actions_list:
        action = act.get("action")
        if not action:
            logging.warning(f"No action in: {act}")
            continue
        await process_action(update, context, text, act, user_id)

async def check_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid == CREATOR_ID or is_authorized(uid):
        return True
    if uid not in _auth_notified:
        _auth_notified.add(uid)
        name = update.effective_user.first_name or "Alguien"
        await update.message.reply_text(f"\U0001f512 *Acceso denegado*. Necesit\u00e1s autorizaci\u00f3n del creador.", parse_mode="Markdown")
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
    chat_type = update.effective_chat.type if update.effective_chat else "private"
    raw = update.message.text or ""

    if chat_type in ("group", "supergroup"):
        bot_username = (await context.bot.get_me()).username.lower()
        mentioned = bot_username in raw.lower() or re.search(r'(?:^|\s)[Oo]siris(?:\s|$|[ ,.!?])', raw)
        if not mentioned:
            return
        if bot_username in raw.lower():
            raw = raw.replace(f"@{bot_username}", "").strip()

    flow = context.user_data.get("auth_flow")
    if flow:
        try:
            exchange_code(flow, raw)
            del context.user_data["auth_flow"]
            await update.message.reply_text("Google autenticado \u2705 Ya puedes usar Calendar, Drive y YouTube.")
        except Exception as e:
            await update.message.reply_text(f"Error con el c\u00f3digo: {e}")
        return
    text = strip_wake_word(raw)
    if not text:
        await wake_greeting(update)
        return
    try:
        await process_text(update, context, text)
    except Exception as e:
        await update.message.reply_text(f"Error interno: {e}")
        logging.error(f"process_text error: {e}")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update, context):
        return
    user_id = update.effective_user.id
    msg = await update.message.reply_text("\U0001f3a4 Procesando audio...")
    try:
        file = await update.message.voice.get_file()
        file_path = f"voice_{update.message.message_id}.ogg"
        await file.download_to_drive(file_path)

        song = recognize_music(file_path)
        if song:
            os.remove(file_path)
            log_activity(user_id, "identificar_cancion", song.split('\n')[0][:100])
            await msg.edit_text(song, parse_mode="Markdown")
            return

        transcription = transcribe_audio(file_path)
        os.remove(file_path)
        if not transcription.strip():
            await msg.edit_text("No escuch\u00e9 nada claro.")
            return
        t = strip_wake_word(transcription)
        await msg.edit_text(f"\U0001f4dd Transcrib\u00ed: \"{t}\"")
        if not t:
            await wake_greeting(update)
            return
        await process_text(update, context, t)
    except Exception as e:
        await msg.edit_text(f"Error: {e}")
        logging.error(f"Voice error: {e}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    caption = strip_wake_word(update.message.caption or "")
    msg = await update.message.reply_text("\U0001f5bc Procesando...")
    try:
        file = await update.message.photo[-1].get_file()
        ext = "jpg"
        file_path = f"photo_{update.message.message_id}.{ext}"
        await file.download_to_drive(file_path)

        ocr_keywords = ["extrae", "texto", "ocr", "lee", "escribe", "saca", "transcribe", "factura", "flyer"]
        is_ocr = any(kw in (caption or "").lower() for kw in ocr_keywords) or context.user_data.pop("ocr_pending", False)

        if is_ocr:
            text = ocr_image(file_path)
            log_activity(user_id, "ocr_imagen", text[:100])
            import re
            amounts = re.findall(r'(?:total|monto|pagar|importe|neto)[:\s]*\$\s*([\d,.]+)', text, re.I)
            if not amounts:
                amounts = re.findall(r'(?:total|monto|pagar|importe|neto)[:\s]*([\d,.]+)', text, re.I)
            if not amounts:
                amounts = re.findall(r'\$\s*([\d,.]+)', text)
            if amounts:
                total = float(amounts[-1].replace(",", ""))
                add_expense(user_id, total, "factura", "comida")
                log_activity(user_id, "registrar_gasto", f"factura: {total}")
                await msg.edit_text(f"\U0001f4dd *Texto extra\u00eddo:*\n\n{text}\n\n\U0001f4b0 *Total detectado:* {total} CRC \u2014 registrado como gasto", parse_mode="Markdown")
            else:
                await msg.edit_text(f"\U0001f4dd *Texto extra\u00eddo:*\n\n{text}", parse_mode="Markdown")
        elif not caption:
            desc = analyze_image(file_path, "Describe esta imagen en detalle.")
            log_activity(user_id, "vision_imagen", desc[:100])
            await msg.edit_text(f"\U0001f5bc *Lo que veo:*\n\n{desc}", parse_mode="Markdown")
        else:
            await msg.edit_text(f"\U0001f4dd Procesando: \"{caption}\"")
            await process_text(update, context, caption)

        os.remove(file_path)
    except Exception as e:
        await msg.edit_text(f"Error: {e}")
        logging.error(f"Photo error: {e}")

async def post_init(app):
    schedule_from_db(app)
    t6 = local_now().replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
    app.job_queue.run_daily(daily_reminders, time=t6.time(), name="daily_reminders")
    app.job_queue.run_daily(evening_summary, time=time(hour=21, minute=0), name="evening_summary")
    logging.info(f"Recordatorios del día agendados a las 6:00")
    logging.info(f"Resumen nocturno agendado a las 21:00")

async def daily_reminders(context: ContextTypes.DEFAULT_TYPE):
    today = local_now().strftime("%Y-%m-%d")
    reminders = get_all_active()
    by_user = {}
    for r in reminders:
        rid, uid, text, dt_str, recurring, search_q, fn, end_date, lead_minutes = r
        if dt_str.startswith(today):
            by_user.setdefault(uid, []).append((text, dt_str, recurring, fn, lead_minutes))
    for uid, items in by_user.items():
        items.sort(key=lambda x: (-x[4], x[1]))
        lines = ["\U0001f4cb *Buenos d\u00edas, Jefe!* Recordatorios de hoy:\n"]
        for text, dt_str, recurring, fn, lead in items:
            line = f"\u2022 {text} \u2014 \U0001f4c5 {dt_str.split()[1]}"
            if lead >= 120:
                line = "\U0001f534 " + line
            elif lead >= 60:
                line = "\U0001f7e1 " + line
            else:
                line = "\u26aa " + line
            if fn:
                line += f" \U0001f4e8 para *{fn}*"
            if lead:
                line += f" \u23f0 -{lead}min"
            if recurring:
                line += f" \U0001f504 {recurring}"
            lines.append(line)
        try:
            await context.bot.send_message(chat_id=uid, text="\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Error daily reminders for user {uid}: {e}")

async def evening_summary(context: ContextTypes.DEFAULT_TYPE):
    today = local_now().strftime("%Y-%m-%d")
    users = set()
    for r in get_all_active():
        users.add(r[1])
    for uid in users:
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
        total_spent = sum(r[0] for r in expenses)
        lines = [f"\U0001f303 *Buenas noches, Jefe!* Resumen del d\u00eda:\n"]
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
            lines.append(f"\U0001f4b0 Gastos: *{total_spent} CRC* ({len(expenses)} compras)")
        lines.append(f"\n\u2728 *{len(rows)}* actividad(es) en total")
        try:
            await context.bot.send_message(chat_id=uid, text="\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Error evening summary for user {uid}: {e}")

def main():
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("auth", auth))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("authorize", authorize))
    app.add_handler(CommandHandler("deauthorize", deauthorize))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    t = threading.Thread(target=run_dashboard, daemon=True)
    t.start()
    logging.info(f"Osiris bot + Dashboard iniciados (zona: {TZ})")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
