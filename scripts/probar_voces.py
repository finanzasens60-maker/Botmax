"""Genera una muestra por cada preset de voz y la "escucha": la transcribe con Whisper y mide qué tan
clara, rápida y expresiva suena. Imprime un ranking para elegir la voz más llamativa.

Uso:  python scripts/probar_voces.py        (crea la carpeta muestras/ y muestras/ranking.md)
En GitHub: Actions -> "Probar voces" -> Run workflow -> descarga el artefacto "muestras-de-voz".
"""
import copy
import re
import subprocess
import sys
import unicodedata
import wave
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import tts  # noqa: E402

TEXTO = ("¿Sabías que nadie se dio cuenta de esto? Rockstar escondió un detalle en el tráiler de GTA seis, "
         "y cambia todo. Mira bien: en Vice City, de noche, los carteles se mueven. Increíble, ¿no? "
         "Cuéntame en los comentarios si tú también lo viste.")


def norm(t: str) -> list[str]:
    t = unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z]+", t)


def word_accuracy(ref: str, hyp: str) -> float:
    r, h = norm(ref), norm(hyp)
    d = np.zeros((len(r) + 1, len(h) + 1), dtype=int)
    d[:, 0] = range(len(r) + 1)
    d[0, :] = range(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (r[i - 1] != h[j - 1]))
    return max(0.0, 1 - d[len(r), len(h)] / max(1, len(r)))


def load_wav(mp3: Path) -> np.ndarray:
    wav = mp3.with_suffix(".wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-ar", "16000", str(wav)], check=True)
    with wave.open(str(wav)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    wav.unlink()
    return x


def prosody(x: np.ndarray, sr: int = 16000) -> dict:
    """Variación de tono (expresividad), pausas y energía."""
    frame, hop = 640, 320  # 40 ms / 20 ms
    f0s, energies = [], []
    for i in range(0, len(x) - frame, hop):
        fr = x[i:i + frame] * np.hanning(frame)
        e = float(np.sqrt(np.mean(fr ** 2)))
        energies.append(e)
        if e < 0.02:
            continue
        ac = np.correlate(fr, fr, "full")[frame - 1:]
        lo, hi = sr // 400, sr // 70  # 70–400 Hz
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] / (ac[0] + 1e-9) > 0.35:
            f0s.append(sr / k)
    energies = np.array(energies)
    f0 = np.array(f0s)
    semis = 12 * np.log2(f0 / np.median(f0)) if len(f0) > 10 else np.array([0.0])
    return {"pitch_var": float(np.std(semis)),             # semitonos: más = más expresiva
            "pausas": float(np.mean(energies < 0.01)),     # fracción de silencio
            "energia_var": float(np.std(energies) / (np.mean(energies) + 1e-9))}


def main() -> None:
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    out_dir = Path("muestras")
    out_dir.mkdir(exist_ok=True)
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("small", device="cpu", compute_type="int8")
    except Exception as exc:
        print(f"(sin Whisper: {exc})")
        model = None

    rows = []
    for name in cfg["voice"]["presets"]:
        c = copy.deepcopy(cfg)
        c["voice"]["preset"] = name
        path, _, dur = tts.synthesize(TEXTO, c, out_dir / f"{name}.mp3")
        acc = 0.0
        heard = ""
        if model:
            segs, _ = model.transcribe(str(path), language="es", beam_size=5)
            heard = " ".join(s.text.strip() for s in segs)
            acc = word_accuracy(TEXTO, heard)
        p = prosody(load_wav(path))
        wps = len(TEXTO.split()) / dur
        # Puntaje "llamativo": que se entienda perfecto, que tenga variación de tono y buen ritmo
        ritmo = 1 - min(1, abs(wps - 3.1) / 1.2)          # ~3.1 palabras/s es el ritmo de los Shorts virales
        score = 50 * acc + 30 * min(1, p["pitch_var"] / 3.5) + 20 * ritmo
        rows.append((score, name, acc, p["pitch_var"], wps, p["pausas"], heard))
        print(f"  -> {name}: {dur:.1f}s")

    rows.sort(reverse=True)
    lines = ["| # | preset | puntaje | se entiende | expresividad (semitonos) | palabras/seg | pausas |",
             "|---|---|---|---|---|---|---|"]
    for i, (score, name, acc, pv, wps, pa, _) in enumerate(rows, 1):
        lines.append(f"| {i} | {name} | {score:.0f} | {acc:.0%} | {pv:.2f} | {wps:.2f} | {pa:.0%} |")
    lines.append("\nLo que Whisper escuchó de cada voz:")
    for _, name, *_rest, heard in rows:
        lines.append(f"- **{name}**: {heard}")
    report = "\n".join(lines)
    (out_dir / "ranking.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"\nRECOMENDADA: {rows[0][1]}")


if __name__ == "__main__":
    main()
