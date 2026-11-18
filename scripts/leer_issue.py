"""Lee el link (y opciones) desde el Issue o desde el formulario manual. Imprime líneas key=value."""
import os
import re

body = os.environ.get("ISSUE_BODY") or ""
title = os.environ.get("ISSUE_TITLE") or ""
url = (os.environ.get("IN_URL") or "").strip()
start = (os.environ.get("IN_START") or "").strip()
minutes = (os.environ.get("IN_MINUTES") or "").strip()
mine = (os.environ.get("IN_MINE") or "").strip().lower() == "true"
topic = (os.environ.get("IN_TOPIC") or "").strip()
hours = (os.environ.get("IN_HOURS") or "").strip()

if body:
    m = re.search(r"https?://\S+", body)
    url = url or (m.group(0).rstrip(").,>") if m else "")
    sec = lambda label: re.search(label + r"[^\n]*\n+\s*([^\n#]+)", body)
    s = sec(r"Desde qué minuto")
    if s and re.fullmatch(r"[\d:]+", s.group(1).strip()):
        start = s.group(1).strip()
    tt = sec(r"Juego o tema")
    if tt and tt.group(1).strip() and "No response" not in tt.group(1):
        topic = tt.group(1).strip()
    hh = sec(r"Horas a cubrir")
    if hh and re.fullmatch(r"\d+(\.\d+)?", hh.group(1).strip()):
        hours = hh.group(1).strip()
    mm = sec(r"Cuántos minutos")
    if mm and mm.group(1).strip().isdigit():
        minutes = mm.group(1).strip()
    mine = mine or bool(re.search(r"- \[x\][^\n]*#mio|#mio\b(?![^\n]*—)", body, re.I)) or "#mio" in title.lower()

if not re.match(r"https?://", url or ""):
    raise SystemExit("No encontré un link válido (debe empezar por http)")
safe = lambda v: re.sub(r"[\r\n]", "", v)
print(f"url={safe(url)}")
print(f"start={safe(start)}")
print(f"minutes={safe(minutes)}")
print(f"mine={'true' if mine else 'false'}")
print(f"topic={safe(topic)[:40]}")
print(f"hours={hours if re.fullmatch(r'\d+(\.\d+)?', hours) else ''}")
