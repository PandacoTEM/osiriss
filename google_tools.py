import os
import base64
from email.message import EmailMessage
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build
from auth import get_credentials

TIMEZONE = os.getenv("TIMEZONE") or os.getenv("TZ") or "America/Costa_Rica"


def _service(user_id, name, version):
    creds = get_credentials(user_id)
    if not creds:
        return None
    return build(name, version, credentials=creds)

def create_event(user_id, summary, start_dt_str, duration_min=60):
    svc = _service(user_id, "calendar", "v3")
    if not svc:
        return None
    start_dt = datetime.strptime(start_dt_str, "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=duration_min)
    event = {
        "summary": summary,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE}
    }
    ev = svc.events().insert(calendarId="primary", body=event).execute()
    return ev.get("htmlLink")

def list_events(user_id, date_str=None):
    svc = _service(user_id, "calendar", "v3")
    if not svc:
        return None
    tz = ZoneInfo(TIMEZONE)
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else datetime.now(tz).date()
    start = datetime.combine(target_date, time.min, tzinfo=tz).isoformat()
    end = datetime.combine(target_date, time.max, tzinfo=tz).isoformat()
    events = svc.events().list(
        calendarId="primary", timeMin=start, timeMax=end,
        singleEvents=True, orderBy="startTime"
    ).execute()
    items = events.get("items", [])
    if not items:
        return []
    result = []
    for ev in items:
        s = ev["start"].get("dateTime", ev["start"].get("date"))
        result.append(f"\u2022 {ev['summary']} ({s})")
    return result

def search_youtube(user_id, query, max_results=3):
    svc = _service(user_id, "youtube", "v3")
    if not svc:
        return None
    resp = svc.search().list(q=query, part="snippet", type="video", maxResults=max_results).execute()
    items = resp.get("items", [])
    if not items:
        return ["No se encontraron videos."]
    result = []
    for i in items:
        vid = i["id"]["videoId"]
        title = i["snippet"]["title"]
        channel = i["snippet"]["channelTitle"]
        link = f"https://youtu.be/{vid}"
        result.append(f"\U0001f3ac {title}\n   \U0001f469\u200d\U0001f3a4 {channel}\n   \U0001f517 {link}")
    return result

def search_drive(user_id, query, max_results=5):
    svc = _service(user_id, "drive", "v3")
    if not svc:
        return None
    resp = svc.files().list(
        q=f"name contains '{query.replace(chr(39), chr(92) + chr(39))}' and trashed=false",
        pageSize=max_results, fields="files(id,name,mimeType)"
    ).execute()
    items = resp.get("files", [])
    if not items:
        return ["No se encontraron archivos en Drive."]
    result = []
    for f in items:
        link = f"https://drive.google.com/file/d/{f['id']}/view"
        icon = "\U0001f4c4" if f.get("mimeType") == "application/pdf" else "\U0001f4c1"
        result.append(f"{icon} {f['name']}\n   \U0001f517 {link}")
    return result


def create_gmail_draft(user_id, to_address, subject, body):
    svc = _service(user_id, "gmail", "v1")
    if not svc:
        return None
    message = EmailMessage()
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    draft = svc.users().drafts().create(
        userId="me",
        body={"message": {"raw": raw}},
    ).execute()
    return draft.get("id")
