"""Lee las opciones del Issue de resumen (o del formulario manual). Imprime líneas key=value."""
import os
import re

body = os.environ.get("ISSUE_BODY") or ""
title = os.environ.get("ISSUE_TITLE") or ""
url = (os.environ.get("IN_URL") or "").strip()
parts = (os.environ.get("IN_PARTS") or "").strip()
minutes = (os.environ.get("IN_MINUTES") or "").strip()
topic = (os.environ.get("IN_TOPIC") or "").strip()
mine = (os.environ.get("IN_MINE") or "").strip().lower() == "true"

if body:
    m = re.search(r"https?://\S+", body)
    url = url or (m.group(0).rstrip(").,>") if m else "")
    sec = lambda label: re.search(label + r"[^\n]*\n+\s*([^\n#]+)", body)
    p = sec(r"Cuántos videos")
    if p and p.group(1).strip().isdigit():
        parts = p.group(1).strip()
    d = sec(r"Duración de cada video")
    if d and d.group(1).strip().isdigit():
        minutes = d.group(1).strip()
    t = sec(r"Juego o tema")
    if t and t.group(1).strip() and "No response" not in t.group(1):
        topic = t.group(1).strip()
    mine = mine or bool(re.search(r"- \[x\][^\n]*#mio", body, re.I)) or "#mio" in title.lower()

if not re.match(r"https?://", url or ""):
    raise SystemExit("No encontré un link válido (debe empezar por http)")
clean = lambda v: re.sub(r"[\r\n\"'`$]", "", v)
print(f"url={clean(url)}")
print(f"parts={parts if parts.isdigit() else '1'}")
print(f"minutes={minutes if minutes.isdigit() else '10'}")
print(f"topic={clean(topic)[:40]}")
print(f"mine={'true' if mine else 'false'}")
