# Osiris

Asistente personal gratuito sobre Telegram. Gestiona recordatorios, voz, memoria,
tareas, gastos, OCR, documentos, rutinas, reuniones, contactos y Google.

## Operacion gratuita

- Telegram Bot API como canal principal.
- Render Free como servicio web.
- Neon Free como PostgreSQL persistente; no uses Render Free Postgres porque
  vence 30 dias despues de crearlo.
- UptimeRobot Free consultando `/health` cada 5 minutos para evitar que Render
  duerma el servicio tras 15 minutos sin trafico.
- `openrouter/free` como proveedor de IA principal.
- Groq como respaldo dentro de su cuota gratuita.
- AudD solo se consulta cuando el usuario pide identificar una cancion.

WhatsApp no esta activado porque un canal oficial estable puede requerir cobro.
Telegram mantiene el proyecto en costo cero.

Los planes gratuitos tienen limites y no ofrecen una garantia contractual de
disponibilidad. OpenRouter Free permite 50 solicitudes de IA al dia para cuentas
sin creditos comprados; las funciones locales y los recordatorios no consumen
esa cuota.

## Configuracion

1. Crea una base gratuita en Neon y coloca su cadena de conexion agrupada en
   `DATABASE_URL` dentro de Render.
2. Copia las variables de `.env.example` a `.env` para desarrollo o a Environment
   Variables en Render.
3. Configura `TELEGRAM_WEBHOOK_SECRET`, `DASHBOARD_PASSWORD` y
   `DASHBOARD_SESSION_SECRET` con valores aleatorios diferentes.
4. En Google Cloud registra exactamente la URI de `GOOGLE_REDIRECT_URI`.
5. Configura `GOOGLE_TOKEN_ENCRYPTION_KEY` para cifrar los tokens almacenados.
6. Configura `OSIRIS_BACKUP_KEY` y conservala fuera de Render. Sin esa misma
   clave no se pueden restaurar los archivos `.osirisbackup`.
7. En UptimeRobot crea un monitor HTTP gratuito cada 5 minutos para
   `https://osiriss.onrender.com/health`.
8. Ejecuta `pip install -r requirements.txt` y luego `python bot.py`.

Referencias: [Render Free](https://render.com/docs/free),
[Neon Free](https://neon.com/pricing),
[UptimeRobot Free](https://uptimerobot.com/pricing/) y
[limites de OpenRouter](https://openrouter.ai/docs/api/reference/limits).

## Controles utiles

- `/auth`: conecta Google; solo funciona para el creador.
- `/panel`: abre el dashboard; solo funciona para el creador.
- `/exportar`: descarga todos los datos personales en JSON.
- `/backup`: crea una copia cifrada y restaurable de todos los datos.
- Envia un archivo `.osirisbackup` al bot para validarlo y restaurarlo con confirmacion.
- `/borrardatos`: elimina los datos luego de una confirmacion.
- `/desconectargoogle`: revoca la conexion local con Google tras confirmar.
- `/estado`: comprueba base de datos, Telegram, proveedores y ultimo respaldo.
- `/plan`, `/habitos`, `/rutinas`, `/metas`, `/fechas`: organizacion personal.
- `/documentos`, `/presupuestos`, `/suscripciones`, `/gastoscsv`: documentos y finanzas.
- `/reunion iniciar <titulo>` y `/reunion terminar`: captura decisiones y genera minuta.
- `/privado`, `/inbox`, `/deshacer`, `/voz`, `/sugerencias`: privacidad y asistencia.
- `/myid`: muestra el ID necesario para contactos y autorizaciones.

Los recordatorios incluyen botones para posponer o completar. Las eliminaciones y
los gastos detectados por OCR requieren confirmacion. Los compromisos que Osiris
detecta en una conversacion tambien requieren confirmacion antes de convertirse
en recordatorios.

El resumen matutino usa `America/Costa_Rica` y se envia a las 6:00 por defecto.
La hora se puede cambiar hablando con Osiris. El resumen nocturno sale a las
21:00 y el PDF semanal los domingos, si esas preferencias estan activas.

## Pruebas

```powershell
python -m unittest -v test_core.py test_security.py
```
