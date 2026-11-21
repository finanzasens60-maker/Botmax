"""RESUMEN para YouTube: de un directo (link) o una grabación larga, videos HORIZONTALES de ~10 minutos
con los mejores momentos, listos para subir (título, descripción con capítulos y miniatura).

Cómo trabaja (gastando pocos minutos de GitHub):
1. Baja solo el audio (o la calidad más baja) y mide el volumen de cada segundo.
2. Toma los tramos más intensos, los transcribe y la IA elige los mejores hasta llenar N videos de ~10 min.
3. Baja en buena calidad SOLO esos tramos, les pone un rótulo con lo que pasa y los une en orden.
4. Cada parte sale con título, descripción con capítulos (minuto a minuto) y miniatura.
   Si el directo es tuyo (#mio) se sube a YouTube como PRIVADO; si no, queda para descargar.

Uso: python -m bot.resumen URL [--parts 2] [--minutes 10] [--topic "GTA 6"] [--mine] [--dry-run]
Sirve con links de Kick, Twitch, YouTube y también con grabaciones en Google Drive / Dropbox (link público).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

from . import live, publish, script, visuals
from .main import github_release, load_state, save_state, slugify
from .mejores import _loud_windows, _rms_per_second

W, H, FPS = 1920, 1080, 30

SYSTEM = """Eres editor de un canal de YouTube en español latino. Armas videos de ~{minutes} minutos con los MEJORES
momentos de un directo. Te paso momentos candidatos (id, minuto del directo, duración y transcripción).
- Elige los mejores para llenar {parts} video(s) de unos {minutes} minutos cada uno (en total ~{total} segundos).
  Prioriza: risas, sorpresas, peleas sanas, momentos épicos, historias que se entienden solas. Descarta charla
  aburrida, silencios y temas sexuales, insultos discriminatorios o violencia real.
- Para cada elegido: "start" y "end" en segundos RELATIVOS al momento (entre 20 y 90 s de duración), que empiece
  con fuerza y termine cuando se cierra la idea; y "label": rótulo corto (máx. 7 palabras) de lo que pasa.
- Para cada video (parte): "title" (máx 70 caracteres, con gancho y honesto), "thumbnail_text" (2 a 4 palabras),
  "description" (2-3 frases) y "hashtags" (3 a 6).
No inventes nada que no esté en la transcripción. En directos con varias personas no atribuyas todo al streamer.
Responde SOLO JSON:
{{"moments": [{{"id": 3, "start": 5.0, "end": 70.0, "label": "..."}}],
 "parts": [{{"title": "...", "thumbnail_text": "...", "description": "...", "hashtags": ["#..."]}}]}}"""


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, **kw)


def dur_of(p: Path) -> float:
    out = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
             capture_output=True).stdout.strip()
    return float(out or 0)


def _parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0) if m else text)
    if not data.get("moments") or not data.get("parts"):
        raise ValueError("respuesta incompleta")
    return data


def label_png(cfg: dict, text: str, out: Path) -> Path:
    """Rótulo abajo a la izquierda (estilo noticiero) con lo que pasa en el momento."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(visuals.font_file(cfg), 54)
    accent = visuals._hex(cfg["video"]["accent_color"])
    text = text.upper()
    tw = d.textlength(text, font=font)
    x0, y0 = 70, H - 210
    d.rounded_rectangle((x0, y0, x0 + tw + 70, y0 + 96), radius=22, fill=(10, 8, 25, 225))
    d.rectangle((x0, y0, x0 + 14, y0 + 96), fill=accent + (255,))
    d.text((x0 + 40, y0 + 16), text, font=font, fill="white")
    img.save(out)
    return out


def title_card(cfg: dict, still: Path, title: str, out: Path) -> Path:
    img = Image.open(still).convert("RGB").resize((W, H))
    from PIL import ImageFilter
    img = img.filter(ImageFilter.GaussianBlur(18)).point(lambda p: int(p * 0.45))
    d = ImageDraw.Draw(img)
    fp = visuals.font_file(cfg)
    size, lines = 110, [title]
    while size > 60:
        font = ImageFont.truetype(fp, size)
        words, lines, cur = title.split(), [], ""
        for w in words:
            t = f"{cur} {w}".strip()
            if cur and d.textlength(t, font=font) > W - 240:
                lines.append(cur)
                cur = w
            else:
                cur = t
        lines.append(cur)
        if len(lines) <= 3:
            break
        size -= 8
    font = ImageFont.truetype(fp, size)
    y = H / 2 - len(lines) * size * 0.6
    for ln in lines:
        d.text(((W - d.textlength(ln, font=font)) / 2, y), ln, font=font, fill="white", stroke_width=4,
               stroke_fill="black")
        y += size * 1.2
    small = ImageFont.truetype(fp, 44)
    who = cfg["channel"]["name"]
    d.text(((W - d.textlength(who, font=small)) / 2, H - 140), who, font=small,
           fill=visuals._hex(cfg["video"]["accent_color"]))
    img.save(out)
    return out


def render_moment(cfg: dict, src: Path, label: Path, out: Path) -> Path:
    d = dur_of(src)
    fc = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
          f"fps={FPS},setpts=PTS-STARTPTS[v0];"
          f"[v0][1:v]overlay=0:0:enable='between(t,0.3,5.5)',fade=t=in:st=0:d=0.35,"
          f"fade=t=out:st={max(0.0, d - 0.4):.2f}:d=0.35,format=yuv420p[v];"
          f"[0:a]asetpts=PTS-STARTPTS,aresample=48000,afade=t=in:st=0:d=0.3,"
          f"afade=t=out:st={max(0.0, d - 0.4):.2f}:d=0.35,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-loop", "1", "-i", str(label),
        "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{d:.2f}", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)])
    return out


def render_card(card: Path, seconds: float, out: Path) -> Path:
    sh(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(card), "-f", "lavfi", "-i",
        "anullsrc=r=48000:cl=stereo", "-t", f"{seconds:.2f}", "-vf", f"fps={FPS},format=yuv420p,fade=t=out:st={seconds - 0.4:.2f}:d=0.4",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)])
    return out


def ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--parts", type=int, default=1)
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--topic", default=None)
    ap.add_argument("--mine", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    live.TOPIC = (a.topic or cfg["live"].get("default_topic") or "GTA 6").strip()[:40]
    parts = max(1, min(a.parts, 6))
    total = int(parts * a.minutes * 60)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    info = live.get_info(a.url)
    if info.get("is_live"):
        raise SystemExit("Ese directo sigue EN VIVO: pega el link del video cuando termine (o usa los clips de directos).")
    uploader = info.get("uploader") or info.get("channel") or "directo"
    src_url = info.get("_src", a.url)
    work = Path("out") / f"{stamp}-resumen-{slugify(uploader)}"
    work.mkdir(parents=True, exist_ok=True)
    print(f"[resumen] '{info.get('title', '')[:70]}' de {uploader} · {(info.get('duration') or 0) / 3600:.1f} h"
          f" · {parts} video(s) de ~{a.minutes:.0f} min")

    # 1) audio barato + volumen por segundo
    sh(["yt-dlp", "-f", "ba/wa/w", "--no-playlist", "--no-warnings", "-o", str(work / "barato.%(ext)s"), src_url])
    cheap = next(p for p in work.glob("barato.*") if p.suffix != ".part")
    rms = _rms_per_second(cheap, work)
    largo = len(rms)
    if largo < total * 1.3:
        raise SystemExit(f"El video dura {largo / 60:.0f} min: es muy corto para {parts} resumen(es) de {a.minutes:.0f} min.")
    win = 75
    k = min(int(total / 45) * 2 + 10, max(12, largo // 120))
    windows = _loud_windows(rms, win, k, spacing=int(win * 1.4))
    print(f"[resumen] {len(windows)} momentos candidatos por volumen")

    # 2) transcribir solo esos momentos
    cands = []
    for s, loud in windows:
        wav = work / f"w{s}.wav"
        sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(s), "-t", str(win), "-i", str(cheap),
            "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        try:
            ws = live.transcribe(wav, cfg)
        except Exception:
            ws = []
        wav.unlink(missing_ok=True)
        text = " ".join(w["word"] for w in ws)
        if len(ws) >= 12:
            cands.append({"id": len(cands), "start": s, "loud": loud, "text": text[:600]})
    if len(cands) < 3:
        raise SystemExit("No encontré suficientes momentos con conversación para armar el resumen.")
    listing = "\n\n".join(f"[id {c['id']}] minuto {c['start'] / 60:.0f} · {win}s · intensidad {c['loud']:.1f}\n{c['text']}"
                          for c in cands)
    system = SYSTEM.format(minutes=int(a.minutes), parts=parts, total=total)
    data = script._gemini(system, f"Directo: {info.get('title', '')}\nCanal: {uploader}\n\nMOMENTOS:\n{listing}",
                          cfg["script"].get("gemini_model", "gemini-2.5-flash"), parse=_parse)
    by_id = {c["id"]: c for c in cands}
    moments = []
    for m in data["moments"]:
        c = by_id.get(int(m.get("id", -1)))
        if not c:
            continue
        s = c["start"] + max(0.0, float(m.get("start", 0)))
        e = c["start"] + min(float(win), float(m.get("end", win)))
        if e - s < 15:
            s, e = c["start"], c["start"] + win
        moments.append({"abs_start": s, "abs_end": e, "label": m.get("label", "")[:60]})
    moments.sort(key=lambda m: m["abs_start"])
    print(f"[resumen] la IA eligió {len(moments)} momentos ({sum(m['abs_end'] - m['abs_start'] for m in moments) / 60:.1f} min)")

    # 3) repartir en partes de ~N minutos, en orden
    groups, cur, acc = [], [], 0.0
    target = a.minutes * 60
    for m in moments:
        cur.append(m)
        acc += m["abs_end"] - m["abs_start"]
        if acc >= target * 0.95 and len(groups) < parts - 1:
            groups.append(cur)
            cur, acc = [], 0.0
    if cur:
        groups.append(cur)
    # la última parte no debe pasarse mucho de la duración pedida
    while len(groups[-1]) > 1 and sum(m["abs_end"] - m["abs_start"] for m in groups[-1]) > target * 1.25:
        groups[-1].pop()

    results = []
    site = (info.get("extractor_key") or "").split(":")[0].capitalize()
    for p, group in enumerate(groups, 1):
        pmeta = data["parts"][min(p - 1, len(data["parts"]) - 1)]
        title = pmeta["title"][:95] + (f" (Parte {p})" if len(groups) > 1 and "parte" not in pmeta["title"].lower() else "")
        segs, chapters, t = [], [], 4.0
        for i, m in enumerate(group, 1):
            raw = work / f"p{p}_m{i}_raw.mp4"
            s, e = m["abs_start"], m["abs_end"]
            try:
                sh(["yt-dlp", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4",
                    "--no-playlist", "--no-warnings", "--download-sections", f"*{max(0, int(s) - 1)}-{int(e) + 1}",
                    "--force-keyframes-at-cuts", "-o", str(raw), src_url])
            except subprocess.CalledProcessError:
                print(f"[resumen] no pude bajar el momento {i} en buena calidad; uso la copia barata")
                sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(s), "-t", str(e - s), "-i", str(cheap),
                    "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(raw)])
            lab = label_png(cfg, m["label"] or f"Momento {i}", work / f"p{p}_l{i}.png")
            seg = render_moment(cfg, raw, lab, work / f"p{p}_m{i}.mp4")
            raw.unlink(missing_ok=True)
            chapters.append(f"{ts(t)} {m['label'] or f'Momento {i}'}")
            t += dur_of(seg)
            segs.append(seg)
            print(f"[resumen] parte {p}: momento {i}/{len(group)} listo ({m['label']})")
        still = work / f"p{p}_still.png"
        sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", "2", "-i", str(segs[0]), "-frames:v", "1", str(still)])
        intro = render_card(title_card(cfg, still, pmeta["title"], work / f"p{p}_card.png"), 4.0,
                            work / f"p{p}_intro.mp4")
        lst = work / f"p{p}.txt"
        lst.write_text("".join(f"file '{x.resolve()}'\n" for x in [intro, *segs]), encoding="utf-8")
        video = work / f"resumen_parte{p}.mp4"
        sh(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
            "-movflags", "+faststart", str(video)])
        for x in segs + [intro]:
            x.unlink(missing_ok=True)
        thumb = visuals.thumbnail(cfg, video, pmeta.get("thumbnail_text", "MEJORES MOMENTOS"), work / f"p{p}_thumb.jpg")
        tags = [x if x.startswith("#") else f"#{x}" for x in pmeta.get("hashtags", [])][:6]
        description = "\n".join([
            pmeta.get("description", ""), "",
            "Capítulos:", "00:00 Intro", *chapters, "",
            f"🎥 Directo original de {uploader}" + (f" ({site})" if site else "") + f": {info.get('webpage_url', a.url)}",
            f"Suscríbete a {cfg['channel']['name']} para más.", "", " ".join(tags)])
        caption = work / f"resumen_parte{p}.txt"
        caption.write_text(f"TÍTULO:\n{title}\n\nDESCRIPCIÓN:\n{description}\n", encoding="utf-8")
        print(f"[resumen] parte {p} lista: {dur_of(video) / 60:.1f} min · {video.stat().st_size / 1e6:.0f} MB")
        res = {"type": "resumen", "key": f"resumen-{stamp}-{p}", "title": title, "video_title": title, "date": stamp,
               "source": a.url, "youtube": None, "release": None}
        if not a.dry_run:
            if a.mine:
                ycfg = json.loads(json.dumps(cfg))
                ycfg["publish"]["youtube"]["privacy"] = "private"
                try:
                    res["youtube"] = publish.upload_youtube(video, {"title": title, "hashtags": tags}, description, ycfg,
                                                            short=False, thumbnail=thumb)
                except Exception as exc:
                    print(f"[youtube] ERROR: {exc}")
            res["release"] = github_release(video, caption, {"title": title}, f"resumen-{stamp}-p{p}", extra=[thumb])
        results.append(res)

    if not a.dry_run:
        state = load_state()
        state["items"] += results
        save_state(state)
    lines = [f"**{len(results)} video(s) de resumen** de ~{a.minutes:.0f} min listos:", ""]
    for r in results:
        lines.append(f"- **{r['title']}**" + (f" — YouTube (privado): {r['youtube']}" if r["youtube"] else "")
                     + (f" — descargar: {r['release']}" if r["release"] else ""))
    lines += ["", "Cada uno trae título, descripción con capítulos y miniatura (en el link de descarga)."]
    if not a.mine:
        lines.append("No se subieron a YouTube porque no marcaste el directo como tuyo (#mio).")
    Path("out").mkdir(exist_ok=True)
    Path("out/live_summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
