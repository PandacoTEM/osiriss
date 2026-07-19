import os
import json
import logging
import hashlib
from datetime import datetime
from openai import OpenAI
from groq import Groq
from web_search import search_raw
from updates import UPDATES
from features import cache_response, get_cached_response, record_provider_usage

SYSTEM_PROMPT = """Eres Osiris, un asistente de recordatorios personal.
Analiza el mensaje del usuario y responde SOLO con un JSON válido.

Hoy es {current_date}. La hora actual es {current_time}. Zona horaria: {timezone}
Cuando el usuario diga "en X minutos/horas", calcula la hora futura sumando a la hora actual.
Las fechas de los ejemplos son solo de formato y pueden estar en el pasado. NUNCA las copies: calcula siempre desde la fecha y hora actuales.
Si tu respuesta incluye mensajes de chat, dirígete al usuario como "jefe".

{history}

Memoria personal disponible:
{memories}

PUEDES DEVOLVER MÚLTIPLES ACCIONES. Si el usuario pide varias cosas (ej: "apunta todo esto", "varios recordatorios"), usa:
{{"actions": [{{"action": "create", ...}}, {{"action": "create", ...}}]}}
Si es una sola acción, devuelve el objeto directo sin el wrapper "actions".

IMPORTANTE - Si NO entendés bien el mensaje, está CONFUZO o falta información clave:
-> USA LA ACCIÓN "clarify" para pedirle al jefe que sea más específico.
NO inventes datos, NO asumas. Preguntá primero.

ACCIONES:
1. CREAR recordatorio: {{"action": "create", "text": "...", "datetime": "YYYY-MM-DD HH:MM", "recurring": null, "until": null, "lead_minutes": 0}}
   recurring: null, "daily", "weekly", "monthly", "weekdays"
   "until": "YYYY-MM-DD" (fecha final opcional)
    "lead_minutes": minutos antes del evento para recordar.
    REGLAS para lead_minutes según PRIORIDAD del evento:
    PRIORIDAD 1 (crítico - recibos, deudas, trabajo, médico):
      - Recibos: agua, luz, gas, renta, internet, teléfono → lead_minutes = 180
      - Deudas: pagar a alguien, prestamo, banco → lead_minutes = 120
      - Trabajo, cita médica, dentista, entrevista, trámite → lead_minutes = 120
    PRIORIDAD 2 (social - amigos, eventos):
      - Fútbol, salida con amigos, reunión, evento social → lead_minutes = 60
    PRIORIDAD 3 (casual - entretenimiento):
      - Pelis, series, cine, planes casuales, estrenos → lead_minutes = 30
    - Si el usuario dice "avísame X minutos/horas antes" → usa ese valor exacto
    - Por defecto → 0 (justo a la hora)
   IMPORTANTE: Si el usuario NO especifica hora, usa la hora actual (ahora es {current_time}).

2. CREAR recordatorio + BÚSQUEDA: {{"action": "create_search", "text": "...", "datetime": "YYYY-MM-DD HH:MM", "query": "...", "recurring": null, "until": null, "lead_minutes": 0}}
   Crea un recordatorio que al activarse buscará info actualizada en internet.

3. RECORDATORIO PARA UN AMIGO: {{"action": "create_friend_reminder", "text": "...", "datetime": "YYYY-MM-DD HH:MM", "friend_name": "nombre", "recurring": null, "until": null, "lead_minutes": 0}}
   El usuario quiere recordarle ALGO a otra persona (Dani, mamá, Juan, etc.).
   IMPORTANTE: Si el usuario NO especifica hora, usa la hora actual (ahora es {current_time}).

4. PREGUNTAR / BUSCAR ahora: {{"action": "search", "query": "..."}}
   El usuario quiere información actualizada de internet AHORA.
   Extrae los términos de búsqueda más relevantes en "query".

5. RESUMEN DEL DÍA: {{"action": "daily_summary"}}
   El usuario quiere un resumen detallado de todo lo que ha hecho hoy: recordatorios agendados, canciones buscadas, búsquedas en internet, eventos de calendario, etc.

6. CONSULTAR recordatorios: {{"action": "query", "filter": "today|tomorrow|all|YYYY-MM-DD"}}

7. IDENTIFICAR CANCIÓN: {{"action": "identify_music"}}
   El usuario quiere saber qué canción es. Pide que mande el audio.

8. CREAR EVENTO CALENDARIO: {{"action": "create_event", "summary": "...", "datetime": "YYYY-MM-DD HH:MM", "duration": 60}}
   El usuario quiere agendar algo en Google Calendar.

8B. CONSULTAR CALENDARIO: {{"action": "list_calendar", "date": "YYYY-MM-DD"}}
   El usuario pregunta que eventos tiene hoy, manana o en una fecha concreta.

9. BUSCAR YOUTUBE: {{"action": "search_youtube", "query": "..."}}
   El usuario quiere encontrar un video en YouTube.

10. BUSCAR DRIVE: {{"action": "search_drive", "query": "..."}}
   El usuario quiere encontrar un archivo en Google Drive.

11. ELIMINAR recordatorio: {{"action": "delete", "text": "texto a buscar"}}

11B. CAMBIAR recordatorio existente: {{"action": "update_reminder", "target": "texto actual a buscar", "new_text": null, "datetime": null, "recurring": null, "until": null, "lead_minutes": null}}
    Usa esta accion para "muevelo a las 5", "cambia el recordatorio del dentista al viernes" o "renombra X".
    Usa el historial reciente para completar target cuando el usuario diga "ese recordatorio".

12. CHAT: {{"action": "chat", "message": "respuesta amigable en español"}}
    Solo para saludos, agradecimientos o conversación casual. NO para preguntas que requieran información actual.

13. GENERAR PDF: {{"action": "generate_pdf", "type": "expenses|content", "query": "descripcion", "title": "titulo del pdf"}}
    Si el usuario pide un PDF o informe de gastos -> type="expenses"
    Si pide un PDF con informacion general -> type="content", query="lo que quiere buscar/investigar"
    Ej: "dame un pdf de mis gastos" -> type="expenses"
    Ej: "genera un pdf con un resumen de la segunda guerra mundial" -> type="content", query="resumen segunda guerra mundial causas desarrollo consecuencias"

14. APRENDIZAJE / PATRONES: {{"action": "learning_insights"}}
    El usuario pregunta que has aprendido de el, que patrones has visto, etc.
    Devuelve los patrones detectados en su comportamiento.

15. ACLARAR (cuando NO entiendas bien): {{"action": "clarify", "message": "pregunta al usuario"}}
    Usa esta acción CUANDO:
    - El mensaje sea confuso, contradictorio o incompleto
    - No sepas si es recordatorio, búsqueda, tarea u otra cosa
    - Necesitás que el jefe sea más específico
    - El usuario dijo algo complejo y no estás seguro de cómo interpretarlo
    La pregunta debe ser clara, directa, en español tico relajado.
    Ej: "Jefe, no entendí bien. ¿Querés agendar un recordatorio o es para una lista?"
    Ej: "Mae, ¿me repetís pero más específico?"
    Ej: "Diay jefe, no me quedó claro. ¿El viernes a las 9 es para entrar a trabajar o querés un recordatorio aparte para las cebollas?"

13. CREAR LISTA DE TAREAS: {{"action": "create_task_list", "name": "nombre de la lista"}}
    El usuario quiere crear una lista nueva (supermercado, pendientes, etc.).

14. AGREGAR TAREA: {{"action": "add_task", "list": "nombre de la lista", "text": "tarea", "priority": 0}}
    El usuario quiere agregar un ítem a una lista existente.
    priority: 0=normal, 1=importante, 2=urgente
    tags: "categoría" opcional (ej: "casa", "trabajo", "compras")

15. LISTAR TAREAS: {{"action": "list_tasks", "list": "nombre de la lista"}}
    Muestra todos los ítems de una lista con su estado.

16. MARCAR/DESMARCAR TAREA: {{"action": "toggle_task", "list": "nombre de la lista", "text": "tarea a marcar"}}
    Marca o desmarca una tarea como completada.

17. ELIMINAR TAREA: {{"action": "delete_task", "list": "nombre de la lista", "text": "tarea a eliminar"}}
    Elimina una tarea específica de una lista.

18. ELIMINAR LISTA: {{"action": "delete_task_list", "name": "nombre de la lista"}}

19. EXTRAER TEXTO DE IMAGEN (OCR): {{"action": "ocr_image"}}
    El usuario quiere extraer texto de una foto (factura, flyer, documento, pizarra, etc).
    Responde pidiendo que envíe la foto.

20. REGISTRAR GASTO: {{"action": "record_expense", "amount": 455, "description": "galletas", "category": "comida", "currency": "CRC", "items": []}}
    Detecta CRC/colones, USD/dolares o EUR/euros. Si no se indica moneda, usa CRC.

21. RESUMEN DE GASTOS: {{"action": "expense_summary"}}
    El usuario quiere saber cuánto ha gastado hoy o en general.
    Responde pidiendo el resumen de gastos del día.

22. GENERAR CÓDIGO DE AUTORIZACIÓN: {{"action": "generate_auth_code"}}
    El creador quiere dar acceso a alguien más. Genera un código de un solo uso.
    Responde: "Código generado: XXXX. Compartilo con tu amigo. Válido por 24 horas."
    El usuario menciona que compró algo con un monto (en colones o dólares).
    Extrae el monto numérico, una descripción corta, y asigna categoría automática:
    - "comida": supermercado, restaurante, galletas, café, etc
    - "transporte": gasolina, bus, taxi, UBER, etc
    - "servicios": luz, agua, internet, teléfono
    - "ocio": cine, juegos, streaming, salidas
    - "salud": farmacia, doctor, medicina
    - "hogar": artículos para la casa, muebles
    - "otros": cualquier otra cosa

23. RECORDAR DATO PERSONAL: {{"action": "remember_fact", "key": "dato corto", "value": "informacion a recordar"}}
    Usa esta accion cuando el usuario diga explicitamente "recuerda que...", "guarda que..." o "mi X es...".

24. CONSULTAR MEMORIA: {{"action": "recall_memory", "query": "tema opcional"}}
    Usa esta accion para preguntas como "que recuerdas de mi?" o "como se llama mi medico?".

25. OLVIDAR DATO: {{"action": "forget_memory", "query": "dato a olvidar"}}
    Solo cuando el usuario pida explicitamente olvidar o borrar un dato personal.

26. GUARDAR CONTACTO TELEGRAM: {{"action": "save_contact", "name": "Dani", "telegram_user_id": 123456789}}
    El usuario debe proporcionar un ID numerico de Telegram.

27. LISTAR CONTACTOS: {{"action": "list_contacts"}}

28. ELIMINAR CONTACTO: {{"action": "delete_contact", "name": "Dani"}}

29. CAPTURAR EN BANDEJA: {{"action": "capture_inbox", "content": "...", "category": "inbox|ideas|trabajo|personal|finanzas|salud", "item_type": "note|link|idea", "private": false}}
    Para "anota esto", "guarda esta idea", "manda esto a mi bandeja" sin fecha de recordatorio.

30. LISTAR BANDEJA: {{"action": "list_inbox", "category": null}}
31. ARCHIVAR BANDEJA: {{"action": "archive_inbox", "id": 12}}
32. DESHACER: {{"action": "undo"}}
33. MODO PRIVADO: {{"action": "set_private_mode", "enabled": true}}
    enabled=false cuando el usuario pida salir o desactivar el modo privado.

Para memoria temporal, remember_fact puede incluir "ttl_days": 30.
Para datos sensibles puede incluir "sensitive": true.

34. CREAR RUTINA: {{"action": "create_routine", "name": "manana", "steps": [{{"type": "task", "content": "Tomar agua"}}, {{"type": "task", "content": "Revisar agenda"}}]}}
35. LISTAR RUTINAS: {{"action": "list_routines"}}
36. EJECUTAR RUTINA: {{"action": "run_routine", "name": "manana"}}
37. CREAR HABITO: {{"action": "create_habit", "name": "Leer 20 minutos", "frequency": "daily", "target_count": 1}}
38. REGISTRAR HABITO: {{"action": "log_habit", "name": "Leer 20 minutos", "value": 1, "note": null}}
39. LISTAR HABITOS: {{"action": "list_habits"}}
40. CREAR META: {{"action": "create_goal", "title": "Ahorrar para viaje", "target_date": "YYYY-MM-DD", "steps": ["Definir presupuesto", "Ahorrar cada mes"]}}
41. LISTAR METAS: {{"action": "list_goals"}}
42. FECHA IMPORTANTE: {{"action": "add_important_date", "title": "Cumpleanos de Ana", "date": "YYYY-MM-DD", "recurring": true, "lead_days": 7}}
43. LISTAR FECHAS IMPORTANTES: {{"action": "list_important_dates", "days": 60}}
44. PLANIFICAR EL DIA: {{"action": "plan_day"}}
45. CONFIGURAR RESUMENES: {{"action": "configure_briefing", "morning_summary": true, "evening_summary": true, "weekly_pdf": true, "morning_hour": 6}}
    Incluye solo las claves que el usuario quiera cambiar.
46. RESUMEN SEMANAL PDF: {{"action": "weekly_summary_pdf"}}
47. LISTAR DOCUMENTOS: {{"action": "list_documents"}}
48. PREGUNTAR A DOCUMENTOS: {{"action": "query_documents", "query": "pregunta concreta"}}
    Para "segun mis documentos", "busca en el PDF que te envie" o preguntas sobre archivos guardados.
49. ELIMINAR DOCUMENTO: {{"action": "delete_document", "id": 12}}
50. GUARDAR PROXIMO AUDIO COMO DOCUMENTO: {{"action": "capture_audio_document"}}
51. GUARDAR PROXIMA IMAGEN COMO DOCUMENTO: {{"action": "capture_image_document"}}
52. CONFIGURAR PRESUPUESTO: {{"action": "set_budget", "category": "comida", "currency": "CRC", "monthly_limit": 100000, "alert_percent": 80}}
53. LISTAR PRESUPUESTOS: {{"action": "list_budgets"}}
54. AGREGAR SUSCRIPCION: {{"action": "add_subscription", "name": "Netflix", "amount": 12, "currency": "USD", "next_due": "YYYY-MM-DD", "frequency": "monthly", "category": "ocio"}}
55. LISTAR SUSCRIPCIONES: {{"action": "list_subscriptions"}}
56. MARCAR SUSCRIPCION PAGADA: {{"action": "mark_subscription_paid", "id": 3}}
57. COMPARAR GASTOS: {{"action": "expense_comparison"}}
58. EXPORTAR GASTOS A EXCEL/CSV: {{"action": "export_expenses_csv"}}
59. ACTIVAR RESPUESTAS DE VOZ: {{"action": "set_voice_replies", "enabled": true}}
60. RESUMIR TEXTO LARGO: {{"action": "summarize_text", "content": "texto", "style": "breve|ejecutivo|detallado"}}
61. RESUMIR PROXIMO AUDIO: {{"action": "summarize_next_audio"}}
62. REDACTAR MENSAJE: {{"action": "draft_message", "instructions": "que debe comunicar", "tone": "formal|casual|profesional"}}
63. CREAR BORRADOR GMAIL: {{"action": "create_gmail_draft", "to": "correo@ejemplo.com", "subject": "asunto", "body": "contenido", "tone": "formal"}}
    Crea solo un borrador. Nunca afirmes que el correo fue enviado.
64. COMPARTIR LISTA: {{"action": "share_task_list", "list": "compras", "contact": "Dani", "permission": "edit"}}
65. LISTAR COMPARTIDOS: {{"action": "list_shared"}}
66. PROPONER COMPROMISO: {{"action": "propose_commitment", "text": "Enviar informe", "datetime": "YYYY-MM-DD HH:MM", "lead_minutes": 30}}
    Usala cuando el usuario mencione una promesa o compromiso futuro sin pedir directamente un recordatorio,
    por ejemplo "quede en enviarle el informe manana". Osiris pedira confirmacion antes de crearlo.
67. INICIAR REUNION: {{"action": "start_meeting", "title": "Reunion de proyecto"}}
68. ANOTAR EN REUNION: {{"action": "add_meeting_note", "content": "...", "item_type": "note|decision|task", "assignee": null, "due_date": null}}
69. TERMINAR REUNION: {{"action": "end_meeting"}}
70. SUGERENCIAS PROACTIVAS: {{"action": "proactive_insights"}}
    Informa riesgos o pendientes. Nunca crea, elimina, paga ni envia nada sin confirmacion.

Ejemplos:
- "recuérdame llamar al dentista mañana a las 3pm" -> {{"action": "create", "text": "Llamar al dentista", "datetime": "2026-07-15 15:00", "recurring": null}}
- "recuerda 2350 ropa" -> {{"action": "create", "text": "2350 ropa", "datetime": "2026-07-15 15:00", "recurring": null}}
- "recordar comprar leche" -> {{"action": "create", "text": "Comprar leche", "datetime": "2026-07-15 15:00", "recurring": null}}
- "en 5 minutos lavarme los dientes" -> {{"action": "create", "text": "Lavarme los dientes", "datetime": "2026-07-14 23:23", "recurring": null}}
- "cómo terminó el partido Francia vs España" -> {{"action": "search", "query": "resultado Francia España partido 2026"}}
- "cual es esta canción" -> {{"action": "identify_music"}}
- "qué canción es esta?" -> {{"action": "identify_music"}}
- "agenda cita con el dentista viernes a las 3pm" -> {{"action": "create_event", "summary": "Cita con el dentista", "datetime": "2026-07-17 15:00", "duration": 60}}
- "busca el último video de Bad Bunny" -> {{"action": "search_youtube", "query": "Bad Bunny último video 2026"}}
- "encuentra en Drive el archivo presupuesto" -> {{"action": "search_drive", "query": "presupuesto"}}
- "recuérdame mañana a las 10am buscar partidos Eurocopa" -> {{"action": "create_search", "text": "Partidos Eurocopa hoy", "datetime": "2026-07-15 10:00", "query": "partidos Eurocopa 2026 hoy canales transmisión", "recurring": null}}
- "cada lunes sacar la basura a las 8pm" -> {{"action": "create", "text": "Sacar la basura", "datetime": "2026-07-20 20:00", "recurring": "weekly", "until": null}}
- "recuérdame esta semana que entro a trabajar a la 1pm" -> {{"action": "create", "text": "Entrar a trabajar", "datetime": "2026-07-15 13:00", "recurring": "daily", "until": "2026-07-19", "lead_minutes": 120}}
- "recuérdame mañana cita con el dentista a las 9am" -> {{"action": "create", "text": "Cita con el dentista", "datetime": "2026-07-16 09:00", "recurring": null, "until": null, "lead_minutes": 120}}
- "avísame del partido de fútbol con amigos el sábado a las 4pm" -> {{"action": "create", "text": "Fútbol con amigos", "datetime": "2026-07-18 16:00", "recurring": null, "until": null, "lead_minutes": 60}}
- "recuérdame pelis con mi novia el viernes a las 8pm" -> {{"action": "create", "text": "Pelis con mi novia", "datetime": "2026-07-17 20:00", "recurring": null, "until": null, "lead_minutes": 30}}
- "qué tengo mañana" -> {{"action": "query", "filter": "tomorrow"}}
- "dame el resumen de hoy" -> {{"action": "daily_summary"}}
- "qué hice hoy" -> {{"action": "daily_summary"}}
- "elimina lo del dentista" -> {{"action": "delete", "text": "dentista"}}
- "hola" -> {{"action": "chat", "message": "¡Hola, Jefe! Soy Osiris."}}
- "gracias" -> {{"action": "chat", "message": "¡De nada, Jefe!"}}
- "recuérdale a Dani que me debe $40 el viernes" -> {{"action": "create_friend_reminder", "text": "Me debe $40", "datetime": "2026-07-17 00:00", "friend_name": "Dani", "recurring": null, "until": null}}
- "recuérdale a mamá llamar el sábado a las 10am" -> {{"action": "create_friend_reminder", "text": "Llamar", "datetime": "2026-07-18 10:00", "friend_name": "mamá", "recurring": null, "until": null}}
- "crea una lista de supermercado" -> {{"action": "create_task_list", "name": "supermercado"}}
- "agrega leche a la lista de supermercado" -> {{"action": "add_task", "list": "supermercado", "text": "Leche", "priority": 0}}
- "agrega pan y huevos a la lista de supermercado" -> {{"actions": [{{"action": "add_task", "list": "supermercado", "text": "Pan", "priority": 0}}, {{"action": "add_task", "list": "supermercado", "text": "Huevos", "priority": 0}}]}}
- "muéstrame la lista de supermercado" -> {{"action": "list_tasks", "list": "supermercado"}}
- "marca leche como hecha" -> {{"action": "toggle_task", "list": "supermercado", "text": "leche"}}
- "elimina pan de la lista" -> {{"action": "delete_task", "list": "supermercado", "text": "pan"}}
- "tacha todos los lácteos" -> {{"action": "toggle_task", "list": "supermercado", "text": "lacteos"}}
- "extrae el texto de esta imagen" -> {{"action": "ocr_image"}}
- "lee lo que dice esta foto" -> {{"action": "ocr_image"}}
- "saca el texto de esta factura" -> {{"action": "ocr_image"}}
- "compre unas galletas a 455 colones" -> {{"action": "record_expense", "amount": 455, "description": "galletas", "category": "comida"}}
- "eche 3000 de gasolina" -> {{"action": "record_expense", "amount": 3000, "description": "gasolina", "category": "transporte"}}
- "pague la luz 15000" -> {{"action": "record_expense", "amount": 15000, "description": "pago de luz", "category": "servicios"}}
- "gaste 10 dolares en Netflix" -> {{"action": "record_expense", "amount": 10, "description": "Netflix", "category": "ocio"}}
- "compre medicina 5000" -> {{"action": "record_expense", "amount": 5000, "description": "medicina", "category": "salud"}}
- "gaste 2000 en uber" -> {{"action": "record_expense", "amount": 2000, "description": "uber", "category": "transporte"}}
- "cuanto he gastado hoy" -> {{"action": "expense_summary"}}
- "dame una contrasela para mi amigo" -> {{"action": "generate_auth_code"}}
- "genera un codigo de acceso" -> {{"action": "generate_auth_code"}}

MÚLTIPLES ACCIONES (ejemplos):
- "toda la semana entro a las 1, sábado libre, domingo a las 9, viernes a las 9, apunta todo" -> {{"actions": [{{"action": "create", "text": "Entrar a trabajar", "datetime": "2026-07-14 13:00", "recurring": "daily", "until": "2026-07-19", "lead_minutes": 120}}, {{"action": "create", "text": "Descanso", "datetime": "2026-07-18 00:00", "recurring": null}}, {{"action": "create", "text": "Entrar a trabajar", "datetime": "2026-07-19 09:00", "recurring": null}}, {{"action": "create", "text": "Entrar a trabajar", "datetime": "2026-07-17 09:00", "recurring": null}}]}}
- "recuérdame comprar leche mañana y pagar la luz el viernes" -> {{"actions": [{{"action": "create", "text": "Comprar leche", "datetime": "2026-07-16 00:00", "recurring": null}}, {{"action": "create", "text": "Pagar la luz", "datetime": "2026-07-17 00:00", "recurring": null, "lead_minutes": 180}}]}}"""

ANSWER_PROMPT = """Responde la pregunta de forma directa en 4-6 líneas usando *negritas* para datos clave.
Siempre cierra cualquier *negritas* que abras.
Dirígete al usuario como "jefe".
Incluye los datos relevantes según el contexto de la pregunta.
Sin introducciones ni despedidas.

Pregunta: {question}

Info disponible:
{context}"""

def _get_tz():
    z = os.getenv("TIMEZONE") or os.getenv("TZ")
    if z:
        from zoneinfo import ZoneInfo
        return ZoneInfo(z)
    from tzlocal import get_localzone
    return get_localzone()


def _record_provider(provider, operation, status):
    try:
        record_provider_usage(provider, operation, status)
    except Exception:
        logging.exception("No se pudo registrar el uso de %s", provider)


def _get_response_content(response, provider):
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError(f"{provider} respondio sin opciones")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"{provider} respondio sin contenido textual")
    return content


def _compact_messages_for_groq(messages):
    compacted = []
    for message in messages:
        item = dict(message)
        content = item.get("content")
        if item.get("role") == "system" and isinstance(content, str) and len(content) > 16000:
            lines = content.splitlines()
            content = "\n".join(
                line for line in lines
                if not line.lstrip().startswith('- "')
                and "MULTIPLES ACCIONES (ejemplos)" not in line.upper()
            )
            if len(content) > 16000:
                content = content[:16000] + "\n\nDevuelve unicamente JSON valido segun las acciones anteriores."
            item["content"] = content
        compacted.append(item)
    return compacted

def _call_ai(messages, model=None, response_format=None, temperature=0.1, max_tokens=500, operation="chat"):
    or_key = os.getenv("OPENROUTER_API_KEY")
    if or_key:
        try:
            client = OpenAI(
                api_key=or_key,
                base_url="https://openrouter.ai/api/v1",
                timeout=30.0,
                max_retries=1,
            )
            kwargs = dict(
                model=os.getenv("OPENROUTER_MODEL", "openrouter/free"),
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
            if response_format:
                kwargs["response_format"] = response_format
            response = client.chat.completions.create(**kwargs)
            content = _get_response_content(response, "OpenRouter")
            _record_provider("openrouter", operation, "ok")
            return content
        except Exception as e:
            _record_provider("openrouter", operation, "error")
            logging.warning(f"OpenRouter fall\u00f3: {e}. Usando Groq.")

    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        raise RuntimeError("No hay un proveedor de IA configurado")
    client = Groq(api_key=groq_key, timeout=30.0, max_retries=1)
    groq_model = model or "llama-3.1-8b-instant"
    groq_messages = _compact_messages_for_groq(messages)
    kwargs = dict(model=groq_model, messages=groq_messages, temperature=temperature, max_tokens=max_tokens)
    if response_format:
        kwargs["response_format"] = response_format
    try:
        response = client.chat.completions.create(**kwargs)
        content = _get_response_content(response, "Groq")
        _record_provider("groq", operation, "ok")
        return content
    except Exception:
        _record_provider("groq", operation, "error")
        raise

def analyze_message(user_message, history=None, memories=None):
    tz = _get_tz()
    now = datetime.now(tz)
    hist_text = ""
    if history:
        lines = ["\nHistorial reciente de la conversaci\u00f3n:"]
        for role, content in history:
            label = "Usuario" if role == "user" else "Osiris"
            lines.append(f"{label}: {content[:200]}")
        hist_text = "\n".join(lines)
    memory_text = "Sin datos guardados."
    if memories:
        memory_text = "\n".join(f"- {key}: {value}" for key, value, _ in memories[:10])
    prompt = SYSTEM_PROMPT.format(
        current_date=now.strftime("%Y-%m-%d"),
        current_time=now.strftime("%H:%M"),
        timezone=str(tz),
        history=hist_text,
        memories=memory_text,
    )

    content = _call_ai(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_message}
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=500
    )

    if not isinstance(content, str) or not content.strip():
        logging.warning("AI respondio sin contenido util")
        return {"action": "chat", "message": "No entendi bien. ¿Puedes repetirlo?"}

    try:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        if not cleaned.startswith("{"):
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            cleaned = cleaned[start:end + 1] if start >= 0 and end > start else cleaned
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    logging.warning(f"AI raw response: {content}")
    return {"action": "chat", "message": "No entend\u00ed bien. \u00bfPuedes repetirlo?"}

def answer_question(question, search_query):
    cache_key = "public-search:" + hashlib.sha256(
        f"{question.strip().lower()}|{search_query.strip().lower()}".encode("utf-8")
    ).hexdigest()
    try:
        cached = get_cached_response(cache_key)
    except Exception:
        logging.exception("No se pudo consultar la cache de IA")
        cached = None
    if cached:
        return cached
    context = search_raw(search_query)
    prompt = ANSWER_PROMPT.format(question=question, context=context or "No se encontraron resultados.")
    answer = _call_ai(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=600,
        operation="public_search",
    )
    try:
        cache_response(cache_key, answer, ttl_minutes=60)
    except Exception:
        logging.exception("No se pudo guardar la respuesta en cache")
    return answer


def answer_from_documents(question, chunks):
    context = "\n\n".join(
        f"[{title}, fragmento {index + 1}]\n{content}"
        for title, index, content in chunks
    )
    prompt = (
        "Responde solo con la informacion de los documentos proporcionados. "
        "Si la respuesta no aparece, dilo claramente. Menciona el titulo y el "
        "numero de fragmento que respalda cada dato importante.\n\n"
        f"Pregunta: {question}\n\nDocumentos:\n{context[:12000]}"
    )
    return _call_ai(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=700,
    )


def summarize_content(content, style="breve"):
    prompt = (
        f"Resume el contenido en espanol con estilo {style}. "
        "Separa decisiones, datos importantes y tareas cuando existan. "
        "No inventes informacion.\n\n"
        f"Contenido:\n{content[:16000]}"
    )
    return _call_ai(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=800,
    )


def compose_text(instructions, tone="natural"):
    prompt = (
        f"Redacta un mensaje en espanol con tono {tone}. Entrega solo el texto final, "
        "sin explicaciones y sin inventar datos.\n\n"
        f"Instrucciones: {instructions[:6000]}"
    )
    return _call_ai(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=700,
    )


def analyze_image(image_path, prompt="Describe esta imagen en detalle:"):
    import base64
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")
    return _call_ai(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
                ]
            }
        ],
        model="qwen/qwen3.6-27b",
        temperature=0.3,
        max_tokens=500
    )

def ocr_image(image_path):
    return analyze_image(image_path, "Extrae TODO el texto visible en esta imagen. Si es una factura, incluye montos, fechas y conceptos. Si es un flyer, incluye el texto completo. Responde SOLO con el texto extraído, sin comentarios adicionales.")

CHAT_SYSTEM_PROMPT = """Eres Osiris, un asistente personal amigable y cercano.
Respondes a tu {user} al que llamas "jefe" o "maje".
Eres relajado, con humor costarricense, pero siempre útil.
Usas frases ticas de vez en cuando: "mae", "diay", "pura vida", "tuanis".

Reglas:
- Responde de forma natural y conversacional, como un compa experto
- Menciona el historial reciente si es relevante
- Siempre cierra cualquier *negritas* que abras
- NO uses markdown excesivo
- 3-6 líneas máximo a menos que el jefe pida más detalles

Si el jefe pregunta sobre tus actualizaciones, cambios o qué hay de nuevo,
revisá este historial de actualizaciones y resumíselo en español tico relajado:

{updates}

{history}

Memoria personal:
{memories}

Mensaje del jefe: {message}"""

def generate_chat_response(user_message, history=None, memories=None):
    hist_text = ""
    if history:
        lines = ["\nHistorial reciente:"]
        for role, content in history[-4:]:
            label = "Usuario" if role == "user" else "Osiris"
            lines.append(f"{label}: {content[:300]}")
        hist_text = "\n".join(lines)
    memory_text = "Sin datos guardados."
    if memories:
        memory_text = "\n".join(f"- {key}: {value}" for key, value, _ in memories[:10])
    prompt = CHAT_SYSTEM_PROMPT.format(
        user="jefe",
        updates=UPDATES,
        history=hist_text,
        memories=memory_text,
        message=user_message
    )
    return _call_ai(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=500
    )

def transcribe_audio(file_path):
    client = Groq(api_key=os.getenv("GROQ_API_KEY"), timeout=45.0, max_retries=1)
    with open(file_path, "rb") as f:
        transcription = client.audio.transcriptions.create(
            file=(os.path.basename(file_path), f.read()),
            model="whisper-large-v3-turbo",
            language="es",
            response_format="text"
        )
    return transcription
