"""Cola de subida a YouTube (siempre PRIVADO).

Revisa los Releases del bot, encuentra los videos que todavía no están en YouTube y los sube en orden
de prioridad hasta que YouTube diga que se llegó al tope del día. Lo que falte se sube en la próxima
ejecución. Nunca sube dos veces el mismo video.

Prioridad: clips virales de Kick (westcol 72 h) > mejores momentos > clips de directos > Shorts propios.

Si TikTok está conectado, también manda los clips a la BANDEJA de TikTok (borradores para publicar desde la app).

Uso: python scripts/subir_cola.py [--desde 20261119] [--max 20] [--solo kick-westcol] [--max-tiktok 4] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import publish, tiktok  # noqa: E402
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
    ap.add_argument("--max-tiktok", type=int, default=4, help="máximo de envíos a TikTok en esta ejecución")
    ap.add_argument("--tiktok-dia", type=int, default=12, help="máximo de envíos a TikTok en 24 h")
    ap.add_argument("--solo-youtube", action="store_true")
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

    tt_on = bool(os.environ.get("TIKTOK_CLIENT_SECRET")) and tiktok.TOKEN_FILE.exists() and not a.solo_youtube
    tt_hechos = {it["key"] for it in state["items"] if it.get("tiktok")}
    hace24 = time.time() - 86400
    tt_hoy = sum(1 for it in state["items"] if it.get("tiktok") and it.get("ts", 0) >= hace24)

    todos = []
    for rel in releases(a.desde, a.solo):
        tag = rel["tag"]
        for mp4, txt in pares(tag):
            key = f"cola-{tag}-{mp4[:-4]}"
            ttkey = f"tt-{tag}-{mp4[:-4]}"
            falta_tt = tt_on and ttkey not in tt_hechos and not tag.startswith("largo-")
            if key in hechos and not falta_tt:
                continue
            titulo, desc = "", ""
            if txt:
                d = work / tag
                d.mkdir(exist_ok=True)
                gh("release", "download", tag, "-p", txt, "-D", str(d), "--clobber")
                titulo, desc = leer_txt((d / txt).read_text(encoding="utf-8"))
            if not titulo:
                titulo = f"{tag} {mp4[:-4]}"
            falta_yt = key not in hechos and (tag, titulo.lower()) not in subidos_por_titulo
            if falta_yt or falta_tt:
                todos.append({"tag": tag, "mp4": mp4, "key": key, "ttkey": ttkey, "title": titulo, "desc": desc,
                              "yt": falta_yt, "tt": falta_tt})

    pendientes = [p for p in todos if p["yt"]]
    pend_tt = [p for p in todos if p["tt"]]
    print(f"[cola] {len(pendientes)} videos sin subir a YouTube" + (f", {len(pend_tt)} sin mandar a TikTok" if tt_on else ""))
    for p in pendientes:
        print(f"  - {p['tag']}/{p['mp4']}: {p['title']}")
    if a.dry_run:
        return

    def bajar(p: dict) -> Path:
        d = work / p["tag"]
        d.mkdir(exist_ok=True)
        f = d / p["mp4"]
        if not f.exists():
            gh("release", "download", p["tag"], "-p", p["mp4"], "-D", str(d), "--clobber")
        return f

    subidos, limite = [], False
    for p in pendientes:
        if len(subidos) >= a.max:
            break
        f = bajar(p)
        largo = p["tag"].startswith("largo-")
        tags = re.findall(r"#\w+", p["desc"])[:8] or ["#GTA6"]
        try:
            url = publish.upload_youtube(f, {"title": p["title"], "hashtags": tags}, p["desc"], cfg, short=not largo)
        except Exception as exc:  # noqa: BLE001
            print(f"[youtube] ERROR: {exc}")
            if any(s in str(exc) for s in LIMITE):
                limite = True
                break
            continue
        if url:
            state["items"].append({"type": "cola", "key": p["key"], "title": p["title"], "date": stamp, "youtube": url,
                                   "release": f"https://github.com/{gh_repo()}/releases/tag/{p['tag']}"})
            save_state(state)
            subidos.append(p | {"url": url})
            print(f"[cola] subido (privado): {p['title']} -> {url}")

    # TikTok: a la bandeja (borradores), con tope propio por día
    enviados_tt, tope_tt = [], False
    if tt_on:
        for p in pend_tt:
            if len(enviados_tt) >= a.max_tiktok or tt_hoy + len(enviados_tt) >= a.tiktok_dia:
                tope_tt = True
                break
            try:
                pid = tiktok.upload_inbox(bajar(p))
            except tiktok.TikTokLimit as exc:
                print(f"[tiktok] tope: {exc}")
                tope_tt = True
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[tiktok] ERROR: {exc}")
                if "conectad" in str(exc) or "renovar" in str(exc):
                    break
                continue
            tags = " ".join(dict.fromkeys([t for t in re.findall(r"#\w+", p["desc"]) if t.lower() != "#shorts"] + ["#GTA6", "#fyp"]))
            caption = f"{p['title']} {tags}"[:2200]
            state["items"].append({"type": "tiktok", "key": p["ttkey"], "title": p["title"], "date": stamp,
                                   "ts": int(time.time()), "tiktok": pid, "caption": caption})
            save_state(state)
            enviados_tt.append(p | {"caption": caption})
            time.sleep(12)  # TikTok permite 6 inicios por minuto

    for f in work.rglob("*.mp4"):
        f.unlink(missing_ok=True)

    faltan = len(pendientes) - len(subidos)
    lines = [f"**Cola de YouTube:** {len(subidos)} subido(s) como **privados**, {faltan} pendiente(s).", ""]
    lines += [f"- {s['title']} — {s['url']}" for s in subidos]
    if limite:
        lines += ["", "YouTube llegó al tope de subidas del día; sigo en la próxima revisión automática."]
    if tt_on:
        lines += ["", f"**TikTok:** {len(enviados_tt)} video(s) enviados a tu **bandeja** de TikTok "
                      f"({len(pend_tt) - len(enviados_tt)} en espera). Abre la notificación en la app, pega el texto y publica:", ""]
        lines += [f"{i}. `{e['caption']}`" for i, e in enumerate(enviados_tt, 1)]
        if tope_tt:
            lines += ["", "Llegué al tope de TikTok por hoy; los demás van en las próximas revisiones."]
    Path("out/cola_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def gh_repo() -> str:
    import os
    return os.environ.get("GITHUB_REPOSITORY", "finanzasens60-maker/gta6-bot")


if __name__ == "__main__":
    main()
