"""Los N mejores clips de un video larguísimo (por ejemplo, el VOD de un directo de 24 horas).

Gasta pocos minutos de GitHub porque NO procesa todo el video:
1. Descarga solo el audio (o la calidad más baja) del VOD.
2. Mide el volumen de cada segundo y elige los momentos más intensos (gritos, risas, euforia).
3. Transcribe solo esos momentos con Whisper.
4. La IA elige los N mejores y les pone título.
5. Descarga en buena calidad SOLO esos tramos y los convierte en Shorts verticales.
"""
from __future__ import annotations

import json
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import live, publish
from .main import github_release, load_state, save_state, slugify


def _sh(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _rms_per_second(src: Path, work: Path) -> list[float]:
    stats = work / "rms.txt"
    _sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-af",
         "aresample=16000,aformat=channel_layouts=mono,asetnsamples=n=16000,astats=metadata=1:reset=1,"
         f"ametadata=print:key=lavfi.astats.Overall.RMS_level:file={stats.as_posix()}", "-f", "null", "-"])
    vals = []
    for line in stats.read_text().splitlines():
        if "RMS_level=" in line:
            v = line.split("=")[1]
            vals.append(-90.0 if v in ("-inf", "inf", "nan") else float(v))
    return vals


def _loud_windows(rms: list[float], win: int, k: int, spacing: int) -> list[tuple[int, float]]:
    if len(rms) < win:
        return [(0, 0.0)]
    mean = statistics.fmean(rms)
    sd = statistics.pstdev(rms) or 1.0
    scored = []
    for s in range(0, len(rms) - win, 10):
        top = sorted(rms[s:s + win])[-max(1, win // 4):]
        scored.append((s, (statistics.fmean(top) - mean) / sd))
    scored.sort(key=lambda x: -x[1])
    chosen: list[tuple[int, float]] = []
    for s, sc in scored:
        if all(abs(s - c) >= spacing for c, _ in chosen):
            chosen.append((s, sc))
        if len(chosen) >= k:
            break
    return sorted(chosen)


def best_of_vod(url: str, info: dict, n: int, topic: str | None, mine: bool, dry_run: bool) -> list[dict]:
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    lcfg = cfg["live"]
    live.TOPIC = (topic or lcfg.get("default_topic") or "GTA 6").strip()[:40]
    win = int(lcfg.get("window_seconds", 45))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    uploader = info.get("uploader") or info.get("channel") or "directo"
    work = Path("out") / f"{stamp}-mejores-{slugify(uploader)}"
    work.mkdir(parents=True, exist_ok=True)
    horas = (info.get("duration") or 0) / 3600
    print(f"[mejores] video de {horas:.1f} h · buscando los {n} mejores momentos (tema: {live.TOPIC})")

    # 1) audio barato (o el video en la calidad más baja si no hay audio separado)
    _sh(["yt-dlp", "-f", "ba/wa/w", "--no-playlist", "--no-warnings", "-o", str(work / "barato.%(ext)s"), info.get("_src", url)])
    cheap = next(p for p in work.glob("barato.*") if p.suffix != ".part")
    print(f"[mejores] descargado ({cheap.stat().st_size / 1e6:.0f} MB)")

    # 2) momentos más intensos por volumen
    rms = _rms_per_second(cheap, work)
    k = max(30, n * 3)
    windows = _loud_windows(rms, win, k, spacing=int(lcfg.get("min_spacing_seconds", 240)))
    print(f"[mejores] {len(windows)} momentos candidatos por volumen")

    # 3) transcribir solo esos momentos
    cands, words_by_id = [], {}
    for i, (s, loud) in enumerate(windows):
        wav = work / f"w{i:03d}.wav"
        _sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(s), "-t", str(win), "-i", str(cheap),
             "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        try:
            ws = live.transcribe(wav, cfg)
        except Exception as exc:
            print(f"[mejores] no se pudo transcribir el momento {i}: {exc}")
            ws = []
        wav.unlink(missing_ok=True)
        text = " ".join(w["word"] for w in ws)
        react = len(live.REACTIONS.findall(text))
        score = loud * 1.5 + react * 0.8 + min(len(ws) / win, 3) * 0.5
        if len(ws) >= 10:
            cands.append({"id": len(cands), "start": s, "end": s + win, "score": score, "text": text[:500]})
            words_by_id[len(cands) - 1] = [{**w, "start": w["start"] + s, "end": w["end"] + s} for w in ws]
    if not cands:
        print("[mejores] no encontré momentos con conversación suficiente")
        return []
    cands = sorted(cands, key=lambda c: -c["score"])[:24]
    words_all = [w for c in cands for w in words_by_id[c["id"]]]

    # 4) la IA elige los N mejores
    lcfg["clips_per_stream"] = n
    clips = live.pick_with_ai(sorted(cands, key=lambda c: c["start"]), info, cfg)

    # 5) descargar en buena calidad solo esos tramos y renderizar
    site = (info.get("extractor_key") or info.get("extractor") or "").split(":")[0].capitalize()
    credit = f"Directo de {uploader}" + (f" ({site})" if site else "")
    results, files = [], []
    for i, clip in enumerate(clips, 1):
        s, e = float(clip["abs_start"]), float(clip["abs_end"])
        seg = work / f"tramo{i}.mp4"
        try:
            _sh(["yt-dlp", "-f", "bv*[height<=720]+ba/b[height<=720]/b", "--merge-output-format", "mp4",
                 "--no-playlist", "--no-warnings", "--download-sections", f"*{int(s)}-{int(e) + 1}",
                 "--force-keyframes-at-cuts", "-o", str(seg), info.get("_src", url)])
        except subprocess.CalledProcessError:
            print(f"[mejores] no pude bajar el tramo {i} en buena calidad; uso la copia barata")
            _sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(s), "-t", str(e - s), "-i", str(cheap),
                 "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(seg)])
        rel = {**clip, "abs_start": 0.0, "abs_end": e - s}
        rel_words = [{"word": w["word"], "start": w["start"] - s, "end": w["end"] - s}
                     for w in words_all if s <= w["start"] < e]
        video = live.render_clip(cfg, seg, rel, rel_words, credit, i, work)
        seg.unlink(missing_ok=True)
        print(f"[mejores] clip {i}/{len(clips)}: {clip['title']} ({e - s:.0f}s, minuto {s / 60:.0f})")

        meta = {"title": clip["title"], "description": clip.get("description", ""),
                "hashtags": clip.get("hashtags", [live.topic_tag(), "#Shorts"]), "headline": clip.get("headline", "")}
        tags = [t if t.startswith("#") else f"#{t}" for t in meta["hashtags"]]
        meta["hashtags"] = tags + [t for t in (live.topic_tag(), "#Shorts") if t not in tags]
        description = "\n".join([
            meta["description"], "",
            f"🎥 Clip del directo de {uploader}: {info.get('webpage_url', url)}",
            f"Suscríbete a {cfg['channel']['name']} para más {'de GTA 6' if live.is_gta() else 'clips'}.", "",
            " ".join(meta["hashtags"]),
        ])
        txt = work / f"clip{i}_publicar.txt"
        txt.write_text(f"TÍTULO:\n{meta['title']}\n\nDESCRIPCIÓN:\n{description}\n\n"
                       f"MOMENTO DEL DIRECTO: {int(s // 3600)}:{int(s % 3600 // 60):02d}:{int(s % 60):02d}\n",
                       encoding="utf-8")
        files += [video, txt]
        res = {"type": "live", "key": f"mejores-{stamp}-{i}", "title": meta["title"], "video_title": meta["title"],
               "date": stamp, "source": url, "youtube": None, "release": None, "file": str(video)}
        if not dry_run:
            subidos = live._uploads_last_24h() + sum(1 for r in results if r.get("youtube"))
            if mine and lcfg.get("auto_upload_if_mine", True) and subidos < int(lcfg.get("max_youtube_uploads_per_day", 10)):
                try:
                    ycfg = cfg if live.is_gta() else {**cfg, "publish": {**cfg["publish"], "youtube": {
                        **cfg["publish"]["youtube"], "default_tags": [live.TOPIC, "clips", "stream", "directo", "shorts"]}}}
                    res["youtube"] = publish.upload_youtube(video, meta, description, ycfg)
                except Exception as exc:
                    print(f"[youtube] ERROR: {exc}")
            if cfg["publish"].get("telegram"):
                publish.send_telegram(video, meta, res["youtube"], None)
        results.append(res)

    if not dry_run and cfg["publish"].get("github_release") and files:
        rel = github_release(files[0], files[1], {"title": f"Mejores clips: {info.get('title', uploader)}"[:100]},
                             f"mejores-{stamp}", extra=files[2:])
        for r in results:
            r["release"] = rel
        state = load_state()
        state["items"] += results
        save_state(state)
    cheap.unlink(missing_ok=True)
    (work / "resultado.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results
