from datetime import datetime
from tzlocal import get_localzone
now = datetime.now(get_localzone())
print(f"Hoy es: {now.strftime('%Y-%m-%d %A')}")
