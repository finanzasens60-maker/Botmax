"""Imágenes del juego para los videos, sin necesidad de tener el juego.

Fuentes (en este orden):
1. Directos EN VIVO de GTA 6 de los streamers de config.yaml (se graban unos segundos, sin su audio:
   la narración es siempre la voz del canal).
2. Gameplay de los VODs de Kick de los canales en broll.kick_vods (tramos al azar, SIN audio y con la
   cámara del streamer recortada: solo se ve el juego). Se le da crédito al canal.
3. Capturas oficiales de Rockstar (rockstargames.com/VI), con movimiento suave tipo documental.
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


def _facecam_box(video: Path, samples: int = 14) -> tuple[int, int, int, int, int, int] | None:
    """Busca la cámara del streamer: una cara que aparece SIEMPRE en el mismo sitio (las del juego se mueven)."""
    try:
        import cv2
    except ImportError:
        print("[broll] sin OpenCV: no puedo buscar la cámara del streamer")
        return None
    cap = cv2.VideoCapture(str(video))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if n <= 0 or W <= 0:
        return None
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if det.empty():
        print("[broll] OpenCV sin detector de caras: no puedo buscar la cámara")
        return None
    boxes, seen = [], 0
    for k in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (k + 0.5) / samples))
        ok, frame = cap.read()
        if not ok:
            continue
        seen += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        for (x, y, w, h) in det.detectMultiScale(gray, 1.1, 6, minSize=(max(24, W // 40), max(24, W // 40))):
            boxes.append((x + w / 2, y + h / 2, w, h))
    cap.release()
    if not boxes or not seen:
        return None
    # agrupa por posición: la cámara es la caja que se repite en la mayoría de los cuadros
    best = max(boxes, key=lambda b: sum(abs(b[0] - o[0]) < W * 0.06 and abs(b[1] - o[1]) < H * 0.06 for o in boxes))
    same = [o for o in boxes if abs(best[0] - o[0]) < W * 0.06 and abs(best[1] - o[1]) < H * 0.06]
    if len(same) < max(3, seen * 0.4):
        return None
    cx = sum(o[0] for o in same) / len(same)
    cy = sum(o[1] for o in same) / len(same)
    fw = max(o[2] for o in same)
    # la cámara suele ser un recuadro bastante más grande que la cara: tapamos ~4x la cara (con margen)
    bw, bh = int(fw * 4.0), int(fw * 4.0 * 0.9)
    x0, y0 = max(0, int(cx - bw / 2)), max(0, int(cy - bh / 2.4))
    bw, bh = min(bw, W - x0), min(bh, H - y0)
    return (x0 - x0 % 2, y0 - y0 % 2, bw - bw % 2, bh - bh % 2, W, H)


def _crop_without(box: tuple[int, int, int, int, int, int]) -> tuple[int, int, int, int] | None:
    """El recorte 16:9 más grande que deja la cámara del streamer por fuera (izquierda, derecha, arriba o abajo)."""
    x, y, w, h, W, H = box
    regions = [(0, 0, x, H), (x + w, 0, W - x - w, H), (0, 0, W, y), (0, y + h, W, H - y - h)]
    best = None
    for rx, ry, rw, rh in regions:
        if rw <= 0 or rh <= 0:
            continue
        cw = min(rw, rh * 16 / 9)
        ch = cw * 9 / 16
        if best is None or cw > best[2]:
            cx = rx + (rw - cw) / 2
            cy = ry + (rh - ch) / 2
            best = (int(cx) // 2 * 2, int(cy) // 2 * 2, int(cw) // 2 * 2, int(ch) // 2 * 2)
    # si el recorte deja menos de la mitad del ancho, la imagen quedaría muy pixelada
    return best if best and best[2] >= W * 0.5 else None


def hide_facecam(video: Path) -> Path | None:
    """Quita la cámara del streamer RECORTANDO la imagen (solo queda el juego).
    Si la cámara está en un sitio donde el recorte dejaría muy poca imagen, se descarta ese tramo."""
    box = _facecam_box(video)
    if not box:
        return video
    W, H = box[4], box[5]
    crop = _crop_without(box)
    if not crop:
        print(f"[broll] la cámara está en el centro de {video.name}: descarto este tramo")
        video.unlink(missing_ok=True)
        return None
    cx, cy, cw, ch = crop
    out = video.with_name(video.stem + "_sincara.mp4")
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                        "-vf", f"crop={cw}:{ch}:{cx}:{cy},scale={W}:{H}:flags=lanczos,setsar=1",
                        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", str(out)], check=True, timeout=300)
        print(f"[broll] cámara del streamer recortada en {video.name} (queda {cw}x{ch} desde {cx},{cy})")
        video.unlink(missing_ok=True)
        return out
    except Exception as exc:
        print(f"[broll] no pude recortar la cámara: {exc}; descarto el tramo")
        video.unlink(missing_ok=True)
        return None


def kick_vod_clips(cfg: dict, work: Path) -> list[Path]:
    """Tramos al azar (sin audio) de los VODs de GTA 6 de los canales de Kick en broll.kick_vods."""
    bcfg = cfg.get("broll", {})
    channels = list(bcfg.get("kick_vods") or [])
    if not channels:
        return []
    keywords = [k.lower() for k in bcfg.get("keywords", ["gta 6", "gta vi", "gta6"])]
    secs = int(bcfg.get("vod_seconds", 45))
    per = int(bcfg.get("vod_clips", 3))
    random.shuffle(channels)
    files: list[Path] = []
    for ch in channels[: int(bcfg.get("max_streams", 2))]:
        try:
            r = requests.get(f"https://kick.com/api/v2/channels/{ch}/videos", timeout=30,
                             headers={"User-Agent": UA, "Accept": "application/json"})
            vids = r.json()
            vids = vids if isinstance(vids, list) else vids.get("data", [])
        except Exception as exc:
            print(f"[broll] no pude leer los videos de {ch}: {exc}")
            continue
        def _gta(v: dict) -> bool:
            text = " ".join([v.get("session_title") or ""] + [c.get("name", "") for c in v.get("categories") or []]).lower()
            return any(k in text for k in keywords + ["grand theft auto"])
        vods = [v for v in vids if not v.get("is_live") and v.get("source") and (v.get("duration") or 0) > 1_800_000
                and _gta(v)]
        if not vods:
            continue
        vod = random.choice(vods[:4])
        dur = vod["duration"] / 1000
        got = 0
        for k in range(per * 2):
            if got >= per:
                break
            start = random.uniform(600, max(700, dur - secs - 60))
            dst = work / f"vod_{ch}_{k}.mp4"
            try:
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.0f}", "-i", vod["source"],
                                "-t", str(secs), "-an", "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "veryfast",
                                "-crf", "22", str(dst)], check=True, timeout=240)
            except Exception as exc:
                print(f"[broll] no pude cortar el VOD de {ch}: {exc}")
                continue
            if dst.exists() and dst.stat().st_size > 300_000:
                clean = hide_facecam(dst)
                if clean:
                    files.append(clean)
                    got += 1
        if got:
            print(f"[broll] {got} tramo(s) de gameplay de {ch} ('{vod.get('session_title', '')[:50]}')")
            CREDITS.append(f"{ch} (Kick)")
    return files


def collect(cfg: dict, work: Path, duration: float) -> list[Path]:
    """Devuelve clips de fondo listos (directos + capturas animadas). Vacío si no hay nada."""
    CREDITS.clear()
    v = cfg["video"]
    bcfg = cfg.get("broll", {})
    sources = stream_clips(cfg, work) + kick_vod_clips(cfg, work)
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
