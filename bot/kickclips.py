"""Clips virales a partir de los clips que hizo el PÚBLICO en Kick.

En vez de adivinar con el volumen, usa lo que la gente ya eligió: Kick guarda los clips que los
espectadores cortaron de cada directo, con su número de vistas. Este módulo:
  1. trae todos los clips de un directo (por id del directo o link del VOD), ordenados por vistas,
  2. descarta temas que YouTube castiga (desnudos, acoso…),
  3. los transcribe y la IA elige el mejor tramo (máx. 58 s), le pone título con gancho y puntúa su potencial viral,
  4. los arma en vertical con el estilo del canal y los deja en un Release (y en el Issue si se indica).

Uso: python -m bot.kickclips westcol --vod https://kick.com/westcol/videos/<uuid> --count 25 --issue 3
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from . import live, publish, script
from .main import github_release, load_state, save_state

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0 Safari/537.36",
      "Accept": "application/json"}
# Títulos de clip que casi siempre traen contenido que YouTube restringe o desmonetiza
VETO = re.compile(r"teta|tetas|culo|c\*lo|desnud|nude|nsfw|acoso|porno|sexo|pene|vagina|chichi", re.I)

SYSTEM = """Eres editor de un canal de YouTube Shorts en español latino que publica los mejores momentos de directos.
Te paso clips que el público cortó de un directo (con su número de vistas, el título que le puso quien lo cortó
y la transcripción con tiempos en segundos). Para CADA clip:
- "score": del 1 al 10, qué tan viral puede ser como Short (gracia, sorpresa, polémica sana, emoción, frase memorable).
  Pon 1 si tiene contenido sexual, desnudos, acoso, violencia real grave o insultos discriminatorios.
- "start" y "end": el mejor tramo en segundos (entre 12 y 58 s de duración), que empiece con fuerza y cierre bien.
- "title": título de YouTube de máximo 65 caracteres, con gancho y curiosidad, fiel a lo que pasa. Sin emojis al inicio.
- "headline": titular corto en MAYÚSCULAS para la pantalla (máx. 6 palabras).
- "description": 1-2 frases de contexto.
- "hashtags": 3 a 5 hashtags.
También te paso 3 fotogramas de cada clip (inicio, mitad y final): úsalos para entender qué pasa de verdad
(quién aparece, dónde están, qué hacen). El título y el titular deben describir EXACTAMENTE lo que se ve y se oye
en el tramo elegido: que quien lo vea sienta que el título le cumplió.
En los directos largos participan el streamer, su grupo de amigos (la ganga) e invitados: NO atribuyas todo al
streamer. Si no está claro quién lo dice o lo hace, usa "la ganga", "un invitado", "uno del parche" o descríbelo
sin nombre. Solo nombra a alguien si su nombre aparece en la transcripción o en el título del clip.
No inventes nada que no esté en la transcripción, los fotogramas o el título del clip.
Responde SOLO JSON: {"clips": [{"id": 1, "score": 8, "start": 3.0, "end": 41.0, "title": "...", "headline": "...",
"description": "...", "hashtags": ["#..."]}]}"""


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, **kw)


def livestream_id(vod: str, channel: str = "") -> tuple[str, str]:
    if vod == "live":  # el directo de ahora (o el último si ya terminó)
        r = requests.get(f"https://kick.com/api/v2/channels/{channel}/livestream", headers=UA, timeout=30)
        d = (r.json() or {}).get("data") if r.status_code == 200 else None
        if d:
            return str(d["id"]), d.get("session_title", "")
        vids = requests.get(f"https://kick.com/api/v2/channels/{channel}/videos", headers=UA, timeout=30).json()
        vids = vids if isinstance(vids, list) else vids.get("data", [])
        return (str(vids[0]["id"]), vids[0].get("session_title", "")) if vids else ("", "")
    m = re.search(r"videos/([0-9a-f-]{36})", vod)
    if not m:
        return vod, ""  # ya es un id de directo
    d = requests.get(f"https://kick.com/api/v1/video/{m.group(1)}", headers=UA, timeout=30).json()
    ls = d.get("livestream") or {}
    return str(ls.get("id")), ls.get("session_title", "")


def fetch_clips(channel: str, stream_id: str, pages: int = 150) -> list[dict]:
    """Clips del directo: los de hoy, luego de la semana (y si hay pocos, del último mes)."""
    out = _fetch(channel, stream_id, "day", 60)
    out += _fetch(channel, stream_id, "week", pages)
    if len(out) < 40:
        out += _fetch(channel, stream_id, "month", pages)
    return _uniq(out)


def _uniq(out: list[dict]) -> list[dict]:
    seen, uniq = set(), []
    for c in sorted(out, key=lambda c: -int(c.get("view_count") or c.get("views") or 0)):
        if c["id"] not in seen:
            seen.add(c["id"])
            uniq.append(c)
    return uniq


def _fetch(channel: str, stream_id: str, time_range: str, pages: int) -> list[dict]:
    out, cursor = [], None
    for _ in range(pages):
        url = f"https://kick.com/api/v2/channels/{channel}/clips?sort=view&time={time_range}"
        if cursor:
            url += f"&cursor={requests.utils.quote(cursor, safe='')}"
        r = requests.get(url, headers=UA, timeout=30)
        if r.status_code != 200:
            print(f"[kick] la API respondió {r.status_code}; sigo con lo que tengo")
            break
        d = r.json()
        out += [c for c in d.get("clips", []) if str(c.get("livestream_id")) == str(stream_id)]
        cursor = d.get("nextCursor")
        if not cursor or not d.get("clips"):
            break
    return out


def transcribe_words(model, wav: Path) -> list[dict]:
    import numpy as np
    with wave.open(str(wav), "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    segs, _ = model.transcribe(pcm, language="es", vad_filter=True, beam_size=1, word_timestamps=True)
    return [{"word": x.word.strip(), "start": float(x.start), "end": float(x.end)} for s in segs for x in (s.words or [])]


def frames(video: Path, dur: float, n: int = 3) -> list[dict]:
    """Fotogramas pequeños (JPG en base64) para que la IA vea qué pasa en el clip."""
    import base64
    parts = []
    for k in range(n):
        t = dur * (0.15 + 0.7 * k / max(1, n - 1))
        jpg = video.with_name(f"{video.stem}_f{k}.jpg")
        try:
            sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                "-vf", "scale=480:-2", "-q:v", "5", str(jpg)], timeout=60)
            parts.append({"inline_data": {"mime_type": "image/jpeg",
                                          "data": base64.b64encode(jpg.read_bytes()).decode()}})
            jpg.unlink(missing_ok=True)
        except Exception:
            pass
    return parts


def timed_text(words: list[dict], limit: int = 1100) -> str:
    out, last = [], -99.0
    for w in words:
        if w["start"] - last >= 5:
            out.append(f"[{w['start']:.0f}s]")
            last = w["start"]
        out.append(w["word"])
    return " ".join(out)[:limit] or "(casi sin habla: gritos, risas o música)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("channel")
    ap.add_argument("--vod", required=True, help="link del VOD de Kick o id del directo")
    ap.add_argument("--count", type=int, default=25)
    ap.add_argument("--pool", type=int, default=45, help="cuántos de los más vistos revisar")
    ap.add_argument("--min-score", type=int, default=6)
    ap.add_argument("--skip", type=int, default=0, help="saltar los N más vistos (si ya se usaron)")
    ap.add_argument("--youtube", action="store_true", help="subirlos a YouTube como PRIVADOS (los revisas y publicas tú)")
    ap.add_argument("--max-youtube", type=int, default=6, help="tope de clips de Kick subidos a YouTube por día")
    a = ap.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    streamer = a.channel.capitalize()
    live.TOPIC = streamer
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    work = Path("out") / f"{stamp}-kickclips-{a.channel}"
    work.mkdir(parents=True, exist_ok=True)

    sid, stitle = livestream_id(a.vod, a.channel)
    clips = fetch_clips(a.channel, sid)
    print(f"[kick] directo {sid} '{stitle}': {len(clips)} clips del público")
    usados = {it.get("key") for it in load_state()["items"]}
    clips = [c for c in clips if not VETO.search(c.get("title") or "") and f"kick-{c['id']}" not in usados]
    print(f"[kick] {len(clips)} sin usar todavía")
    if not clips:
        Path("out/live_summary.md").write_text("Todavía no hay clips nuevos del público en este directo.", encoding="utf-8")
        return
    pool = clips[a.skip:a.skip + a.pool]
    print(f"[kick] reviso los {len(pool)} más vistos (sin temas vetados)")

    from faster_whisper import WhisperModel
    model = WhisperModel(cfg["live"]["whisper_model"], device="cpu", compute_type="int8")
    items = []
    for i, c in enumerate(pool, 1):
        mp4 = work / f"k{i}.mp4"
        try:
            sh(["ffmpeg", "-y", "-loglevel", "error", "-i", c["clip_url"], "-c", "copy", "-bsf:a", "aac_adtstoasc", str(mp4)],
               timeout=300)
            wav = work / f"k{i}.wav"
            sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
            words = transcribe_words(model, wav)
            wav.unlink(missing_ok=True)
        except Exception as exc:
            print(f"[kick] no pude bajar el clip {c['id']}: {exc}")
            continue
        dur = float(c.get("duration") or 30)
        items.append({"id": i, "clip": c, "file": mp4, "words": words, "dur": dur})
        print(f"[kick] {i:02d}. {c.get('view_count')} vistas · {dur:.0f}s · '{c.get('title')}'")

    meta: dict[int, dict] = {}
    ctx = f"Streamer: {streamer} (en el directo también están su grupo, la ganga, e invitados)\nDirecto: {stitle}"
    for k in range(0, len(items), 8):
        batch = items[k:k + 8]
        listing = "\n\n".join(
            f"[id {it['id']}] {it['clip'].get('view_count')} vistas · {it['dur']:.0f}s · título del clip: "
            f"'{it['clip'].get('title')}'\n{timed_text(it['words'])}" for it in batch)
        script.EXTRA_PARTS[:] = []
        for it in batch:
            script.EXTRA_PARTS.append({"text": f"Fotogramas del clip id {it['id']}:"})
            script.EXTRA_PARTS += frames(it["file"], it["dur"])
        try:
            data = live._raw_llm(script._gemini, SYSTEM, f"{ctx}\n\nCLIPS:\n{listing}", "gemini-2.5-flash")
            meta.update({int(x["id"]): x for x in data.get("clips", []) if "id" in x})
        except Exception as exc:
            print(f"[kick] IA falló en la tanda {k // 8 + 1}: {exc}")
        finally:
            script.EXTRA_PARTS[:] = []

    ranked = []
    for it in items:
        m = meta.get(it["id"])
        if not m or int(m.get("score", 0)) < a.min_score:
            continue
        views = int(it["clip"].get("view_count") or 0)
        ranked.append((int(m["score"]) * 10 + min(views, 5000) / 250, it, m))
    ranked.sort(key=lambda r: -r[0])
    chosen = ranked[:a.count]
    print(f"[kick] {len(chosen)} clips pasan el filtro de calidad (score ≥ {a.min_score})")

    credit = f"Directo de {streamer} (Kick)"
    files, results = [], []
    for n, (_, it, m) in enumerate(chosen, 1):
        s = max(0.0, float(m.get("start", 0)))
        e = min(it["dur"], float(m.get("end", it["dur"])))
        if e - s < 10:
            s, e = 0.0, min(it["dur"], 58.0)
        e = min(e, s + 58)
        rel = {"abs_start": s, "abs_end": e, "headline": m.get("headline", m["title"]).upper()[:60], "title": m["title"]}
        video = live.render_clip(cfg, it["file"], rel, it["words"], credit, n, work)
        out = work / f"{a.channel}_{n:02d}.mp4"
        video.rename(out)
        tags = [t if t.startswith("#") else f"#{t}" for t in m.get("hashtags", [])][:5]
        tags += [t for t in (f"#{streamer}", "#Shorts") if t.lower() not in [x.lower() for x in tags]]
        description = "\n".join([
            m.get("description", ""), "",
            f"🎥 Clip del directo de {streamer}: https://kick.com/{a.channel}",
            "Todo el crédito es del creador original.", "", " ".join(tags)])
        txt = out.with_suffix(".txt")
        txt.write_text(f"TÍTULO:\n{m['title']}\n\nDESCRIPCIÓN:\n{description}\n\n"
                       f"VISTAS EN KICK: {it['clip'].get('view_count')} · PUNTAJE VIRAL: {m.get('score')}/10\n",
                       encoding="utf-8")
        files += [out, txt]
        results.append({"type": "live", "key": f"kick-{it['clip']['id']}", "title": m["title"], "date": stamp,
                        "source": it["clip"]["clip_url"], "score": m.get("score"),
                        "views": it["clip"].get("view_count"), "youtube": None,
                        "_up": (out, {"title": m["title"], "hashtags": tags}, description)})
        print(f"[kick] {n:02d}. ({m.get('score')}/10, {it['clip'].get('view_count')} vistas) {m['title']}")

    # Subida a YouTube SIEMPRE privada, los mejores primero; si YouTube corta por el límite diario,
    # los que falten no se marcan como usados y se intentan en la próxima revisión.
    limite = False
    if a.youtube:
        ycfg = json.loads(json.dumps(cfg))
        ycfg["publish"]["youtube"]["privacy"] = "private"
        ycfg["publish"]["youtube"]["default_tags"] = [streamer, "clips", "directo", "kick", "shorts"]
        hoy = stamp[:8]
        ya = sum(1 for it in load_state()["items"]
                 if str(it.get("key", "")).startswith("kick-") and it.get("youtube") and str(it.get("date", ""))[:8] == hoy)
        for r in results:
            if ya >= a.max_youtube:
                limite = True
            if limite:
                r["pendiente"] = True
                continue
            try:
                r["youtube"] = publish.upload_youtube(*r["_up"][:3], ycfg)
                ya += 1 if r["youtube"] else 0
            except Exception as exc:
                print(f"[youtube] ERROR: {exc}")
                if "uploadLimitExceeded" in str(exc) or "exceeded the number of videos" in str(exc) or "quota" in str(exc).lower():
                    limite = True
                    r["pendiente"] = True
    for r in results:
        r.pop("_up", None)

    rel_url = None
    if files:
        rel_url = github_release(files[0], files[1], {"title": f"Clips virales: {stitle or streamer}"[:100]},
                                 f"kick-{a.channel}-{stamp}", extra=files[2:])
        for r in results:
            r["release"] = rel_url
        state = load_state()
        state["items"] += results  # los pendientes también: la cola de YouTube (subir_cola.py) los sube
        save_state(state)
    lines = [f"**{len(results)} clips virales** sacados de los clips más vistos por el público en Kick "
             f"(de {len(clips)} clips del directo).", ""]
    lines += [f"{i}. {r['title']} — {r['score']}/10 · {r['views']} vistas en Kick"
              + (f" — {r['youtube']}" if r.get("youtube") else (" — (en cola: se sube solo, en privado, cuando YouTube libere el tope)" if r.get("pendiente") else ""))
              for i, r in enumerate(results, 1)]
    if rel_url:
        lines += ["", f"Descárgalos aquí: {rel_url}"]
    if a.youtube:
        lines += ["", "Subidos a YouTube como **privados**: revísalos en YouTube Studio y publica los que quieras."]
        if limite:
            lines += ["Se llegó al tope de subidas de hoy; la cola los sube solos (privados) en las próximas horas."]
    else:
        lines += ["", "No se subieron a YouTube. Revísalos antes de publicarlos."]
    Path("out/live_summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
