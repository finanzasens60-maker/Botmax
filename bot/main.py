"""Pipeline completo: noticia -> guion -> voz -> video -> publicación.

Uso:
  python -m bot.main                 # corre todo y publica
  python -m bot.main --dry-run       # genera el video pero no publica nada
  python -m bot.main --sample        # usa una noticia de ejemplo (sin internet para noticias)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import longform, news, publish, render, script, tts, visuals

STATE = Path("data/published.json")


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "video"


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"items": []}


def save_state(state: dict) -> None:
    state["items"] = state["items"][-300:]
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def sample_story() -> news.NewsItem:
    return news.NewsItem(
        title="Rockstar confirma que GTA 6 llega el 19 de noviembre a PS5 y Xbox Series X|S",
        link="https://www.rockstargames.com/VI",
        summary=("Rockstar Games reconfirmó en su tráiler de lanzamiento que Grand Theft Auto VI saldrá el "
                 "jueves 19 de noviembre de 2026 en PlayStation 5 y Xbox Series X|S. La precarga digital "
                 "abrió el 12 de noviembre. La versión de PC no tiene fecha anunciada."),
        source="Rockstar Games",
        published=time.time(),
    )


def github_release(video: Path, caption_file: Path, meta: dict, tag: str, extra: list[Path] | None = None) -> str | None:
    if not os.environ.get("GITHUB_ACTIONS") or not shutil.which("gh"):
        return None
    try:
        out = subprocess.run(
            ["gh", "release", "create", tag, str(video), str(caption_file), *[str(e) for e in (extra or [])],
             "--title", meta["title"][:100], "--notes", caption_file.read_text(encoding="utf-8")[:5000]],
            capture_output=True, text=True, check=True)
        url = out.stdout.strip().splitlines()[-1]
        print(f"[release] {url}")
        return url
    except subprocess.CalledProcessError as exc:
        print(f"[release] falló: {exc.stderr[-300:]}")
        return None


def make_one(cfg: dict, story: news.NewsItem, dry_run: bool, meta_override: dict | None = None) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    work = Path("out") / f"{stamp}-{slugify(story.title)}"
    work.mkdir(parents=True, exist_ok=True)
    print(f"\n=== Noticia: {story.title}\n    Fuente: {story.source}")

    meta = meta_override or script.write_script(story, cfg)
    print(f"[script] título: {meta['title']}")
    voice, words, dur = tts.synthesize(meta["script"], cfg, work / "voice.mp3")
    print(f"[tts] duración de la voz: {dur:.1f}s")
    if dur > 170:
        raise RuntimeError("El guion quedó demasiado largo para un Short (>170 s)")

    bg = visuals.build_background(cfg, dur + 1.0, work)
    from . import broll
    creditos = list(broll.CREDITS)
    streamers = [c for c in creditos if "Rockstar" not in c]
    hcfg = cfg if not streamers else {**cfg, "channel": {**cfg["channel"], "handle": f"Imágenes: {streamers[0]}"}}
    head = visuals.header_card(hcfg, meta["headline"], work / "header.png")
    ass = render.build_ass(words, cfg, work / "captions.ass")
    video = render.render(cfg, bg, head, voice, ass, dur, work / "video.mp4")
    print(f"[render] listo: {video} ({video.stat().st_size / 1e6:.1f} MB)")

    description = publish.build_description(meta, story, cfg)
    if creditos:
        description += "\n\nImágenes: " + ", ".join(creditos)
    caption_file = work / "publicar.txt"
    caption_file.write_text(
        f"TÍTULO:\n{meta['title']}\n\nDESCRIPCIÓN / CAPTION (TikTok, Reels):\n{description}\n\n"
        f"GUION:\n{meta['script']}\n", encoding="utf-8")
    (work / "meta.json").write_text(json.dumps({**meta, "source": story.source, "link": story.link},
                                               ensure_ascii=False, indent=2), encoding="utf-8")

    result = {"key": story.key, "title": story.title, "video_title": meta["title"], "date": stamp,
              "youtube": None, "release": None}
    if not dry_run:
        try:
            result["youtube"] = publish.upload_youtube(video, meta, description, cfg)
        except Exception as exc:
            print(f"[youtube] ERROR al subir: {exc}")
        if cfg["publish"].get("instagram", {}).get("enabled"):
            hechos = sum(1 for it in load_state()["items"] if it.get("instagram") and it.get("date", "")[:8] == stamp[:8])
            if hechos < int(cfg["publish"]["instagram"].get("max_per_day", 6)):
                try:
                    result["instagram"] = publish.upload_instagram(video, f"{meta['title']}\n\n{description}", cfg)
                except Exception as exc:
                    print(f"[instagram] ERROR al subir: {exc}")
        if cfg["publish"].get("github_release"):
            result["release"] = github_release(video, caption_file, meta, f"video-{stamp}")
        if cfg["publish"].get("telegram"):
            publish.send_telegram(video, meta, result["youtube"], result["release"])
    # limpiar temporales pesados
    for p in work.glob("seg_*.mp4"):
        p.unlink()
    for p in work.glob("pexels_*.mp4"):
        p.unlink()
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="genera pero no publica")
    ap.add_argument("--sample", action="store_true", help="usa una noticia de ejemplo")
    ap.add_argument("--count", type=int, default=None)
    ap.add_argument("--long", action="store_true", help="hacer un video largo en vez de Shorts")
    ap.add_argument("--theme", default=None, help="tema del video largo (ver config.yaml)")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    state = load_state()

    if args.long:
        tried = set()
        for _ in range(len(cfg["long"]["themes"])):
            theme = args.theme
            if not theme:
                pending = [t for t in cfg["long"]["themes"] if t["name"] not in tried]
                theme = longform.choose_theme({**cfg, "long": {**cfg["long"], "themes": pending}}, state)["name"]
            tried.add(theme)
            result = longform.make_long(cfg, state, args.dry_run, theme)
            if result:
                if not args.dry_run:
                    state["items"].append(result)
                    save_state(state)
                print("\nTerminado: 1 video largo.")
                return
            if args.theme:
                break
        print("No se pudo hacer un video largo hoy (pocas fuentes).")
        return
    count = args.count or cfg["publish"].get("videos_per_run", 1)

    items = [] if args.sample else news.fetch_items(cfg)
    print(f"[news] {len(items)} titulares relevantes")
    made = 0
    for _ in range(count):
        if args.sample:
            story = sample_story()
        else:
            keys = {i["key"] for i in state["items"]}
            story = news.pick_story(items, keys, [i["title"] for i in state["items"]])
        if not story:
            print("[news] no hay noticias nuevas que no hayamos publicado. Nada que hacer.")
            break
        result = make_one(cfg, story, args.dry_run)
        made += 1
        if not args.dry_run and not args.sample:
            state["items"].append(result)
            save_state(state)
        items = [i for i in items if i.key != story.key]
    print(f"\nTerminado: {made} video(s).")


if __name__ == "__main__":
    main()
