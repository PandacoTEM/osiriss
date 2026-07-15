import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly"
]
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokens.json")
CREDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")

def get_auth_url():
    flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
    flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
    auth_url, _ = flow.authorization_url(prompt="consent")
    return auth_url, flow

def exchange_code(flow, code):
    flow.fetch_token(code=code.strip())
    creds = flow.credentials
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    return creds

def get_credentials():
    if not os.path.exists(TOKEN_FILE):
        return None
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds if creds and creds.valid else None

def is_authenticated():
    return get_credentials() is not None
