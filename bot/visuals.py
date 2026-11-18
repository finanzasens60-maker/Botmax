"""Fondos del video (clips propios, Pexels gratis o fondo animado) y la tarjeta de titular."""
from __future__ import annotations

import os
import random
import subprocess
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _pexels(cfg: dict, work: Path, n: int = 4) -> list[Path]:
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return []
    queries = random.sample(cfg["video"]["pexels_queries"], k=min(3, len(cfg["video"]["pexels_queries"])))
    files: list[Path] = []
    for q in queries:
        try:
            r = requests.get("https://api.pexels.com/videos/search",
                             headers={"Authorization": key},
                             params={"query": q, "orientation": "portrait" if cfg["video"]["height"] > cfg["video"]["width"] else "landscape", "size": "medium", "per_page": 15,
                                     "page": random.randint(1, 3)}, timeout=20)
            r.raise_for_status()
            vids = r.json().get("videos", [])
            random.shuffle(vids)
            for v in vids[:2]:
                short = min(cfg["video"]["width"], cfg["video"]["height"])
                cands = [f for f in v["video_files"] if f.get("width") and f.get("height") and short * 0.9 <= min(f["width"], f["height"]) <= short * 1.8]
                if not cands:
                    continue
                best = sorted(cands, key=lambda f: f["height"])[0]
                dst = work / f"pexels_{v['id']}.mp4"
                with requests.get(best["link"], stream=True, timeout=60) as d:
                    d.raise_for_status()
                    with open(dst, "wb") as fh:
                        for part in d.iter_content(1 << 20):
                            fh.write(part)
                files.append(dst)
                if len(files) >= n:
                    return files
        except Exception as exc:
            print(f"[visuals] Pexels '{q}' falló: {exc}")
    return files


def _procedural(cfg: dict, duration: float, out: Path) -> Path:
    """Fondo animado con colores de Vice City; nunca falla y no tiene copyright."""
    w, h, fps = cfg["video"]["width"], cfg["video"]["height"], cfg["video"]["fps"]
    palettes = [
        ("0xFF3EA5", "0x7A2CF5", "0x00C2FF", "0xFF8A3D"),
        ("0x00E5FF", "0xFF3EA5", "0x6A1BFF", "0xFFB13D"),
        ("0xFF8A3D", "0xFF3E6C", "0x8A2BE2", "0x2CC4F5"),
    ]
    c = random.choice(palettes)
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i",
          f"gradients=s={w}x{h}:r={fps}:c0={c[0]}:c1={c[1]}:c2={c[2]}:c3={c[3]}:nb_colors=4:speed=0.04:type=linear:d={duration:.2f}",
          "-vf", "noise=alls=6:allf=t,vignette=PI/5", "-t", f"{duration:.2f}",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", str(out)])
    return out


def build_background(cfg: dict, duration: float, work: Path) -> Path:
    w, h, fps = cfg["video"]["width"], cfg["video"]["height"], cfg["video"]["fps"]
    local = [p for p in Path("assets/backgrounds").glob("*") if p.suffix.lower() in VIDEO_EXT]
    if local:
        sources = local
    else:  # sin clips propios: directos de GTA 6 + capturas oficiales de Rockstar, y si no, Pexels
        from . import broll
        sources = broll.collect(cfg, work, duration) or _pexels(cfg, work)
    out = work / "background.mp4"
    if not sources:
        print("[visuals] sin clips: usando fondo animado generado")
        return _procedural(cfg, min(duration, 45.0), out)  # se repite en bucle si el video es más largo

    print(f"[visuals] usando {len(sources)} clip(s) de fondo ({'tus clips' if local else 'directos/Rockstar/Pexels'})")
    random.shuffle(sources)
    segs: list[Path] = []
    total, i = 0.0, 0
    while total < duration and i < max(40, int(duration / 3) + 5):
        src = sources[i % len(sources)]
        seg_len = random.uniform(3.5, 6.0)
        src_dur = _probe_duration(src)
        start = random.uniform(0, max(0.0, src_dur - seg_len)) if src_dur > seg_len else 0.0
        seg = work / f"seg_{i:02d}.mp4"
        try:
            vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},setsar=1,"
                  f"eq=brightness=-0.06:saturation=1.15")
            if local:  # tus clips del juego: conservamos su sonido (si no tiene, ponemos silencio)
                has_a = bool(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                                             "stream=index", "-of", "csv=p=0", str(src)],
                                            capture_output=True, text=True).stdout.strip())
                audio_in = [] if has_a else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                _run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(src), *audio_in, "-t", f"{seg_len:.2f}",
                      "-map", "0:v:0", "-map", "0:a:0" if has_a else "1:a:0", "-vf", vf,
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", str(seg)])
            else:
                _run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{seg_len:.2f}", "-an",
                      "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
                      str(seg)])
            segs.append(seg)
            total += min(seg_len, src_dur or seg_len)
        except subprocess.CalledProcessError as exc:
            print(f"[visuals] clip {src.name} falló: {exc.stderr[-200:]}")
        i += 1
    if not segs:
        return _procedural(cfg, duration, out)
    lst = work / "segs.txt"
    lst.write_text("".join(f"file '{s.resolve()}'\n" for s in segs))
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    return out


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def font_file(cfg: dict) -> str:
    fp = cfg["video"]["font"]
    if Path(fp).exists():
        return fp
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def header_card(cfg: dict, headline: str, out: Path) -> Path:
    """PNG transparente con la etiqueta, el titular y el nombre del canal."""
    w, h = cfg["video"]["width"], cfg["video"]["height"]
    font_path = font_file(cfg)
    accent = _hex(cfg["video"]["accent_color"])
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    tag_font = ImageFont.truetype(font_path, 44)
    tag = cfg["video"].get("tag", "GTA VI  •  NOTICIA")
    tb = d.textbbox((0, 0), tag, font=tag_font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    tx, ty = (w - tw) // 2, 230
    d.rounded_rectangle((tx - 34, ty - 18, tx + tw + 34, ty + th + 30), radius=40, fill=accent + (255,))
    d.text((tx, ty - tb[1] + 6), tag, font=tag_font, fill="white")

    import re as _re
    text = _re.sub(r"\b(GTA|ROCKSTAR|VICE|XBOX|PS)\s+(\S+)", lambda m: m.group(1) + "\u00a0" + m.group(2), headline)
    max_w = w - 160
    size = 92
    while True:
        font = ImageFont.truetype(font_path, size)
        lines, cur = [], ""
        for word in text.split(" "):
            test = f"{cur} {word}".strip()
            if cur and d.textlength(test, font=font) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = test
        lines.append(cur)
        if (len(lines) <= 3 and max(d.textlength(l, font=font) for l in lines) <= max_w) or size <= 52:
            break
        size -= 4
    lines = [l.replace("\u00a0", " ") for l in lines[:3]]
    y = ty + th + 70
    line_h = int(size * 1.18)
    box_top = y - 24
    box_bottom = y + line_h * len(lines) + 12
    d.rounded_rectangle((50, box_top, w - 50, box_bottom), radius=36, fill=(10, 8, 25, 200))
    for line in lines:
        lw = d.textlength(line, font=font)
        d.text(((w - lw) / 2, y), line, font=font, fill="white", stroke_width=3, stroke_fill="black")
        y += line_h

    small = ImageFont.truetype(font_path, 38)
    handle = cfg["channel"].get("handle") or cfg["channel"]["name"]
    hw = d.textlength(handle, font=small)
    d.text(((w - hw) / 2, box_bottom + 26), handle, font=small, fill=(255, 255, 255, 210),
           stroke_width=2, stroke_fill="black")
    img.save(out)
    return out


def _wrap(d: ImageDraw.ImageDraw, text: str, font_path: str, max_w: int, size: int, max_lines: int,
          min_size: int = 40) -> tuple[list[str], ImageFont.FreeTypeFont]:
    while True:
        font = ImageFont.truetype(font_path, size)
        lines, cur = [], ""
        for word in text.split():
            test = f"{cur} {word}".strip()
            if cur and d.textlength(test, font=font) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = test
        lines.append(cur)
        if (len(lines) <= max_lines and max(d.textlength(l, font=font) for l in lines) <= max_w) or size <= min_size:
            return lines[:max_lines], font
        size -= 4


def section_card(cfg: dict, kicker: str, heading: str, out: Path) -> Path:
    """Tarjeta para videos largos (16:9): etiqueta + título de la sección arriba a la izquierda."""
    w, h = cfg["video"]["width"], cfg["video"]["height"]
    fp = font_file(cfg)
    accent = _hex(cfg["video"]["accent_color"])
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    kf = ImageFont.truetype(fp, 34)
    kb = d.textbbox((0, 0), kicker, font=kf)
    d.rounded_rectangle((60, 54, 60 + kb[2] + 48, 54 + kb[3] + 30), radius=30, fill=accent + (255,))
    d.text((84, 62 - kb[1] + 6), kicker, font=kf, fill="white")
    lines, font = _wrap(d, heading.upper(), fp, int(w * 0.62), 60, 2)
    y = 54 + kb[3] + 50
    lh = int(font.size * 1.2)
    bw = int(max(d.textlength(l, font=font) for l in lines)) + 60
    d.rounded_rectangle((60, y - 14, 60 + bw, y + lh * len(lines) + 6), radius=24, fill=(10, 8, 25, 205))
    for line in lines:
        d.text((90, y), line, font=font, fill="white", stroke_width=2, stroke_fill="black")
        y += lh
    sf = ImageFont.truetype(fp, 30)
    handle = cfg["channel"].get("handle") or cfg["channel"]["name"]
    d.text((w - 60 - d.textlength(handle, font=sf), 62), handle, font=sf, fill=(255, 255, 255, 220),
           stroke_width=2, stroke_fill="black")
    img.save(out)
    return out


def thumbnail(cfg: dict, background: Path, text: str, out: Path) -> Path:
    """Miniatura 1280x720 con un fotograma del fondo y texto grande."""
    frame = out.with_name("thumb_bg.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "2", "-i", str(background), "-frames:v", "1",
                    "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720", str(frame)], check=True)
    fp = font_file(cfg)
    accent = _hex(cfg["video"]["accent_color"])
    hl = _hex(cfg["video"]["highlight_color"])
    img = Image.open(frame).convert("RGBA")
    shade = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for x in range(1280):  # degradado oscuro a la izquierda para que el texto se lea
        a = int(220 * max(0.0, 1 - x / 1000))
        sd.line([(x, 0), (x, 720)], fill=(8, 4, 24, a))
    img = Image.alpha_composite(img, shade)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1280, 16), fill=accent + (255,))
    d.rectangle((0, 704, 1280, 720), fill=accent + (255,))
    lines, font = _wrap(d, text.upper(), fp, 820, 128, 3, 60)
    y = (720 - int(font.size * 1.12) * len(lines)) // 2
    for i, line in enumerate(lines):
        color = hl + (255,) if i == 0 else (255, 255, 255, 255)
        d.text((60, y), line, font=font, fill=color, stroke_width=8, stroke_fill="black")
        y += int(font.size * 1.12)
    tag_f = ImageFont.truetype(fp, 40)
    d.rounded_rectangle((60, 40, 60 + d.textlength("GTA VI", font=tag_f) + 44, 104), radius=20, fill=accent + (255,))
    d.text((82, 44), "GTA VI", font=tag_f, fill="white")
    img.convert("RGB").save(out, quality=92)
    return out
