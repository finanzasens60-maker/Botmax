"""Shorts de TRUCOS y CURIOSIDADES de GTA 6 (no noticias).

Junta guías, listas de secretos y trucos publicados en la web, la IA escribe varios guiones cortos
(UN truco o curiosidad concreta por Short, sin repetir los ya publicados) y cada uno se arma como Short
con la voz del canal y gameplay de fondo.

Uso: python -m bot.tips --count 1            (publica en YouTube, como los Shorts normales)
     python -m bot.tips --count 6 --no-youtube   (tanda solo para descargar y subir a Reels/TikTok)
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from pathlib import Path

import yaml

from . import longform, news, script
from .main import load_state, make_one, save_state

SYSTEM_TIPS = """Eres el guionista de un canal de YouTube Shorts en español latino sobre GTA 6 (Grand Theft Auto VI).
Te paso artículos y guías reales. Escribe guiones de Shorts de TRUCOS, SECRETOS y CURIOSIDADES:
- Cada Short trata UNA sola cosa concreta y útil o sorprendente (un truco, un secreto, un easter egg,
  una mecánica poco conocida, un dato curioso del mapa o de los personajes).
- Usa SOLO lo que dicen las fuentes. No inventes códigos, ubicaciones ni cifras.
- Si algo es rumor o viene de usuarios, dilo ("según jugadores en Reddit…").
- Formato: la mitad de los Shorts tipo LISTA ("cinco cosas realistas de GTA seis", "tres trucos para ganar
  dinero rápido"), numerando hablado y seco: "Uno. …", "Dos. …", "Y cinco. …", con una mini-escena o ejemplo
  concreto en cada punto. La otra mitad, UN solo truco o curiosidad contado a fondo.
- Listas: 120 a 160 palabras. Truco único: 70 a 110 palabras. Gancho fuerte en la primera frase (máx. 10
  palabras) y cierre con pregunta ("¿cuál te gustó más?") o invitación a guardar el video.
- Que suene SEGUIDO, como un creador contándolo de corrido: frases cortas unidas con conectores naturales
  ("y ojo, porque…", "además", "encima", "lo mejor es que…"), segunda persona, nada de frases sueltas tipo lista.
- Escribe los números como se dicen y di "GTA seis".
- No repitas temas de la lista "YA PUBLICADOS". Cada Short debe ser distinto de los demás.
Responde SOLO JSON:
{"items": [{"script": "...", "headline": "MÁX 6 PALABRAS EN MAYÚSCULAS", "title": "título YouTube (máx 70 caracteres)",
"description": "2 frases", "hashtags": ["#GTA6", "..."], "fuente": 1}]}"""

TIP_THEMES = ("Trucos", "Secretos", "Consejos")


def _parse_items(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0) if m else text)
    items = [i for i in data.get("items", []) if str(i.get("script", "")).strip() and str(i.get("title", "")).strip()]
    if not items:
        raise ValueError("la IA no devolvió guiones")
    return {"items": items}


def write_tips(cfg: dict, n: int, used: list[str]) -> tuple[list[dict], list[news.NewsItem]]:
    themes = [t for t in cfg["long"]["themes"] if t["name"] in TIP_THEMES]
    random.shuffle(themes)
    sources: list[news.NewsItem] = []
    for th in themes:
        sources += longform.gather_sources(cfg, th)
        if len(sources) >= 12:
            break
    seen, uniq = set(), []
    for s in sources:
        if s.key not in seen:
            seen.add(s.key)
            uniq.append(s)
    sources = uniq[:12]
    if len(sources) < 2:
        raise RuntimeError("muy pocas fuentes de trucos/curiosidades por ahora")
    print(f"[tips] {len(sources)} fuentes ({sum(1 for s in sources if s.body)} con texto completo)")
    user = (f"Canal: {cfg['channel']['name']}\nNúmero de Shorts: {n}\n\n"
            f"YA PUBLICADOS (no repetir):\n- " + "\n- ".join(used[-60:] or ["(ninguno)"]) +
            f"\n\nFUENTES:\n{longform._material(sources)}")
    data = script._gemini(SYSTEM_TIPS, user, cfg["script"].get("gemini_model", "gemini-2.5-flash"), parse=_parse_items)
    return data["items"][:n], sources


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-youtube", action="store_true", help="solo Releases (para subir a mano a Reels/TikTok)")
    a = ap.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    state = load_state()
    used = [i.get("video_title") or i.get("title", "") for i in state["items"] if i.get("type") == "tip"]
    items, sources = write_tips(cfg, a.count, used)
    print(f"[tips] la IA escribió {len(items)} guion(es)")
    if a.no_youtube:
        cfg["publish"]["youtube"]["enabled"] = False
    made = 0
    for it in items:
        try:
            idx = int(it.get("fuente") or 1) - 1
        except (TypeError, ValueError):
            idx = 0
        src = sources[idx] if 0 <= idx < len(sources) else sources[0]
        story = news.NewsItem(title=it["title"], link=src.link, summary="", source=src.source, published=time.time())
        meta = script._finish({**it, "provider": "gemini"}, cfg)
        try:
            result = make_one(cfg, story, a.dry_run, meta_override=meta)
        except Exception as exc:
            print(f"[tips] no pude armar '{it['title']}': {exc}")
            continue
        result["type"] = "tip"
        made += 1
        if not a.dry_run:
            state["items"].append(result)
            save_state(state)
    print(f"\nTerminado: {made} Short(s) de trucos/curiosidades.")


if __name__ == "__main__":
    main()
