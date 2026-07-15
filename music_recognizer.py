import os
import requests

AUDD_URL = "https://api.audd.io/"

def recognize(file_path):
    token = os.getenv("AUDD_API_KEY")
    if not token:
        return None
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(AUDD_URL, data={"api_token": token}, files={"file": f})
        data = resp.json()
        if data.get("status") == "success" and data.get("result"):
            r = data["result"]
            title = r.get("title", "?")
            artist = r.get("artist", "?")
            album = r.get("album", "?")
            line = f"\U0001f3b5 *Canci\u00f3n:* {title}\n\U0001f469\u200d\U0001f3a4 *Artista:* {artist}\n\U0001f4bf *\u00c1lbum:* {album}"
            if r.get("song_link"):
                line += f"\n\U0001f517 [Escuchar]({r['song_link']})"
            return line
        return None
    except Exception as e:
        return None
