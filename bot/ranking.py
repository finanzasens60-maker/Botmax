""""LO MÁS VIRAL DE GTA 6 HOY": ranking diario con los clips más vistos de TODA la categoría GTA 6 en Kick.

1. Trae los clips del día de la categoría "Grand Theft Auto VI" en Kick, ordenados por vistas (uno por streamer).
2. Los baja, recorta la cámara del streamer (solo se ve el juego) y los transcribe para saber qué pasa.
3. La IA elige 5, los ordena del 5 al 1 y escribe la narración con el estilo del canal.
4. Cada puesto se arma con su clip de fondo (sonido original bajito), nuestra voz, subtítulos y el crédito
   del streamer en pantalla. Se publica como Short (YouTube privado mientras dure la revisión) y queda en Releases.

Uso: python -m bot.ranking [--dry-run] [--no-youtube]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from . import broll, publish, render, script, tts, visuals
from .main import github_release, load_state, save_state

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0 Safari/537.36",
      "Accept": "application/json"}
VETO = re.compile(r"teta|culo|c\*lo|desnud|nude|nsfw|acoso|porno|sexo|swat|nigg|retard|retrasad", re.I)

SYSTEM = """Eres el guionista de un canal de YouTube Shorts en español latino sobre GTA 6.
Hoy haces el ranking "LO MÁS VIRAL DE GTA SEIS HOY" con clips de streamers de todo el mundo.
Te paso candidatos (streamer, vistas, duración, título que puso el público y transcripción en su idioma).
- Elige los 5 más llamativos (gracia, caos, sorpresa, algo épico del juego). Descarta los que no se entiendan,
  los que tengan contenido sexual, insultos discriminatorios o violencia real.
- Ordénalos del puesto 5 al 1 (el 1 es el mejor).
- "intro": gancho de máximo 14 palabras ("Esto fue lo más viral de GTA seis hoy, y el número uno es una locura").
- Para cada puesto, "line": 22 a 32 palabras, empieza con "Número cinco." / "Número cuatro." … / "Y el número uno.",
  nombra al streamer y cuenta lo que pasa como una mini-escena, con energía y de corrido (conectores
  como "y ojo", "encima", "lo mejor es que"). No inventes lo que no está en el título o la transcripción;
  si no se entiende bien, describe la reacción (gritos, risas, pánico) con honestidad.
- "start": segundo del clip donde empieza lo mejor (para mostrar unos 10 segundos).
- "outro": máximo 14 palabras, pregunta para comentarios ("¿Cuál fue tu favorito?") e invitación a seguir.
- "title" (máx 70 caracteres, con gancho), "headline" (MÁX 6 PALABRAS EN MAYÚSCULAS), "description" (2 frases),
  "hashtags" (3 a 6).
Di "GTA seis" y escribe los números como se dicen.
Responde SOLO JSON: {"intro": "...", "items": [{"id": 3, "rank": 5, "line": "...", "start": 4.0}],
"outro": "...", "title": "...", "headline": "...", "description": "...", "hashtags": ["#GTA6"]}"""


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, **kw)


def fetch_category(slug: str, time_range: str = "day", pages: int = 6) -> list[dict]:
    out, cursor = [], None
    for _ in range(pages):
        url = f"https://kick.com/api/v2/categories/{slug}/clips?sort=view&time={time_range}"
        if cursor:
            url += f"&cursor={requests.utils.quote(cursor, safe='')}"
        r = requests.get(url, headers=UA, timeout=30)
        if r.status_code != 200:
            print(f"[ranking] la API de Kick respondió {r.status_code}")
            break
        d = r.json()
        out += d.get("clips", [])
        cursor = d.get("nextCursor")
        if not cursor:
            break
    return out


def _parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0) if m else text)
    if len(data.get("items", [])) < 3 or not data.get("title"):
        raise ValueError("ranking incompleto")
    return data


def vertical_bg(cfg: dict, src: Path, start: float, seconds: float, out: Path) -> Path:
    """Clip 16:9 centrado sobre su propio fondo borroso (formato vertical), con su sonido original."""
    v = cfg["video"]
    W, H = v["width"], v["height"]
    fc = (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"boxblur=25:4,eq=brightness=-0.2[bg];[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2+60,"
          f"fps={v['fps']},setsar=1,format=yuv420p[v]")
    sh(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-ss", f"{max(0.0, start):.2f}", "-i", str(src),
        "-filter_complex", fc, "-map", "[v]", "-map", "0:a?", "-t", f"{seconds:.2f}", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-ar", "48000", str(out)])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-youtube", action="store_true")
    ap.add_argument("--category", default="grand-theft-auto-vi")
    ap.add_argument("--pool", type=int, default=9)
    a = ap.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg["video"]["game_audio_volume"] = max(0.18, float(cfg["video"].get("game_audio_volume", 0.3)) * 0.7)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    work = Path("out") / f"{stamp}-ranking"
    work.mkdir(parents=True, exist_ok=True)

    clips = fetch_category(a.category)
    seen_ch, pool = set(), []
    for c in sorted(clips, key=lambda c: -int(c.get("view_count") or 0)):
        ch = (c.get("channel") or {}).get("slug")
        if not ch or ch in seen_ch or VETO.search(c.get("title") or "") or float(c.get("duration") or 0) < 8:
            continue
        seen_ch.add(ch)
        pool.append(c)
        if len(pool) >= a.pool:
            break
    print(f"[ranking] {len(clips)} clips de hoy en la categoría; reviso {len(pool)} (uno por streamer)")

    from faster_whisper import WhisperModel
    model = WhisperModel(cfg["live"]["whisper_model"], device="cpu", compute_type="int8")
    cands = []
    for i, c in enumerate(pool, 1):
        raw = work / f"raw{i}.mp4"
        try:
            sh(["ffmpeg", "-y", "-loglevel", "error", "-i", c["clip_url"], "-c", "copy", "-bsf:a", "aac_adtstoasc",
                str(raw)], timeout=300)
        except Exception as exc:
            print(f"[ranking] no pude bajar el clip de {c['channel']['slug']}: {exc}")
            continue
        clean = broll.hide_facecam(raw, keep_audio=True)
        if not clean:
            continue
        wav = work / f"a{i}.wav"
        text = ""
        try:
            sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(clean), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
            import numpy as np
            import wave
            with wave.open(str(wav), "rb") as w:
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
            segs, _ = model.transcribe(pcm, vad_filter=True, beam_size=1)
            text = " ".join(f"[{s.start:.0f}s] {s.text.strip()}" for s in segs)[:700]
        except Exception as exc:
            print(f"[ranking] sin transcripción para {c['channel']['slug']}: {exc}")
        cands.append({"id": i, "clip": c, "file": clean, "text": text})
        print(f"[ranking] {i}. {c.get('view_count')} vistas · {c['channel'].get('username')} · '{c.get('title')}'")
    if len(cands) < 3:
        raise SystemExit("Muy pocos clips hoy para hacer el ranking.")

    listing = "\n\n".join(
        f"[id {k['id']}] {k['clip']['channel'].get('username')} · {k['clip'].get('view_count')} vistas · "
        f"{float(k['clip'].get('duration') or 0):.0f}s · título: '{k['clip'].get('title')}'\n"
        f"{k['text'] or '(sin habla clara)'}" for k in cands)
    data = script._gemini(SYSTEM, f"CANDIDATOS:\n{listing}", cfg["script"].get("gemini_model", "gemini-2.5-flash"),
                          parse=_parse)
    by_id = {k["id"]: k for k in cands}
    items = [it for it in sorted(data["items"], key=lambda x: -int(x.get("rank", 0))) if int(it.get("id", 0)) in by_id][:5]
    print(f"[ranking] título: {data['title']}")

    def fix(t: str) -> str:
        return re.sub(r"\bGTA ?(6|VI)\b", "GTA seis", t)

    parts = [("intro", fix(data["intro"]), items[0])] + [("item", fix(it["line"]), it) for it in items] + \
            [("outro", fix(data["outro"]), items[-1])]
    synth = []
    for n, (kind, text, it) in enumerate(parts):
        voice, words, dur = tts.synthesize(text, cfg, work / f"voz{n}.mp3")
        synth.append((kind, text, it, voice, words, dur))
    grand = sum(s[5] + 0.35 for s in synth)
    print(f"[ranking] duración total: {grand:.1f}s")

    segs, offset, credits = [], 0.0, []
    for n, (kind, text, it, voice, words, dur) in enumerate(synth):
        k = by_id[int(it["id"])]
        user = k["clip"]["channel"].get("username") or k["clip"]["channel"]["slug"]
        start = float(it.get("start", 0)) if kind == "item" else 0.0
        bg = vertical_bg(cfg, k["file"], start, dur + 0.6, work / f"bg{n}.mp4")
        hcfg = json.loads(json.dumps(cfg))
        if kind == "item":
            hcfg["video"]["tag"] = f"TOP {it.get('rank')}  •  GTA VI"
            hcfg["channel"]["handle"] = f"Clip: {user} (Kick)"
            headline = f"NÚMERO {it.get('rank')}: {user.upper()}"
            credits.append(f"#{it.get('rank')} {user} — https://kick.com/{k['clip']['channel']['slug']}")
        else:
            hcfg["video"]["tag"] = "LO MÁS VIRAL HOY"
            headline = data["headline"].upper()
        head = visuals.header_card(hcfg, headline, work / f"head{n}.png")
        ass = render.build_ass(words, cfg, work / f"sub{n}.ass")
        seg = render.render(cfg, bg, head, voice, ass, dur, work / f"part{n}.mp4", offset=offset,
                            grand_total=grand, tail=0.35)
        offset += dur + 0.35
        segs.append(seg)

    lst = work / "partes.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in segs), encoding="utf-8")
    video = work / "ranking.mp4"
    sh(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
        "-movflags", "+faststart", str(video)])
    print(f"[ranking] listo: {video} ({video.stat().st_size / 1e6:.1f} MB)")

    tags = [t if t.startswith("#") else f"#{t}" for t in data.get("hashtags", [])][:6]
    for must in ("#GTA6", "#Shorts"):
        if must.lower() not in [t.lower() for t in tags]:
            tags.append(must)
    meta = {"title": data["title"][:95], "hashtags": tags, "headline": data["headline"], "description": data["description"]}
    description = "\n".join([data["description"], "", "Clips de esta edición (todo el crédito a sus creadores):",
                             *credits, "", f"Suscríbete a {cfg['channel']['name']} para el ranking de cada día.",
                             "Narración generada con voz sintética. Canal de fans, no afiliado a Rockstar Games.", "",
                             " ".join(tags)])
    caption = work / "publicar.txt"
    caption.write_text(f"TÍTULO:\n{meta['title']}\n\nDESCRIPCIÓN:\n{description}\n", encoding="utf-8")

    result = {"type": "ranking", "key": f"ranking-{stamp[:8]}", "title": meta["title"], "video_title": meta["title"],
              "date": stamp, "youtube": None, "release": None}
    if not a.dry_run:
        if not a.no_youtube:
            try:
                result["youtube"] = publish.upload_youtube(video, meta, description, cfg)
            except Exception as exc:
                print(f"[youtube] ERROR al subir: {exc}")
        if cfg["publish"].get("instagram", {}).get("enabled"):
            try:
                result["instagram"] = publish.upload_instagram(video, f"{meta['title']}\n\n{description}", cfg)
            except Exception as exc:
                print(f"[instagram] ERROR al subir: {exc}")
        result["release"] = github_release(video, caption, meta, f"ranking-{stamp}")
        state = load_state()
        state["items"].append(result)
        save_state(state)
    print(f"\nTerminado: ranking '{meta['title']}' -> {result['youtube'] or result['release'] or video}")


if __name__ == "__main__":
    main()
