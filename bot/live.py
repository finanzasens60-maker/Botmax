"""Clips desde directos: pega el link de un directo (Twitch, Kick, YouTube…) y saca los mejores momentos.

Flujo: descargar (o grabar si está en vivo) -> transcribir con Whisper -> buscar momentos con más
emoción (volumen + reacciones + IA) -> recortar en vertical con subtítulos -> publicar/enviar.

Uso:
  python -m bot.live URL [--start 1:20:00] [--minutes 90] [--mine] [--dry-run]
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import publish, render, script, visuals
from .main import github_release, load_state, save_state, slugify

REACTIONS = re.compile(
    r"\b(no puede ser|qué|que es eso|mira|miren|wow|guau|jaja\w*|jeje\w*|increíble|brutal|locura|"
    r"dios|madre mía|no manches|qué es esto|corre|cuidado|vamos|oh no|ay|what|omg|bro|hermano)\b",
    re.I)

SYSTEM_LIVE = """Eres editor de un canal de YouTube Shorts en español sobre {topic}.
Te paso fragmentos de la transcripción de un directo (con su id). Elige los mejores para Shorts:
momentos graciosos, sorpresas, reacciones fuertes, descubrimientos o datos interesantes del juego.
Evita fragmentos aburridos, de charla con el chat sin contexto, o que no se entiendan solos.
Para cada elegido puedes recortar el inicio/fin (en segundos relativos al fragmento) para que empiece
con fuerza y termine en un buen punto. Duración final entre 15 y 58 segundos.
No inventes nada que no esté en la transcripción.

Responde SOLO JSON:
{"clips": [{"id": 3, "start": 2.0, "end": 41.5, "headline": "MÁX 6 PALABRAS EN MAYÚSCULAS",
            "title": "título de YouTube (máx 70 caracteres)", "description": "1-2 frases",
            "hashtags": ["{tag}", "..."]}]}"""

TOPIC = "GTA 6"  # se cambia con --topic (ej. "Fortnite", "Minecraft", "Just Chatting")


def topic_tag() -> str:
    return "#" + (re.sub(r"[^0-9A-Za-zÁÉÍÓÚáéíóúÑñ]", "", TOPIC) or "Clips")


def is_gta() -> bool:
    return bool(re.search(r"gta|grand theft", TOPIC, re.I))


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, **kw)


def _secs(t: str | None) -> float:
    if not t:
        return 0.0
    parts = [float(p) for p in str(t).split(":")]
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s


# ------------------------------------------------------------------ descarga
def _hls_info(src: str, title: str = "", uploader: str = "", page: str = "") -> dict:
    """Info mínima de un VOD en HLS (.m3u8): la duración sale de ffprobe."""
    dur = None
    try:
        out = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src],
                 capture_output=True, timeout=120).stdout.strip()
        dur = float(out) if out and out != "N/A" else None
    except Exception as exc:
        print(f"[live] ffprobe no pudo medir el VOD: {exc}")
    who = uploader or os.environ.get("STREAMER") or os.environ.get("TOPIC") or "directo"
    return {"title": title or f"Directo de {who}", "uploader": who, "duration": dur, "is_live": False,
            "webpage_url": page or src, "extractor_key": "Kick" if "kick" in src else "", "_src": src}


def _kick_vod(url: str) -> dict | None:
    """Kick bloquea a veces a yt-dlp: uso su API pública para sacar el .m3u8 del VOD."""
    m = re.search(r"kick\.com/([^/]+)/videos/([0-9a-f-]{36})", url)
    if not m:
        return None
    import requests
    r = requests.get(f"https://kick.com/api/v1/video/{m.group(2)}", timeout=30,
                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0 Safari/537.36",
                              "Accept": "application/json"})
    r.raise_for_status()
    d = r.json()
    ls = d.get("livestream") or {}
    info = _hls_info(d["source"], ls.get("session_title", ""), m.group(1).capitalize(), url)
    if ls.get("duration"):
        info["duration"] = ls["duration"] / 1000
    return info


def get_info(url: str) -> dict:
    if ".m3u8" in url:
        return _hls_info(url)
    try:
        out = sh(["yt-dlp", "-J", "--no-warnings", "--no-playlist", url], capture_output=True)
        return json.loads(out.stdout)
    except subprocess.CalledProcessError as exc:
        print(f"[live] yt-dlp falló: {(exc.stderr or '').strip()[-300:]}")
        info = _kick_vod(url)
        if info:
            print(f"[live] uso el VOD de Kick por su API ({info['duration'] or 0:.0f} s)")
            return info
        raise


def download(url: str, info: dict, start: float, minutes: float, work: Path, live_minutes: float = 45) -> Path:
    dst = work / "source.mp4"
    fmt = "bv*[height<=720]+ba/b[height<=720]/b"
    if info.get("is_live"):
        minutes = live_minutes
        print(f"[live] está EN VIVO: grabando {minutes:.0f} minutos…")
        stream = sh(["yt-dlp", "-g", "-f", "b[height<=720]/best", "--no-warnings", url],
                    capture_output=True).stdout.strip().splitlines()[0]
        sh(["ffmpeg", "-y", "-loglevel", "error", "-i", stream, "-t", str(int(minutes * 60)),
            "-c", "copy", str(work / "source.ts")])
        sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(work / "source.ts"), "-c", "copy", str(dst)])
        return dst
    end = start + minutes * 60
    dur = info.get("duration") or end
    end = min(end, dur)
    print(f"[live] descargando {start / 60:.0f}–{end / 60:.0f} min de '{info.get('title', '')[:60]}'")
    sh(["yt-dlp", "-f", fmt, "--merge-output-format", "mp4", "--no-warnings", "--no-playlist",
        "--download-sections", f"*{int(start)}-{int(end)}", "--force-keyframes-at-cuts",
        "-o", str(dst), info.get("_src", url)])
    return dst


# ------------------------------------------------------------------ análisis
def transcribe(audio: Path, cfg: dict) -> list[dict]:
    from faster_whisper import WhisperModel

    lcfg = cfg["live"]
    print(f"[live] transcribiendo con Whisper '{lcfg['whisper_model']}' (puede tardar)…")
    model = WhisperModel(lcfg["whisper_model"], device="cpu", compute_type="int8")
    # Leemos el WAV nosotros (16 kHz mono) para no depender de la versión de PyAV
    import wave

    import numpy as np

    with wave.open(str(audio), "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = model.transcribe(pcm, language=lcfg.get("language") or None, word_timestamps=True,
                                   vad_filter=True, beam_size=1)
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"word": w.word.strip(), "start": float(w.start), "end": float(w.end)})
    print(f"[live] {len(words)} palabras transcritas")
    return words


def loudness(audio: Path, work: Path) -> list[float]:
    """Volumen (dB RMS) de cada segundo del audio."""
    stats = work / "rms.txt"
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(audio), "-af",
        f"asetnsamples=n=16000,astats=metadata=1:reset=1,"
        f"ametadata=print:key=lavfi.astats.Overall.RMS_level:file={stats.as_posix()}", "-f", "null", "-"])
    vals = []
    for line in stats.read_text().splitlines():
        if "RMS_level=" in line:
            v = line.split("=")[1]
            vals.append(-90.0 if v in ("-inf", "inf", "nan") else float(v))
    return vals


def candidates(words: list[dict], rms: list[float], cfg: dict) -> list[dict]:
    """Ventanas con más emoción: volumen alto + reacciones + mucha conversación."""
    win, step = cfg["live"]["window_seconds"], 10
    total = max(len(rms), int(words[-1]["end"]) + 1 if words else 0)
    if total < 20:
        return []
    mean = statistics.fmean(rms) if rms else -30
    sd = statistics.pstdev(rms) if len(rms) > 1 else 1
    cands = []
    for s in range(0, max(1, total - win), step):
        e = s + win
        ws = [w for w in words if s <= w["start"] < e]
        text = " ".join(w["word"] for w in ws)
        seg_rms = sorted(rms[s:e])[-max(1, win // 4):] if rms else [mean]
        loud = (statistics.fmean(seg_rms) - mean) / (sd or 1)
        react = len(REACTIONS.findall(text))
        density = len(ws) / win
        score = loud * 1.5 + react * 0.8 + min(density, 3) * 0.5 + (1 if "!" in text else 0)
        cands.append({"start": s, "end": e, "score": score, "text": text})
    cands.sort(key=lambda c: -c["score"])
    chosen: list[dict] = []
    for c in cands:
        if all(abs(c["start"] - o["start"]) >= win for o in chosen) and len(c["text"].split()) >= 15:
            chosen.append(c)
        if len(chosen) >= cfg["live"]["candidates"]:
            break
    chosen.sort(key=lambda c: c["start"])
    for i, c in enumerate(chosen):
        c["id"] = i
    return chosen


def pick_with_ai(cands: list[dict], info: dict, cfg: dict) -> list[dict]:
    n = cfg["live"]["clips_per_stream"]
    listing = "\n\n".join(f"[id {c['id']}] ({c['end'] - c['start']}s)\n{c['text'][:900]}" for c in cands)
    user = (f"Directo: {info.get('title', '')}\nCanal: {info.get('uploader', '')}\n"
            f"Elige los {n} mejores.\n\nFRAGMENTOS:\n{listing}")
    scfg = cfg["script"]
    for provider in [p for p in scfg["providers"] if p != "template"]:
        try:
            fn = script._github if provider == "github" else script._gemini
            model = scfg["github_model"] if provider == "github" else scfg["gemini_model"]
            # _parse_json exige script/headline/...: usamos la llamada cruda
            data = _raw_llm(fn, SYSTEM_LIVE.replace("{topic}", TOPIC).replace("{tag}", topic_tag()), user, model)
            out = []
            by_id = {c["id"]: c for c in cands}
            for clip in data.get("clips", [])[:n]:
                c = by_id.get(int(clip["id"]))
                if not c:
                    continue
                s = c["start"] + max(0.0, float(clip.get("start", 0)))
                e = c["start"] + min(float(c["end"] - c["start"]), float(clip.get("end", c["end"] - c["start"])))
                if e - s < 12:
                    s, e = c["start"], c["end"]
                out.append({**clip, "abs_start": s, "abs_end": min(e, s + 58)})
            if out:
                print(f"[live] la IA ('{provider}') eligió {len(out)} momentos")
                return out
        except Exception as exc:
            print(f"[live] IA '{provider}' falló: {exc}")
    print("[live] sin IA: uso los momentos con más volumen/reacciones")
    return [{"abs_start": c["start"], "abs_end": c["end"], "headline": f"MOMENTAZO EN {TOPIC.upper()}"[:40],
             "title": f"Momentazo en {TOPIC} ({info.get('uploader', 'directo')})"[:70],
             "description": "Uno de los mejores momentos del directo.", "hashtags": [topic_tag(), "#Shorts"]}
            for c in sorted(cands, key=lambda c: -c["score"])[:n]]


def _raw_llm(fn, system: str, user: str, model: str) -> dict:
    original = script._parse_json
    script._parse_json = lambda text: json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    try:
        return fn(system, user, model)
    finally:
        script._parse_json = original


# ------------------------------------------------------------------ render
def render_clip(cfg: dict, src: Path, clip: dict, words: list[dict], credit: str, idx: int, work: Path) -> Path:
    v = cfg["video"]
    W, H = v["width"], v["height"]
    s, e = clip["abs_start"], clip["abs_end"]
    dur = e - s
    ccfg = copy.deepcopy(cfg)
    ccfg["video"]["caption_y"] = cfg["live"].get("caption_y", 0.74)
    ccfg["channel"]["handle"] = credit
    ccfg["video"]["tag"] = ("GTA VI" if is_gta() else TOPIC.upper()[:16]) + "  •  DIRECTO"
    clip_words = [{"word": w["word"], "start": w["start"] - s, "end": w["end"] - s}
                  for w in words if s <= w["start"] < e]
    ass = render.build_ass(clip_words, ccfg, work / f"clip{idx}.ass")
    head = visuals.header_card(ccfg, clip.get("headline", TOPIC), work / f"clip{idx}_head.png")
    ass_path = str(ass.resolve()).replace(":", r"\:")
    fonts_dir = str(Path(v["font"]).parent.resolve())
    accent = v["accent_color"].lstrip("#")
    out = work / f"clip{idx}.mp4"
    fc = (
        f"[0:v]split[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:4,eq=brightness=-0.18[bg];"
        f"[b]scale={W}:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2+80[base];"
        f"[base][1:v]overlay=0:0[hd];"
        f"[hd]subtitles='{ass_path}':fontsdir='{fonts_dir}'[sub];"
        f"[sub][2:v]overlay=x='-w+w*t/{dur:.2f}':y=0:shortest=1,format=yuv420p[v];"
        f"[0:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]"
    )
    sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{s:.2f}", "-t", f"{dur:.2f}", "-i", str(src),
        "-loop", "1", "-i", str(head),
        "-f", "lavfi", "-i", f"color=c=0x{accent}:s={W}x14:r={v['fps']}",
        "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}", "-r", str(v["fps"]),
        "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(out)])
    return out


# ------------------------------------------------------------------ principal
def _uploads_last_24h() -> int:
    """Cuántos clips de directos se subieron a YouTube en las últimas 24 h (para no saturar el canal)."""
    limite = datetime.now(timezone.utc).timestamp() - 86400
    n = 0
    for it in load_state()["items"]:
        if it.get("type") == "live" and it.get("youtube"):
            try:
                ts = datetime.strptime(it["date"], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc).timestamp()
            except Exception:
                continue
            n += ts >= limite
    return n


def run(url: str, start: str | None, minutes: float | None, mine: bool, dry_run: bool, topic: str | None = None,
        words_override: list[dict] | None = None, source_override: Path | None = None,
        info_override: dict | None = None, clips: int | None = None) -> list[dict]:
    global TOPIC
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    lcfg = cfg["live"]
    TOPIC = (topic or lcfg.get("default_topic") or "GTA 6").strip()[:40]
    print(f"[live] tema de los clips: {TOPIC}")
    if clips:  # modo maratón: más clips por bloque
        lcfg["clips_per_stream"] = clips
        lcfg["candidates"] = max(int(lcfg["candidates"]), clips * 2)
    minutes = minutes or lcfg["max_minutes"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    info = info_override or get_info(url)
    uploader = info.get("uploader") or info.get("channel") or "directo"
    work = Path("out") / f"{stamp}-directo-{slugify(uploader)}"
    work.mkdir(parents=True, exist_ok=True)

    src = source_override or download(url, info, _secs(start), minutes, work, lcfg.get("live_record_minutes", 45))
    audio = work / "audio.wav"
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(audio)])
    words = words_override if words_override is not None else transcribe(audio, cfg)
    rms = loudness(audio, work)
    cands = candidates(words, rms, cfg)
    if not cands:
        print("[live] no encontré momentos con suficiente conversación/acción.")
        return []
    clips = pick_with_ai(cands, info, cfg)

    site = (info.get("extractor_key") or info.get("extractor") or "").split(":")[0].capitalize()
    credit = f"Directo de {uploader}" + (f" ({site})" if site else "")
    results, files = [], []
    for i, clip in enumerate(clips, 1):
        video = render_clip(cfg, src, clip, words, credit, i, work)
        print(f"[live] clip {i}: {clip['title']} ({clip['abs_end'] - clip['abs_start']:.0f}s)")
        meta = {"title": clip["title"], "description": clip.get("description", ""),
                "hashtags": clip.get("hashtags", [topic_tag(), "#Shorts"]), "headline": clip.get("headline", "")}
        tags = [t if t.startswith("#") else f"#{t}" for t in meta["hashtags"]]
        meta["hashtags"] = tags + [t for t in (topic_tag(), "#Shorts") if t not in tags]
        description = "\n".join([
            meta["description"], "",
            f"🎥 Clip del directo de {uploader}: {info.get('webpage_url', url)}",
            f"Suscríbete a {cfg['channel']['name']} para más {'de GTA 6' if is_gta() else 'clips'}.", "",
            " ".join(meta["hashtags"]),
        ])
        txt = work / f"clip{i}_publicar.txt"
        txt.write_text(f"TÍTULO:\n{meta['title']}\n\nDESCRIPCIÓN:\n{description}\n", encoding="utf-8")
        files += [video, txt]
        res = {"type": "live", "key": f"live-{stamp}-{i}", "title": meta["title"], "video_title": meta["title"],
               "date": stamp, "source": url, "youtube": None, "release": None, "file": str(video)}
        if not dry_run:
            subidos = _uploads_last_24h() + sum(1 for r in results if r.get("youtube"))
            if mine and lcfg.get("auto_upload_if_mine", True) and subidos < int(lcfg.get("max_youtube_uploads_per_day", 10)):
                try:
                    ycfg = cfg if is_gta() else {**cfg, "publish": {**cfg["publish"], "youtube": {
                        **cfg["publish"]["youtube"], "default_tags": [TOPIC, "clips", "stream", "directo", "shorts"]}}}
                    res["youtube"] = publish.upload_youtube(video, meta, description, ycfg)
                except Exception as exc:
                    print(f"[youtube] ERROR: {exc}")
            if cfg["publish"].get("telegram"):
                publish.send_telegram(video, meta, res["youtube"], None)
        results.append(res)

    if not dry_run and cfg["publish"].get("github_release") and files:
        rel = github_release(files[0], files[1], {"title": f"Clips: {info.get('title', uploader)}"[:100]},
                             f"directo-{stamp}", extra=files[2:])
        for r in results:
            r["release"] = rel
    if not dry_run:
        state = load_state()
        state["items"] += results
        save_state(state)
    (work / "resultado.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for p in (work / "source.ts", audio):
        p.unlink(missing_ok=True)
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--start", default=None, help="desde dónde analizar, ej. 1:20:00")
    ap.add_argument("--minutes", type=float, default=None, help="cuántos minutos analizar (o grabar si es en vivo)")
    ap.add_argument("--mine", action="store_true", help="el directo es mío o tengo permiso: subir a YouTube")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--topic", default=None, help="juego o tema del directo (por defecto GTA 6)")
    a = ap.parse_args()
    res = run(a.url, a.start, a.minutes, a.mine, a.dry_run, a.topic)
    summary = Path("out/live_summary.md")
    lines = [f"**{len(res)} clip(s)** listos del directo."]
    for r in res:
        lines.append(f"- {r['title']}" + (f" — {r['youtube']}" if r.get("youtube") else ""))
    if res and res[0].get("release"):
        lines.append(f"\nDescárgalos aquí: {res[0]['release']}")
    if res and not a.mine:
        lines.append("\nNo los subí a YouTube porque el directo no es tuyo: revísalos y súbelos solo si "
                     "tienes permiso del streamer (o comenta `#mio` en un nuevo Issue si es tuyo).")
    summary.write_text("\n".join(lines), encoding="utf-8")
    print(summary.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
