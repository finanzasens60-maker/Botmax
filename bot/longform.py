"""Videos largos (16:9): trucos, secretos, guías y resumen semanal, con capítulos y miniatura."""
from __future__ import annotations

import copy
import json
import random
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import news, publish, render, script, tts, visuals

SYSTEM_LONG = """Eres el guionista de un canal de YouTube en español latino sobre GTA 6 (Grand Theft Auto VI).
Vas a escribir el guion de un VIDEO LARGO (formato horizontal) a partir de varias fuentes.

REGLAS DE ORO (muy importantes):
- Usa SOLO información que aparezca en las FUENTES. Nunca inventes trucos, códigos, ubicaciones, cifras ni fechas.
- Si un truco o código no aparece textualmente en las fuentes, NO lo incluyas.
- Si algo es rumor o viene de Reddit/redes, dilo ("según usuarios de Reddit…").
- Cada sección es un punto concreto (un truco, un secreto, un consejo o una noticia). Explícalo paso a paso y
  di por qué le sirve al espectador. Menciona la fuente de forma natural ("según IGN…").
- Tono: cercano, con energía, sin groserías. Nada de piratería, mods ni trampas online que causen baneos.
- Escribe los números como se dicen ("diecinueve", "cien mil dólares"). Di "GTA seis" (no "GTA 6").
- Intro de 30-45 palabras con gancho fuerte y promesa del video. Outro de 25-40 palabras pidiendo like,
  suscripción y comentario.
- Si las fuentes solo alcanzan para pocas secciones, haz pocas pero buenas. Mínimo 3 secciones.

Responde SOLO con JSON válido:
{"title": "título de YouTube (máx 70 caracteres, llamativo pero honesto)",
 "thumbnail_text": "2 a 5 palabras para la miniatura",
 "intro": "...",
 "sections": [{"heading": "título corto de la sección (máx 7 palabras)", "text": "lo que se dice (90-170 palabras)"}],
 "outro": "...",
 "description": "descripción de 3-4 frases",
 "hashtags": ["#GTA6", "..."]}"""


def _feeds_for(query: str) -> list[str]:
    q = query.replace(" ", "+")
    return [
        f"https://news.google.com/rss/search?q={q}+when:7d&hl=es-419&gl=US&ceid=US:es-419",
        f"https://news.google.com/rss/search?q={q}+when:7d&hl=en-US&gl=US&ceid=US:en",
        f"https://www.bing.com/news/search?q={q}&format=rss",
    ]


def gather_sources(cfg: dict, theme: dict) -> list[news.NewsItem]:
    lcfg = copy.deepcopy(cfg)
    feeds: list[str] = []
    for q in theme["queries"]:
        feeds += _feeds_for(q)
    feeds += theme.get("extra_feeds", [])
    lcfg["news"]["feeds"] = feeds
    lcfg["news"]["max_age_hours"] = theme.get("max_age_days", 7) * 24
    items = news.fetch_items(lcfg)
    seen, uniq = set(), []
    for it in sorted(items, key=lambda x: -x.published):
        if it.key not in seen:
            seen.add(it.key)
            uniq.append(it)
    # Leer el texto de los artículos con más pinta de tener contenido útil
    for it in uniq[:14]:
        if not it.body:
            it.body = news.fetch_article_text(it.link)
    uniq.sort(key=lambda x: (-len(x.body), -x.published))
    return uniq[:10]


def _material(sources: list[news.NewsItem]) -> str:
    out = []
    for i, s in enumerate(sources, 1):
        body = s.body[:1600] if s.body else s.summary[:500]
        out.append(f"[{i}] {s.title}\nFuente: {s.source}\n{body}")
    return "\n\n".join(out)


def write_long_script(cfg: dict, theme: dict, sources: list[news.NewsItem]) -> dict:
    user = (f"Canal: {cfg['channel']['name']}\nTEMA DEL VIDEO: {theme['name']} — {theme['angle']}\n"
            f"Número de secciones deseado: {cfg['long']['sections']}\n\nFUENTES:\n{_material(sources)}")
    scfg = cfg["script"]
    for provider in [p for p in scfg["providers"] if p != "template"]:
        try:
            fn = script._github if provider == "github" else script._gemini
            model = scfg["github_model"] if provider == "github" else scfg["gemini_model"]
            data = fn(SYSTEM_LONG, user, model)
            if len(data.get("sections", [])) < 3:
                raise ValueError("menos de 3 secciones")
            data["provider"] = provider
            print(f"[long] guion con '{provider}': {len(data['sections'])} secciones")
            return data
        except Exception as exc:
            print(f"[long] '{provider}' falló: {exc}")
    raise RuntimeError("No se pudo escribir el guion largo (se necesita IA: GitHub Models o Gemini)")


def _fmt(t: float) -> str:
    t = int(t)
    return f"{t // 60}:{t % 60:02d}" if t < 3600 else f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def choose_theme(cfg: dict, state: dict) -> dict:
    """Rota los temas: elige el que hace más tiempo no se usa."""
    used = [i.get("theme") for i in state["items"] if i.get("type") == "long"]
    last = {th["name"]: max([k for k, u in enumerate(used) if u == th["name"]], default=-1)
            for th in cfg["long"]["themes"]}
    return min(cfg["long"]["themes"], key=lambda th: last[th["name"]])


def make_long(cfg: dict, state: dict, dry_run: bool, theme_name: str | None = None,
              data_override: dict | None = None) -> dict | None:
    theme = next((t for t in cfg["long"]["themes"] if t["name"] == theme_name), None) or choose_theme(cfg, state)
    print(f"\n=== Video largo: {theme['name']}")
    if data_override:
        sources, data = [], data_override
    else:
        sources = gather_sources(cfg, theme)
        print(f"[long] {len(sources)} fuentes ({sum(1 for s in sources if s.body)} con texto completo)")
        if len(sources) < cfg["long"].get("min_sources", 3):
            print("[long] muy pocas fuentes para este tema; se intentará otro la próxima vez")
            return None
        data = write_long_script(cfg, theme, sources)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    work = Path("out") / f"{stamp}-largo-{theme['name'].lower().replace(' ', '-')}"
    work.mkdir(parents=True, exist_ok=True)

    lcfg = copy.deepcopy(cfg)
    lcfg["video"].update(cfg["long"]["video"])
    if theme.get("voice_preset"):
        lcfg["voice"]["preset"] = theme["voice_preset"]
    music = [p for p in Path("assets/music").glob("*") if p.suffix.lower() in {".mp3", ".wav", ".m4a", ".ogg"}]
    if music:
        lcfg["video"]["music_file"] = str(random.choice(music))

    parts = [("intro", data["title"], data["intro"])]
    parts += [(f"sec{i}", s["heading"], s["text"]) for i, s in enumerate(data["sections"], 1)]
    parts += [("outro", "¡Nos vemos!", data["outro"])]

    # 1) voces y duraciones
    voiced = []
    for pid, heading, text in parts:
        audio, words, dur = tts.synthesize(text, lcfg, work / f"{pid}.mp3")
        voiced.append((pid, heading, audio, words, dur))
    tail = 0.6
    grand = sum(v[4] + tail for v in voiced)
    print(f"[long] duración total: {_fmt(grand)}")

    # 2) fondo único y render de cada parte
    bg = visuals.build_background(lcfg, grand + 2, work)
    bg_dur = visuals._probe_duration(bg)
    seg_files, chapters, t = [], [], 0.0
    n_sec = len(data["sections"])
    for idx, (pid, heading, audio, words, dur) in enumerate(voiced):
        kicker = "GTA VI" if pid in ("intro", "outro") else f"{theme['kicker']}  {idx}/{n_sec}"
        card = visuals.section_card(lcfg, kicker, heading, work / f"{pid}_card.png")
        ass = render.build_ass(words, lcfg, work / f"{pid}.ass")
        # cada parte usa un tramo distinto del fondo
        part_bg = work / f"{pid}_bg.mp4"
        start = t % max(1.0, bg_dur - 1)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-ss", f"{start:.2f}", "-i", str(bg),
                        "-t", f"{dur + tail + 0.5:.2f}", "-c", "copy", str(part_bg)], check=True)
        seg = render.render(lcfg, part_bg, card, audio, ass, dur, work / f"{pid}.mp4",
                            offset=t, grand_total=grand, tail=tail)
        label = "Intro" if pid == "intro" else ("Despedida" if pid == "outro" else heading)
        chapters.append(f"{_fmt(t)} {label}")
        t += dur + tail
        seg_files.append(seg)
        print(f"[long] parte '{label}' lista")

    lst = work / "parts.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in seg_files))
    video = work / "video.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", "-movflags", "+faststart", str(video)], check=True)
    thumb = visuals.thumbnail(lcfg, bg, data["thumbnail_text"], work / "thumbnail.jpg")
    print(f"[long] listo: {video} ({video.stat().st_size / 1e6:.1f} MB)")

    src_lines = [f"- {s.source}: {s.title}" for s in sources[:8]]
    description = "\n".join([
        data["description"], "", "CAPÍTULOS", *chapters, "",
        *(["FUENTES", *src_lines, ""] if src_lines else []),
        f"Suscríbete a {cfg['channel']['name']} para más de GTA 6.",
        "Narración con voz sintética. Canal de fans, no afiliado a Rockstar Games ni Take-Two.", "",
        " ".join(data.get("hashtags", ["#GTA6"])),
    ])
    meta = {"title": data["title"], "description": data["description"],
            "hashtags": data.get("hashtags", ["#GTA6"]), "headline": data["thumbnail_text"]}
    (work / "publicar.txt").write_text(f"TÍTULO:\n{data['title']}\n\nDESCRIPCIÓN:\n{description}\n", encoding="utf-8")
    (work / "guion.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for p in list(work.glob("*_bg.mp4")) + list(work.glob("sec*.mp4")) + list(work.glob("intro.mp4")) + \
            list(work.glob("outro.mp4")) + list(work.glob("seg_*.mp4")) + list(work.glob("pexels_*.mp4")):
        p.unlink(missing_ok=True)

    result = {"type": "long", "theme": theme["name"], "key": f"long-{stamp}", "title": data["title"],
              "video_title": data["title"], "date": stamp, "youtube": None, "release": None}
    if not dry_run:
        try:
            result["youtube"] = publish.upload_youtube(video, meta, description, cfg, short=False, thumbnail=thumb)
        except Exception as exc:
            print(f"[youtube] ERROR al subir: {exc}")
        if cfg["publish"].get("github_release"):
            from .main import github_release
            result["release"] = github_release(video, work / "publicar.txt", meta, f"largo-{stamp}", extra=[thumb])
        if cfg["publish"].get("telegram"):
            publish.send_telegram_text(f"🎬 Video largo listo: {data['title']}", thumb,
                                       result["youtube"] or result["release"])
    return result
