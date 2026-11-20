"""Convierte el guion en voz (edge-tts gratis) y devuelve los tiempos de cada palabra para los subtítulos."""
from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


# Efectos de sonido para la voz (ffmpeg). Hacen que suene a narrador de tráiler / YouTuber gamer.
FX = {
    "ninguno": "",
    # Limpia y cálida, como micrófono de YouTuber: sin eco ni cambios de tono (suena más humano)
    "natural": ("highpass=f=75,equalizer=f=150:t=q:w=1:g=1.5,equalizer=f=3500:t=q:w=1.5:g=2,"
                "acompressor=threshold=-21dB:ratio=2.5:attack=10:release=120:makeup=3,alimiter=limit=0.95"),
    # Voz grave, con cuerpo y un eco corto tipo tráiler de cine
    "trailer": ("highpass=f=60,equalizer=f=110:t=q:w=1:g=5,equalizer=f=350:t=q:w=1.2:g=-3,"
                "equalizer=f=3200:t=q:w=1.5:g=4,acompressor=threshold=-22dB:ratio=4:attack=5:release=90:makeup=5,"
                "aecho=0.85:0.5:45|90:0.10|0.05,alimiter=limit=0.95"),
    # Voz brillante, comprimida y al frente: estilo YouTuber con energía
    "hype": ("highpass=f=80,equalizer=f=180:t=q:w=1:g=2,equalizer=f=4500:t=q:w=1.5:g=5,"
             "acompressor=threshold=-20dB:ratio=6:attack=2:release=50:makeup=6,alimiter=limit=0.95"),
    # Grave y con más eco: para secretos, misterios y easter eggs
    "misterio": ("highpass=f=50,equalizer=f=90:t=q:w=1:g=6,equalizer=f=2500:t=q:w=1.5:g=2,"
                 "acompressor=threshold=-24dB:ratio=4:attack=8:release=120:makeup=5,"
                 "aecho=0.8:0.6:70|140:0.18|0.10,alimiter=limit=0.95"),
    # Tipo radio de la calle / walkie: para noticias de última hora
    "radio": ("highpass=f=300,lowpass=f=4200,equalizer=f=1800:t=q:w=1:g=5,"
              "acompressor=threshold=-20dB:ratio=8:attack=2:release=40:makeup=7,alimiter=limit=0.95"),
}


def resolve_voice(cfg: dict) -> dict:
    """Devuelve {voice, rate, pitch, fx} según el preset elegido en config.yaml."""
    vcfg = cfg["voice"]
    preset = vcfg.get("presets", {}).get(vcfg.get("preset", ""), {})
    return {
        "voice": preset.get("voice", vcfg.get("edge_voice", "es-MX-JorgeNeural")),
        "rate": preset.get("rate", vcfg.get("rate", "+0%")),
        "pitch": preset.get("pitch", vcfg.get("pitch", "+0Hz")),
        "fx": preset.get("fx", vcfg.get("fx", "ninguno")),
    }


def apply_fx(path: Path, fx_name: str) -> None:
    chain = FX.get(fx_name, "")
    if not chain:
        return
    tmp = path.with_name(path.stem + "_fx.mp3")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-af", chain,
                    "-ar", "48000", "-b:a", "192k", str(tmp)], check=True)
    tmp.replace(path)


async def _edge(text: str, voice: str, rate: str, out: Path, pitch: str = "+0Hz") -> list[dict]:
    import edge_tts

    words: list[dict] = []
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    with open(out, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append({"word": chunk["text"], "start": start, "end": start + chunk["duration"] / 1e7})
    return words


def _espeak(text: str, out: Path) -> list[dict]:
    wav = out.with_suffix(".wav")
    subprocess.run(["espeak-ng", "-v", "es-419", "-s", "165", "-w", str(wav), text], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), str(out)], check=True)
    return _estimate(text, _duration(out))


def _estimate(text: str, total: float) -> list[dict]:
    """Reparte el tiempo entre palabras según su longitud (respaldo si no hay tiempos exactos)."""
    tokens = text.split()
    weights = [len(t) + (4 if t[-1:] in ".,;:?!" else 1) for t in tokens]
    scale = total / max(1, sum(weights))
    t, words = 0.0, []
    for tok, w in zip(tokens, weights):
        words.append({"word": tok.strip(".,;:¡!¿?\"'"), "start": t, "end": t + w * scale * 0.85})
        t += w * scale
    return words


def synthesize(text: str, cfg: dict, out: Path) -> tuple[Path, list[dict], float]:
    vcfg = cfg["voice"]
    sel = resolve_voice(cfg)
    words: list[dict] = []
    if vcfg.get("engine", "edge") == "edge":
        try:
            words = asyncio.run(_edge(text, sel["voice"], sel["rate"], out, sel["pitch"]))
            if out.stat().st_size == 0:
                raise RuntimeError("audio vacío")
            if not words:  # algunas versiones no mandan tiempos: los estimamos
                words = _estimate(text, _duration(out))
            print(f"[tts] voz '{sel['voice']}' (velocidad {sel['rate']}, tono {sel['pitch']}, efecto {sel['fx']})")
        except Exception as exc:
            print(f"[tts] edge-tts falló ({exc}); usando espeak-ng")
            words = []
    if not words:
        words = _espeak(text, out)
    max_pause = float(vcfg.get("max_pause", 0))
    if max_pause > 0:
        words = _tighten(out, words, max_pause)
    apply_fx(out, sel["fx"])
    return out, words, _duration(out)


def _tighten(audio: Path, words: list[dict], max_pause: float) -> list[dict]:
    """Acorta las pausas largas entre frases (más ritmo, voz más fluida) y corrige los tiempos de los subtítulos."""
    try:
        log = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(audio), "-af",
                              "silencedetect=noise=-38dB:d=0.12", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=120).stderr
    except Exception:
        return words
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    total = _duration(audio)
    cuts = []  # tramos de silencio que se quitan
    for s0, e0 in zip(starts, ends):
        lead = s0 <= 0.01
        keep = 0.04 if lead else max_pause
        if e0 - s0 > keep + 0.03:
            a = s0 if lead else s0 + keep / 2
            b = e0 - (keep if lead else keep / 2)
            cuts.append((a, b))
    if len(starts) > len(ends):  # silencio al final: déjalo corto
        cuts.append((starts[-1] + 0.15, total))
    cuts = [(a, b) for a, b in cuts if b - a > 0.02]
    if not cuts:
        return words
    keepers, t = [], 0.0
    for a, b in cuts:
        if a > t:
            keepers.append((t, a))
        t = b
    if t < total:
        keepers.append((t, total))
    expr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keepers)
    tmp = audio.with_name(audio.stem + "_tight" + audio.suffix)
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(audio), "-af",
                        f"aselect='{expr}',asetpts=N/SR/TB", str(tmp)], check=True, timeout=120)
        tmp.replace(audio)
    except Exception as exc:
        print(f"[tts] no pude acortar las pausas: {exc}")
        tmp.unlink(missing_ok=True)
        return words

    def shift(x: float) -> float:
        removed = 0.0
        for a, b in cuts:
            if x >= b:
                removed += b - a
            elif x > a:
                return a - removed
        return x - removed

    quitado = sum(b - a for a, b in cuts)
    print(f"[tts] pausas acortadas: {quitado:.1f}s menos de silencio")
    return [{**w, "start": shift(w["start"]), "end": max(shift(w["end"]), shift(w["start"]) + 0.05)} for w in words]
