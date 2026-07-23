import re

UPDATES_TRIGGER = "dime tu color favorito"


def is_updates_trigger(text):
    normalized = re.sub(r"[^\w\s]", "", str(text or "").casefold())
    normalized = " ".join(normalized.split())
    return normalized == UPDATES_TRIGGER


def get_updates_text(max_chars=3900):
    return UPDATES.strip()[:max_chars]


def _get_latest_date():
    match = re.search(r"\[(\d{2} \w{3} \d{4})\]", UPDATES)
    return match.group(1) if match else "desconocida"

UPDATES = """
=== ULTIMAS ACTUALIZACIONES DE OSIRIS ===

[23 Jul 2026] - Google Direct y Gemini 3.5 Flash
- Google Direct como proveedor IA principal (sin OpenRouter): respuestas en ~1s
- Cadena de proveedores: Google Direct -> OpenRouter -> Groq
- Modelo: gemini-3.5-flash (ultimo estable gratuito, 60 req/min)
- /estado ahora muestra Google Direct en proveedores configurados

[22 Jul 2026] - Nucleo de recordatorios y actualizaciones inteligentes
- Ya no hay botones en los recordatorios: se auto-borran al enviarlos
- Bug critico arreglado: desactivacion y actualizacion de recordatorios no funcionaban por parametros al reves
- Bug critico arreglado: columnas faltantes en base de datos impedian programar cualquier recordatorio
- Recordatorios fallidos permanentes ahora se desactivan solos y notifican al creador
- Indices en base de datos para consultas mas rapidas
- Modo WAL activado en SQLite para evitar bloqueos por concurrencia
- Eliminada consulta N+1 en el ciclo de programacion de recordatorios
- get_reminder_by_id ahora devuelve todos los campos de delivery
- Actualizaciones inteligentes: al preguntar "que hay de nuevo" Osiris revisa la fecha de la ultima actualizacion — si es hoy lista los cambios, si es anterior ofrece repasarlos

[18 Jul 2026] - Osiris asistente personal completo
- Recordatorios, bandeja de entrada, modo privado, memoria temporal y deshacer
- Rutinas, habitos, metas, fechas importantes y plan diario
- Resumen matutino, nocturno y reporte semanal PDF
- Biblioteca de documentos: PDF, DOCX, TXT, CSV, imagenes y audios
- Presupuestos, suscripciones, OCR, gastos CSV
- Compromisos con confirmacion, minutas de reunion, listas compartidas
- Google Calendar, Drive, YouTube, Gmail
- Router OpenRouter/Groq con cache y metricas
- Respaldos cifrados, desconexion de Google, /estado

[17 Jul 2026] - Sistema de aprendizaje y PDF
- Aprende de tus patrones de uso: recordatorios, gastos, busquedas
- "dame un pdf de mis gastos" o "genera un pdf sobre cualquier tema"

[16 Jul 2026] - Chat natural y zona horaria
- Tono tico relajado, temperatura mas alta, respuestas menos roboticas
- Recordatorios y resumen nocturno en hora local (America/Costa_Rica)
- Despliegue en Render 24/7 con UptimeRobot

[15-13 Jul 2026] - Funciones iniciales
- Recordatorios, temporizadores, busqueda web, YouTube, Drive
- Control de gastos, listas, OCR, identificacion de musica
- Recordatorios para amigos, multiples acciones por mensaje
- Fallback OpenRouter → Groq, accion clarify
"""

LATEST_DATE = _get_latest_date()
