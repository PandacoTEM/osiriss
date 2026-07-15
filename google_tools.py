from datetime import datetime, timedelta
from googleapiclient.discovery import build
from auth import get_credentials

def _service(name, version):
    creds = get_credentials()
    if not creds:
        return None
    return build(name, version, credentials=creds)

def create_event(summary, start_dt_str, duration_min=60):
    svc = _service("calendar", "v3")
    if not svc:
        return None
    start_dt = datetime.strptime(start_dt_str, "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=duration_min)
    event = {
        "summary": summary,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Mexico_City"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Mexico_City"}
    }
    ev = svc.events().insert(calendarId="primary", body=event).execute()
    return ev.get("htmlLink")

def list_events(date_str=None):
    svc = _service("calendar", "v3")
    if not svc:
        return None
    if date_str:
        start = f"{date_str}T00:00:00"
        end = f"{date_str}T23:59:59"
    else:
        d = datetime.now().strftime("%Y-%m-%d")
        start = f"{d}T00:00:00"
        end = f"{d}T23:59:59"
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

def search_youtube(query, max_results=3):
    svc = _service("youtube", "v3")
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
        result.append(f"\U0001f3ac *{title}*\n   \U0001f469\u200d\U0001f3a4 {channel}\n   \U0001f517 [Ver]({link})")
    return result

def search_drive(query, max_results=5):
    svc = _service("drive", "v3")
    if not svc:
        return None
    resp = svc.files().list(
        q=f"name contains '{query}' and trashed=false",
        pageSize=max_results, fields="files(id,name,mimeType)"
    ).execute()
    items = resp.get("files", [])
    if not items:
        return ["No se encontraron archivos en Drive."]
    result = []
    for f in items:
        link = f"https://drive.google.com/file/d/{f['id']}/view"
        icon = "\U0001f4c4" if f.get("mimeType") == "application/pdf" else "\U0001f4c1"
        result.append(f"{icon} *{f['name']}*\n   \U0001f517 [Abrir]({link})")
    return result
