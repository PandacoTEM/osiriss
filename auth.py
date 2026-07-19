import base64
import hashlib
import json
import os
import threading
import requests
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from cryptography.fernet import Fernet, InvalidToken

from database import delete_token, get_token, save_token


SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]
CREDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
_pending_flows = {}
_flow_lock = threading.Lock()


def _cipher():
    secret = (
        os.getenv("GOOGLE_TOKEN_ENCRYPTION_KEY")
        or os.getenv("DASHBOARD_SESSION_SECRET")
        or os.getenv("TELEGRAM_WEBHOOK_SECRET")
    )
    if not secret:
        raise RuntimeError("Configura GOOGLE_TOKEN_ENCRYPTION_KEY para proteger los tokens de Google")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _encrypt_token(token_data):
    return "enc:" + _cipher().encrypt(token_data.encode("utf-8")).decode("ascii")


def _decrypt_token(token_data):
    if not token_data.startswith("enc:"):
        return token_data
    try:
        return _cipher().decrypt(token_data[4:].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("No se pudo descifrar el token de Google") from exc


def _redirect_uri():
    configured = os.getenv("GOOGLE_REDIRECT_URI")
    if configured:
        return configured
    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "https://osiriss.onrender.com/webhook")
    return webhook_url.removesuffix("/webhook") + "/oauth2/callback"


def _new_flow():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = _redirect_uri()
    if client_id and client_secret:
        config = {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        }
        return Flow.from_client_config(config, scopes=SCOPES, redirect_uri=redirect_uri)
    return Flow.from_client_secrets_file(CREDS_FILE, scopes=SCOPES, redirect_uri=redirect_uri)


def get_auth_url(user_id):
    flow = _new_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    with _flow_lock:
        _pending_flows[state] = (flow, int(user_id), expires_at)
        expired = [key for key, (_, _, expiry) in _pending_flows.items() if expiry < datetime.now(timezone.utc)]
        for key in expired:
            _pending_flows.pop(key, None)
    return auth_url


def complete_auth(state, code):
    with _flow_lock:
        pending = _pending_flows.pop(state, None)
    if not pending:
        raise ValueError("Solicitud OAuth invalida o expirada")
    flow, user_id, expires_at = pending
    if expires_at < datetime.now(timezone.utc):
        raise ValueError("Solicitud OAuth expirada")
    flow.fetch_token(code=code)
    save_token(user_id, _encrypt_token(flow.credentials.to_json()))
    return user_id


def get_credentials(user_id):
    token_data = get_token(user_id)
    if not token_data:
        return None
    decrypted = _decrypt_token(token_data)
    creds = Credentials.from_authorized_user_info(json.loads(decrypted), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_token(user_id, _encrypt_token(creds.to_json()))
    elif not token_data.startswith("enc:"):
        save_token(user_id, _encrypt_token(creds.to_json()))
    return creds if creds.valid else None


def is_authenticated(user_id):
    return get_credentials(user_id) is not None


def revoke_google_access(user_id):
    token_data = get_token(user_id)
    if not token_data:
        return False, False
    remotely_revoked = False
    try:
        credentials = json.loads(_decrypt_token(token_data))
        token = credentials.get("refresh_token") or credentials.get("token")
        if token:
            try:
                response = requests.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": token},
                    headers={"content-type": "application/x-www-form-urlencoded"},
                    timeout=10,
                )
                remotely_revoked = response.status_code == 200
            except requests.RequestException:
                remotely_revoked = False
    finally:
        delete_token(user_id)
    return True, remotely_revoked
