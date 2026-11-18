"""Imágenes del juego para los videos, sin necesidad de tener el juego.

Fuentes (en este orden):
1. Directos EN VIVO de GTA 6 de los streamers de config.yaml (se graban unos segundos, sin su audio:
   la narración es siempre la voz del canal).
2. Capturas oficiales de Rockstar (rockstargames.com/VI), con movimiento suave tipo documental.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0 Safari/537.36")
ROCKSTAR_PAGES = ["https://www.rockstargames.com/VI/", "https://www.rockstargames.com/VI/only-in-leonida",
                  "https://www.rockstargames.com/VI/launch-gallery", "https://www.rockstargames.com/VI/media"]
CACHE = Path("assets/rockstar")
SKIP = re.compile(r"(mobile|meta|logo|shard|icon|button|_fg|favicon|thumb)", re.I)

CREDITS: list[str] = []  # quién aparece en las imágenes del último video


def rockstar_images(max_imgs: int = 60) -> list[Path]:
    """Descarga (una vez) las capturas oficiales y las guarda en assets/rockstar."""
    CACHE.mkdir(parents=True, exist_ok=True)
    have = sorted(CACHE.glob("*.jpg"))
    if len(have) >= 15:
        return have
    found: list[str] = []
    for page in ROCKSTAR_PAGES:
        try:
            html = requests.get(page, headers={"User-Agent": UA}, timeout=30).text
            for m in re.findall(r"/VI/_next/static/media/[A-Za-z0-9_.~\-]+\.jpe?g", html):
                if not SKIP.search(m) and m not in found:
                    found.append(m)
        except Exception as exc:
            print(f"[broll] no pude leer {page}: {exc}")
    random.shuffle(found)
    for path in found[:max_imgs]:
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", path.rsplit("/", 1)[-1])
        dst = CACHE / name
        if dst.exists():
            continue
        try:
            r = requests.get(f"https://www.rockstargames.com{path}?imwidth=1920", headers={"User-Agent": UA}, timeout=30)
            if r.ok and len(r.content) > 40_000:
                dst.write_bytes(r.content)
        except Exception:
            pass
    have = sorted(CACHE.glob("*.jpg"))
    print(f"[broll] {len(have)} capturas oficiales de Rockstar disponibles")
    return have


def image_clip(img: Path, seconds: float, w: int, h: int, fps: int, out: Path) -> Path | None:
    """Convierte una imagen en un clip con paneo lento (efecto documental)."""
    pan = random.choice(["(iw-ow)*t/{d}", "(iw-ow)*(1-t/{d})"]).format(d=f"{seconds:.2f}")
    vf = (f"scale={int(w * 1.25)}:{int(h * 1.25)}:force_original_aspect_ratio=increase,"
          f"crop={w}:{h}:x='{pan}':y='(ih-oh)/2',fps={fps},setsar=1,eq=saturation=1.1")
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", f"{seconds:.2f}", "-i", str(img),
                        "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                        "-pix_fmt", "yuv420p", str(out)], check=True, timeout=120)
        return out
    except Exception as exc:
        print(f"[broll] imagen {img.name} falló: {exc}")
        return None


def _live_gta(url: str, keywords: list[str]) -> dict | None:
    try:
        out = subprocess.run(["yt-dlp", "-J", "--no-warnings", "--no-playlist", url],
                             capture_output=True, text=True, timeout=60)
        info = json.loads(out.stdout)
    except Exception:
        return None
    if not info.get("is_live"):
        return None
    text = " ".join(str(info.get(k) or "") for k in ("title", "description", "categories", "tags")).lower()
    return info if any(k in text for k in keywords) else None


def stream_clips(cfg: dict, work: Path) -> list[Path]:
    """Graba unos segundos (sin audio) de los streamers de la lista que estén jugando GTA 6 ahora."""
    bcfg = cfg.get("broll", {})
    streamers = list(bcfg.get("streamers") or [])
    if not streamers:
        return []
    random.shuffle(streamers)
    secs = int(bcfg.get("stream_seconds", 90))
    keywords = [k.lower() for k in bcfg.get("keywords", ["gta 6", "gta vi", "gta6", "grand theft auto vi"])]
    procs, files = [], []
    for url in streamers:
        if len(procs) >= int(bcfg.get("max_streams", 2)):
            break
        info = _live_gta(url, keywords)
        if not info:
            continue
        try:
            stream = subprocess.run(["yt-dlp", "-g", "-f", "b[height<=720]/best", "--no-warnings", url],
                                    capture_output=True, text=True, timeout=60).stdout.split()[0]
        except Exception:
            continue
        dst = work / f"stream_{len(procs)}.mp4"
        print(f"[broll] grabando {secs}s de {info.get('uploader') or url} jugando GTA 6 (sin su audio)")
        procs.append((subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-i", stream, "-t", str(secs),
                                        "-an", "-c:v", "copy", str(dst)]), dst, info))
    for p, dst, info in procs:
        try:
            p.wait(timeout=secs + 60)
        except subprocess.TimeoutExpired:
            p.kill()
        if dst.exists() and dst.stat().st_size > 500_000:
            files.append(dst)
            site = (info.get("extractor_key") or "").split(":")[0]
            CREDITS.append(f"{info.get('uploader') or info.get('channel')} ({site})".strip())
    return files


def collect(cfg: dict, work: Path, duration: float) -> list[Path]:
    """Devuelve clips de fondo listos (directos + capturas animadas). Vacío si no hay nada."""
    CREDITS.clear()
    v = cfg["video"]
    bcfg = cfg.get("broll", {})
    sources = stream_clips(cfg, work)
    if bcfg.get("rockstar_images", True):
        imgs = rockstar_images()
        if imgs:
            # suficientes imágenes para cubrir el video (con o sin directos)
            need = int(duration / 5) + 2 if not sources else max(3, int(duration / 15))
            random.shuffle(imgs)
            for i, img in enumerate((imgs * 5)[:need]):
                clip = image_clip(img, random.uniform(4.5, 6.5), v["width"], v["height"], v["fps"],
                                  work / f"img_{i:03d}.mp4")
                if clip:
                    sources.append(clip)
            CREDITS.append("Rockstar Games")
    return sources
