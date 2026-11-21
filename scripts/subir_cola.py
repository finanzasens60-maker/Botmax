"""Cola de subida a YouTube (siempre PRIVADO).

Revisa los Releases del bot, encuentra los videos que todavía no están en YouTube y los sube en orden
de prioridad hasta que YouTube diga que se llegó al tope del día. Lo que falte se sube en la próxima
ejecución. Nunca sube dos veces el mismo video.

Prioridad: clips virales de Kick (westcol 72 h) > mejores momentos > clips de directos > Shorts propios.

Uso: python scripts/subir_cola.py [--desde 20261119] [--max 20] [--solo kick-westcol] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import publish  # noqa: E402
from bot.main import load_state, save_state  # noqa: E402

PRIORIDAD = ["kick-", "mejores-", "directo-", "video-", "ranking-", "largo-"]
LIMITE = ("uploadLimitExceeded", "exceeded the number of videos", "quotaExceeded")


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, text=True, capture_output=True).stdout


def releases(desde: str, solo: str) -> list[dict]:
    data = json.loads(gh("release", "list", "--limit", "200", "--json", "tagName,createdAt"))
    out = []
    for r in data:
        tag = r["tagName"]
        pref = next((p for p in PRIORIDAD if tag.startswith(p)), None)
        fecha = re.search(r"(\d{8})-\d{6}", tag)
        if not pref or not fecha or fecha.group(1) < desde or (solo and not tag.startswith(solo)):
            continue
        out.append({"tag": tag, "prio": PRIORIDAD.index(pref), "stamp": fecha.group(0)})
    # primero por tipo; dentro del tipo, lo más nuevo primero (es lo que más tracción tiene ahora)
    return sorted(out, key=lambda r: (r["prio"], -int(r["stamp"].replace("-", ""))))


def pares(tag: str) -> list[tuple[str, str | None]]:
    """(video.mp4, texto.txt) de un Release."""
    names = [a["name"] for a in json.loads(gh("release", "view", tag, "--json", "assets"))["assets"]]
    res = []
    for mp4 in sorted((n for n in names if n.endswith(".mp4")), key=lambda n: int(re.sub(r"\D", "", n) or 0)):
        stem = mp4[:-4]
        txt = next((t for t in (f"{stem}.txt", f"{stem}_publicar.txt", "publicar.txt") if t in names), None)
        res.append((mp4, txt))
    return res


def leer_txt(texto: str) -> tuple[str, str]:
    m = re.search(r"T[ÍI]TULO:\s*\n(.+?)\n", texto)
    titulo = m.group(1).strip() if m else ""
    d = re.search(r"DESCRIPCI[ÓO]N[^\n]*:\s*\n(.*?)(?:\n\n(?:GUION|VISTAS EN KICK|MOMENTO DEL DIRECTO)[^\n]*|\Z)", texto, re.S)
    return titulo, (d.group(1).strip() if d else "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="20261119", help="solo Releases desde esta fecha (AAAAMMDD)")
    ap.add_argument("--max", type=int, default=20, help="máximo de subidas en esta ejecución")
    ap.add_argument("--solo", default="", help="prefijo de Releases a considerar (ej. kick-westcol)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg["publish"]["youtube"]["privacy"] = "private"  # SIEMPRE privados: el dueño los revisa y publica
    state = load_state()
    hechos = {it["key"] for it in state["items"] if it.get("youtube")}
    subidos_por_titulo = {(it.get("release", "").rsplit("/", 1)[-1], (it.get("title") or "").strip().lower())
                          for it in state["items"] if it.get("youtube")}
    subidos_por_titulo |= {(it.get("release", "").rsplit("/", 1)[-1], (it.get("video_title") or "").strip().lower())
                           for it in state["items"] if it.get("youtube")}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    work = Path("out") / f"cola-{stamp}"
    work.mkdir(parents=True, exist_ok=True)

    pendientes, subidos, limite = [], [], False
    for rel in releases(a.desde, a.solo):
        tag = rel["tag"]
        for mp4, txt in pares(tag):
            key = f"cola-{tag}-{mp4[:-4]}"
            if key in hechos:
                continue
            titulo, desc = "", ""
            if txt:
                d = work / tag
                d.mkdir(exist_ok=True)
                gh("release", "download", tag, "-p", txt, "-D", str(d), "--clobber")
                titulo, desc = leer_txt((d / txt).read_text(encoding="utf-8"))
            if not titulo:
                titulo = f"{tag} {mp4[:-4]}"
            if (tag, titulo.lower()) in subidos_por_titulo:
                continue  # ya lo subió el workflow que lo creó
            pendientes.append({"tag": tag, "mp4": mp4, "key": key, "title": titulo, "desc": desc})

    print(f"[cola] {len(pendientes)} videos sin subir a YouTube")
    for p in pendientes:
        print(f"  - {p['tag']}/{p['mp4']}: {p['title']}")
    if a.dry_run:
        return

    for p in pendientes:
        if len(subidos) >= a.max:
            break
        d = work / p["tag"]
        d.mkdir(exist_ok=True)
        gh("release", "download", p["tag"], "-p", p["mp4"], "-D", str(d), "--clobber")
        largo = p["tag"].startswith("largo-")
        tags = re.findall(r"#\w+", p["desc"])[:8] or ["#GTA6"]
        try:
            url = publish.upload_youtube(d / p["mp4"], {"title": p["title"], "hashtags": tags}, p["desc"], cfg,
                                         short=not largo)
        except Exception as exc:  # noqa: BLE001
            print(f"[youtube] ERROR: {exc}")
            if any(s in str(exc) for s in LIMITE):
                limite = True
                break
            continue
        finally:
            (d / p["mp4"]).unlink(missing_ok=True)
        if url:
            item = {"type": "cola", "key": p["key"], "title": p["title"], "date": stamp, "youtube": url,
                    "release": f"https://github.com/{gh_repo()}/releases/tag/{p['tag']}"}
            state["items"].append(item)
            save_state(state)
            subidos.append(p | {"url": url})
            print(f"[cola] subido (privado): {p['title']} -> {url}")

    faltan = len(pendientes) - len(subidos)
    lines = [f"**Cola de YouTube:** {len(subidos)} subido(s) como **privados**, {faltan} pendiente(s).", ""]
    lines += [f"- {s['title']} — {s['url']}" for s in subidos]
    if limite:
        lines += ["", "YouTube llegó al tope de subidas del día; sigo en la próxima revisión automática."]
    Path("out/cola_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def gh_repo() -> str:
    import os
    return os.environ.get("GITHUB_REPOSITORY", "finanzasens60-maker/gta6-bot")


if __name__ == "__main__":
    main()
