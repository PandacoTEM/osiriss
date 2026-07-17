import os
import logging
from twilio.rest import Client

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
MY_PHONE_NUMBER = os.getenv("MY_PHONE_NUMBER")

_client = None

def _get_client():
    global _client
    if _client is None:
        if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
            logging.warning("Twilio no configurado: faltan credenciales")
            return None
        _client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return _client

def make_call(message, to_number=None):
    client = _get_client()
    if not client:
        return False, "Twilio no configurado"

    to = to_number or MY_PHONE_NUMBER
    if not to:
        return False, "No hay número destino configurado (MY_PHONE_NUMBER)"

    twiml = f'<Response><Say voice="alice" language="es-CR">{message}</Say></Response>'

    try:
        call = client.calls.create(
            twiml=twiml,
            to=to,
            from_=TWILIO_PHONE_NUMBER
        )
        logging.info(f"Llamada iniciada: {call.sid} -> {to}")
        return True, f"Llamada iniciada (SID: {call.sid})"
    except Exception as e:
        logging.error(f"Error al llamar: {e}")
        return False, str(e)
