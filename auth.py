import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from database import save_token, get_token

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly"
]
CREDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")

def get_auth_url():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if client_id and client_secret:
        flow = InstalledAppFlow.from_client_config(
            {"installed": {"client_id": client_id, "client_secret": client_secret, "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"]}},
            SCOPES
        )
    else:
        flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
    flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
    auth_url, _ = flow.authorization_url(prompt="consent")
    return auth_url, flow

def exchange_code(flow, code):
    flow.fetch_token(code=code.strip())
    creds = flow.credentials
    save_token(0, creds.to_json())
    return creds

def get_credentials():
    token_data = get_token(0)
    if not token_data:
        return None
    creds = Credentials.from_authorized_user_info(json.loads(token_data), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_token(0, creds.to_json())
    return creds if creds and creds.valid else None

def is_authenticated():
    return get_credentials() is not None
