"""Corrige títulos de clips ya hechos y los sube a YouTube (privados).

Para cada clip de los Releases indicados:
  1. lo transcribe (Whisper) para saber qué pasa en el clip,
  2. la IA (Gemini) escribe título, titular en pantalla, descripción y hashtags en español,
  3. cambia el titular quemado en el video (rehace solo la franja de arriba),
  4. lo sube a YouTube como PRIVADO (si la cuota diaria se acaba, sigue en la próxima ejecución).

Uso: python scripts/retitular.py TAG1 TAG2 ... --streamer Westcol --stream-title "..." --url https://kick.com/westcol
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import live, publish, script, visuals  # noqa: E402
from bot.main import github_release, load_state, save_state  # noqa: E402

SYSTEM = """Eres editor de un canal de YouTube Shorts en español latino. Te paso clips ya cortados de un directo
(transcripción de cada uno). Para cada clip escribe metadatos que den MUCHAS ganas de verlo, sin mentir:
- "title": título de YouTube de máximo 65 caracteres, con gancho y curiosidad, que diga lo que realmente pasa.
  Incluye el nombre del streamer cuando ayude. Nada de "Momentazo" ni títulos genéricos. Sin emojis al inicio.
- "headline": titular corto en MAYÚSCULAS para la pantalla (máximo 6 palabras).
- "description": 1-2 frases que expliquen el contexto del clip.
- "hashtags": 3 a 5 hashtags relevantes.
Cada título debe ser distinto. No inventes nada que no esté en la transcripción; si no se entiende bien,
usa un título honesto basado en el tono (risas, gritos, sorpresa) y el tema del directo.
Responde SOLO JSON: {"clips": [{"id": 1, "title": "...", "headline": "...", "description": "...", "hashtags": ["#..."]}]}"""


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, **kw)


def duration(p: Path) -> float:
    out = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
             capture_output=True).stdout
    return float(out.strip())


def ask_ai(clips: list[dict], ctx: str) -> dict[int, dict]:
    listing = "\n\n".join(f"[id {c['id']}] ({c['dur']:.0f}s)\n{c['text'][:900] or '(casi sin habla)'}" for c in clips)
    data = live._raw_llm(script._gemini, SYSTEM, f"{ctx}\n\nCLIPS:\n{listing}", "gemini-2.5-flash")
    return {int(c["id"]): c for c in data.get("clips", []) if "id" in c}


def new_header(cfg: dict, headline: str, credit: str, tag: str, out: Path) -> Path:
    ccfg = copy.deepcopy(cfg)
    ccfg["channel"]["handle"] = credit
    ccfg["video"]["tag"] = tag
    return visuals.header_card(ccfg, headline, out)


def rerender(cfg: dict, src: Path, header: Path, out: Path) -> Path:
    """Rehace la franja superior (fondo borroso + titular nuevo) y deja intacto el resto del clip."""
    v = cfg["video"]
    W, H = v["width"], v["height"]
    fg_h = round(W * 9 / 16 / 2) * 2
    fg_y = (H - fg_h) // 2 + 80
    top = fg_y - 4          # todo lo que está encima del video (etiqueta, titular, crédito)
    dur = duration(src)
    accent = v["accent_color"].lstrip("#")
    fc = (
        f"[0:v]split[o][c];"
        f"[c]crop={W}:{fg_h}:0:{fg_y},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"boxblur=25:4,eq=brightness=-0.18,crop={W}:{top}:0:0[strip];"
        f"[o][strip]overlay=0:0[a];"
        f"[a][1:v]overlay=0:0[b];"
        f"[b][2:v]overlay=x='-w+w*t/{dur:.2f}':y=0:shortest=1,format=yuv420p[v]"
    )
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-loop", "1", "-i", str(header),
        "-f", "lavfi", "-i", f"color=c=0x{accent}:s={W}x14:r={v['fps']}",
        "-filter_complex", fc, "-map", "[v]", "-map", "0:a", "-t", f"{dur:.2f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-c:a", "copy", "-movflags", "+faststart", str(out)])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--streamer", default="Westcol")
    ap.add_argument("--stream-title", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--tag-label", default="WESTCOL  •  DIRECTO")
    ap.add_argument("--no-upload", action="store_true")
    a = ap.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg["publish"]["youtube"]["privacy"] = "private"  # siempre privados
    cfg["publish"]["youtube"]["default_tags"] = [a.streamer, "clips", "directo", "kick", "shorts", "GTA 6"]
    live.TOPIC = a.streamer
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    work = Path("out") / f"retitular-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    state = load_state()
    done = {it["key"]: it for it in state["items"] if it.get("type") == "retitulado"}
    credit = f"Directo de {a.streamer} (Kick)"
    ctx = f"Streamer: {a.streamer}\nDirecto: {a.stream_title}"

    # 1) bajar los clips de los Releases
    clips = []
    for tag in a.tags:
        d = work / tag
        d.mkdir()
        sh(["gh", "release", "download", tag, "-p", "*.mp4", "-D", str(d)])
        for mp4 in sorted(d.glob("*.mp4"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0)):
            key = f"retit-{tag}-{mp4.stem}"
            if key in done and done[key].get("youtube"):
                print(f"[retitular] {key} ya estaba subido: {done[key]['youtube']}")
                continue
            clips.append({"id": len(clips) + 1, "key": key, "file": mp4, "tag": tag})
    print(f"[retitular] {len(clips)} clips por corregir")
    if not clips:
        return

    # 2) transcribir
    from faster_whisper import WhisperModel
    import numpy as np
    import wave
    model = WhisperModel(cfg["live"]["whisper_model"], device="cpu", compute_type="int8")
    for c in clips:
        wav = work / f"{c['id']}.wav"
        sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(c["file"]), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        with wave.open(str(wav), "rb") as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        segs, _ = model.transcribe(pcm, language="es", vad_filter=True, beam_size=1)
        c["text"] = " ".join(s.text.strip() for s in segs)
        c["dur"] = duration(c["file"])
        print(f"[retitular] clip {c['id']}: {c['text'][:90]!r}")

    # 3) títulos con IA (en tandas de 8 para no saturar)
    meta: dict[int, dict] = {}
    for i in range(0, len(clips), 8):
        try:
            meta.update(ask_ai(clips[i:i + 8], ctx))
        except Exception as exc:
            print(f"[retitular] IA falló en la tanda {i // 8 + 1}: {exc}")
    if not meta:
        sys.exit("La IA no respondió: no cambio nada (vuelve a intentarlo en un rato).")

    # 4) rehacer titular + 5) subir privado
    results, files, cuota = [], [], False
    for c in clips:
        m = meta.get(c["id"])
        if not m:
            print(f"[retitular] clip {c['id']} sin título de la IA: lo salto")
            continue
        title = m["title"].strip()[:90]
        tags = [t if t.startswith("#") else f"#{t}" for t in m.get("hashtags", [])][:5]
        tags += [t for t in (f"#{a.streamer}", "#Shorts") if t.lower() not in [x.lower() for x in tags]]
        head = new_header(cfg, m.get("headline", title).upper()[:60], credit, a.tag_label, work / f"h{c['id']}.png")
        out = rerender(cfg, c["file"], head, work / f"{a.streamer.lower()}_{c['id']:02d}.mp4")
        description = "\n".join([
            m.get("description", ""), "",
            f"🎥 Clip del directo de {a.streamer}: {a.url}",
            "Todo el crédito es del creador original.", "",
            " ".join(tags),
        ])
        txt = out.with_suffix(".txt")
        txt.write_text(f"TÍTULO:\n{title}\n\nDESCRIPCIÓN:\n{description}\n", encoding="utf-8")
        files += [out, txt]
        res = {"type": "retitulado", "key": c["key"], "title": title, "date": stamp, "youtube": None}
        if not a.no_upload and not cuota:
            try:
                res["youtube"] = publish.upload_youtube(out, {"title": title, "hashtags": tags}, description, cfg)
            except Exception as exc:
                print(f"[youtube] ERROR: {exc}")
                if "quota" in str(exc).lower() or "uploadLimitExceeded" in str(exc):
                    cuota = True
                    print("[youtube] se acabó la cuota de hoy: el resto se sube en la próxima ejecución")
        print(f"[retitular] {c['id']:02d}. {title}  ->  {res['youtube'] or 'sin subir'}")
        results.append(res)

    # guardar
    if files:
        rel = github_release(files[0], files[1], {"title": f"Clips corregidos: {a.streamer}"},
                             f"retitulados-{stamp}", extra=files[2:])
        for r in results:
            r["release"] = rel
    state = load_state()
    state["items"] = [it for it in state["items"] if it.get("key") not in {r["key"] for r in results}] + results
    save_state(state)
    lines = [f"**{sum(1 for r in results if r['youtube'])} de {len(results)} clips** con título nuevo subidos a YouTube como **privados**."]
    lines += [f"- {r['title']}" + (f" — {r['youtube']}" if r["youtube"] else " (pendiente de subir)") for r in results]
    if cuota:
        lines.append("\nSe acabó la cuota diaria de YouTube; los pendientes se suben en la próxima ejecución.")
    if results and results[0].get("release"):
        lines.append(f"\nVideos corregidos para descargar: {results[0]['release']}")
    Path("out/retitular_summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
