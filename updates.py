UPDATES = """
=== ULTIMAS ACTUALIZACIONES DE OSIRIS ===

[17 Jul 2026] - Sistema de aprendizaje
- Osiris ahora aprende de tus patrones de uso
- Detecta recordatorios importantes, gastos frecuentes, temas de búsqueda
- Puedes preguntar: "qué has aprendido de mí?" o "dame tus insights"

[17 Jul 2026] - Generador de PDF
- "dame un pdf de mis gastos" → reporte de gastos en PDF
- "dame un pdf con un resumen de la segunda guerra mundial" → investiga y genera PDF
- Categorías, totales, desglose detallado

[17 Jul 2026] - Webhook seguro con secret token
- Agregado TELEGRAM_WEBHOOK_SECRET para verificar que las peticiones webhook vienen de Telegram y no de terceros

[16 Jul 2026] - Chat natural mejorado
- Ahora respondo con tono tico relajado (mae, diay, pura vida)
- Temperatura más alta para respuestas menos robóticas
- Si no entiendo algo, te pregunto en vez de buguearme

[16 Jul 2026] - Corrección de zona horaria
- Recordatorios diarios ahora respetan tu hora local (America/Costa_Rica)
- Resumen nocturno ahora llega a las 9pm, no a las 3pm

[16 Jul 2026] - Despliegue en Render 24/7
- El bot ahora vive en la nube, no depende de tu PC
- UptimeRobot lo mantiene despierto con pings cada 5 minutos

[15 Jul 2026] - Fallback OpenRouter → Groq
- Si OpenRouter falla, uso Groq automáticamente
- Más estable, menos downtime

[14 Jul 2026] - Nueva acción "clarify"
- Cuando no entiendo algo, te pido que seas más específico
- Ej: "Jefe, no entendí bien. ¿Querés un recordatorio o es para una lista?"

[13 Jul 2026] - Funciones iniciales
- Recordatorios, temporizadores, búsqueda web, YouTube, Google Drive
- Control de gastos, listas de tareas, OCR en imágenes
- Reconocimiento de música con Audd
- Recordatorios para amigos
- Múltiples acciones en un solo mensaje
"""
