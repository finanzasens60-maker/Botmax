"""Modo maratón: saca clips de un directo largo (incluso 24 horas) sin parar.

Graba el directo en bloques (por defecto 60 min). Mientras graba el bloque siguiente, procesa el
anterior y saca sus clips. GitHub deja máximo ~6 h por ejecución: antes de llegar al límite, el bot
lanza solo una nueva ejecución que sigue grabando donde quedó, hasta cubrir las horas pedidas o
hasta que el directo termine.

Si el link NO está en vivo (es un VOD), hace lo mismo que bot.live (una sola pasada).

Uso:
  python -m bot.maraton URL [--hours 24] [--topic "Just Chatting"] [--mine] [--issue 5] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import live

JOB_START = time.time()


def _stream_url(url: str) -> str | None:
    try:
        out = subprocess.run(["yt-dlp", "-g", "-f", "b[height<=720]/best", "--no-warnings", url],
                             capture_output=True, text=True, timeout=90)
        lines = [l for l in out.stdout.strip().splitlines() if l.startswith("http")]
        return lines[0] if lines else None
    except Exception as exc:
        print(f"[maratón] no pude obtener el stream: {exc}")
        return None


def _is_live(url: str) -> bool:
    try:
        return bool(live.get_info(url).get("is_live"))
    except Exception:
        return False


def _dispatch_next(url: str, hours_left: float, topic: str | None, mine: bool, issue: str | None) -> bool:
    """Lanza una nueva ejecución del workflow para seguir grabando."""
    cmd = ["gh", "workflow", "run", "directos.yml", "--ref", "main",
           "-f", f"url={url}", "-f", f"hours={hours_left:.2f}", "-f", f"mine={'true' if mine else 'false'}"]
    if topic:
        cmd += ["-f", f"topic={topic}"]
    if issue:
        cmd += ["-f", f"issue={issue}"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"[maratón] lancé la siguiente ejecución ({hours_left:.1f} h restantes)")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"[maratón] no pude lanzar la siguiente ejecución: {exc.stderr[-300:]}")
        return False


def maraton(url: str, hours: float, topic: str | None, mine: bool, issue: str | None, dry_run: bool) -> dict:
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    lcfg = cfg["live"]
    info = live.get_info(url)
    if not info.get("is_live"):
        print("[maratón] el link no está en vivo: lo proceso como video grabado")
        res = live.run(url, None, None, mine, dry_run, topic, info_override=info)
        return {"clips": res, "continues": False, "chunks": 1}

    chunk_min = float(lcfg.get("chunk_minutes", 60))
    per_chunk = max(2, round(float(lcfg.get("clips_per_hour", 8)) * chunk_min / 60))
    job_limit = JOB_START + float(lcfg.get("job_minutes", 330)) * 60
    total_end = time.time() + hours * 3600
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    work = Path("out") / f"maraton-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    print(f"[maratón] {hours:.1f} h a cubrir · bloques de {chunk_min:.0f} min · ~{per_chunk} clips por bloque")

    all_clips: list[dict] = []
    prev: Path | None = None
    k = 0
    stream_ended = False

    def process(chunk: Path) -> None:
        try:
            res = live.run(url, None, None, mine, dry_run, topic, source_override=chunk,
                           info_override=info, clips=per_chunk)
            all_clips.extend(res)
        except Exception as exc:
            print(f"[maratón] error procesando {chunk.name}: {exc}")
        finally:
            chunk.unlink(missing_ok=True)

    while True:
        now = time.time()
        # dejamos ~30 min al final de la ejecución para procesar el último bloque
        mins = min(chunk_min, (total_end - now) / 60, (job_limit - now) / 60 - 30)
        if mins < 5:
            break
        stream = _stream_url(url)
        if not stream:
            stream_ended = True
            print("[maratón] el directo ya no está disponible")
            break
        dst = work / f"bloque{k:03d}.ts"
        print(f"[maratón] grabando bloque {k + 1} ({mins:.0f} min)…")
        net = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "30"] \
            if stream.startswith("http") else []
        rec = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", *net,
                                "-i", stream, "-t", str(int(mins * 60)), "-c", "copy", str(dst)])
        if prev:
            process(prev)  # procesamos el bloque anterior mientras se graba este
        rec.wait()
        if not dst.exists() or dst.stat().st_size < 2_000_000:
            stream_ended = True
            print("[maratón] el bloque salió vacío: el directo probablemente terminó")
            dst.unlink(missing_ok=True)
            break
        prev, k = dst, k + 1
    if prev:
        process(prev)

    hours_left = (total_end - time.time()) / 3600
    continues = False
    if not dry_run and not stream_ended and hours_left > 0.25 and _is_live(url):
        continues = _dispatch_next(url, hours_left, topic, mine, issue)
    return {"clips": all_clips, "continues": continues, "chunks": k, "hours_left": max(0.0, hours_left)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--topic", default=None)
    ap.add_argument("--mine", action="store_true")
    ap.add_argument("--issue", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    hours = a.hours or float(cfg["live"].get("max_hours", 24))
    r = maraton(a.url, hours, a.topic, a.mine, a.issue, a.dry_run)

    clips = r["clips"]
    lines = [f"**{len(clips)} clip(s)** listos" + (f" de {r['chunks']} bloque(s) grabados." if r.get("chunks") else ".")]
    rels = sorted({c["release"] for c in clips if c.get("release")})
    for c in clips[:40]:
        lines.append(f"- {c['title']}" + (f" — {c['youtube']}" if c.get("youtube") else ""))
    if len(clips) > 40:
        lines.append(f"- … y {len(clips) - 40} más")
    if rels:
        lines.append("\nDescárgalos aquí:\n" + "\n".join(f"- {u}" for u in rels))
    if clips and not a.mine:
        lines.append("\nNo los subí a YouTube porque el directo no es tuyo: revísalos y súbelos solo si "
                     "tienes permiso del streamer.")
    if r.get("continues"):
        lines.append(f"\n⏩ El directo sigue en vivo: ya lancé la siguiente tanda ({r['hours_left']:.1f} h restantes). "
                     "Te iré dejando los clips aquí.")
        Path("out").mkdir(exist_ok=True)
        Path("out/continua").write_text("1")
    Path("out").mkdir(exist_ok=True)
    Path("out/live_summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
