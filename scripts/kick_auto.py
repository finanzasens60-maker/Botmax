"""Imprime las variables del directo largo en curso (kick_auto en config.yaml) para el workflow programado."""
import datetime

import yaml

c = yaml.safe_load(open("config.yaml", encoding="utf-8")).get("kick_auto") or {}
activo = bool(c.get("channel")) and str(datetime.date.today()) <= str(c.get("until", ""))
print(f"IN_CHANNEL={c.get('channel', '')}")
print(f"IN_COUNT={c.get('count', 10)}")
print(f"IN_ISSUE={c.get('issue', '')}")
print(f"IN_YOUTUBE={1 if c.get('youtube') else 0}")
print(f"IN_MAXYT={c.get('youtube_per_day', 6)}")
print(f"ACTIVO={1 if activo else 0}")
