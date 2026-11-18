"""Escribe el guion del Short con IA gratuita (GitHub Models o Gemini) y una plantilla de respaldo."""
from __future__ import annotations

import json
import os
import random
import re

import requests

from .news import NewsItem

SYSTEM = """Eres el guionista de un canal de YouTube Shorts en español latino sobre GTA 6 (Grand Theft Auto VI).
Escribes guiones hablados, cortos, con energía y que enganchan en los primeros 2 segundos.

REGLAS DE ORO:
- Usa SOLO los datos de la noticia que te doy. No inventes cifras, fechas, nombres ni detalles.
- Si la noticia es rumor, filtración o viene de Reddit/redes, dilo claramente ("según rumores", "un usuario asegura").
- Nada de groserías, ni contenido para niños, ni piratería/mods/trampas.
- Aporta algo propio: contexto, por qué importa o una opinión corta. No te limites a leer el titular.
- Varía el estilo de gancho: pregunta, dato impactante, "nadie está hablando de esto", comparación, etc.
- Termina con una pregunta para comentarios o un llamado a seguir el canal (varía la frase).
- Escribe los números como se dicen ("diecinueve de noviembre", "setenta dólares") para que la voz los lea bien.
- Di "GTA seis" en el guion hablado (no "GTA 6").

Responde SOLO con JSON válido con estas claves:
{"script": "texto que se leerá en voz alta",
 "headline": "titular corto en MAYÚSCULAS para mostrar arriba del video (máx 6 palabras)",
 "title": "título de YouTube (máx 70 caracteres, con gancho, sin clickbait falso)",
 "description": "descripción de 2-3 frases",
 "hashtags": ["#GTA6", "..."]}"""


def _user_prompt(story: NewsItem, target_words: int, channel: str) -> str:
    parts = [
        f"Canal: {channel}",
        f"Longitud del guion: entre {target_words - 20} y {target_words + 15} palabras.",
        f"\nNOTICIA PRINCIPAL\nTitular: {story.title}\nFuente: {story.source}\nResumen: {story.summary or '(sin resumen)'}",
    ]
    if story.body:
        parts.append(f"Texto del artículo (extracto):\n{story.body[:3000]}")
    if story.related:
        parts.append("Otros titulares sobre el mismo tema:\n- " + "\n- ".join(story.related))
    return "\n".join(parts)


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0) if m else text)
    for k in ("script", "headline", "title", "description"):
        if not str(data.get(k, "")).strip():
            raise ValueError(f"falta '{k}' en la respuesta")
    return data


def _github(system: str, user: str, model: str) -> dict:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_MODELS_TOKEN")
    if not token:
        raise RuntimeError("no hay GITHUB_TOKEN")
    r = requests.post(
        "https://models.github.ai/inference/chat/completions",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.9,
            "response_format": {"type": "json_object"},
        },
        timeout=90,
    )
    r.raise_for_status()
    return _parse_json(r.json()["choices"][0]["message"]["content"])


def _gemini(system: str, user: str, model: str) -> dict:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("no hay GEMINI_API_KEY")
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.9, "responseMimeType": "application/json"},
        },
        timeout=90,
    )
    r.raise_for_status()
    return _parse_json(r.json()["candidates"][0]["content"]["parts"][0]["text"])


def _template(story: NewsItem, channel: str) -> dict:
    title = re.sub(r"\s+-\s+[^-]+$", "", story.title)
    hooks = ["Atención fans de GTA seis.", "Esto acaba de pasar con GTA seis.", "Nueva noticia de GTA seis."]
    closers = ["¿Tú qué opinas? Te leo en los comentarios.", "Sígueme para más noticias de GTA seis."]
    summary = story.summary if story.summary and story.summary.lower() not in title.lower() else ""
    text = f"{random.choice(hooks)} {title}. {summary[:350]} Según {story.source or 'medios especializados'}. {random.choice(closers)}"
    return {
        "script": text.replace("GTA 6", "GTA seis").replace("GTA VI", "GTA seis"),
        "headline": "ÚLTIMA HORA GTA 6",
        "title": title[:70],
        "description": f"{title}. Fuente: {story.source}.",
        "hashtags": ["#GTA6", "#GTAVI", "#Shorts"],
    }


def write_script(story: NewsItem, cfg: dict) -> dict:
    scfg = cfg["script"]
    user = _user_prompt(story, scfg.get("target_words", 120), cfg["channel"]["name"])
    for provider in scfg["providers"]:
        try:
            if provider == "github":
                data = _github(SYSTEM, user, scfg["github_model"])
            elif provider == "gemini":
                data = _gemini(SYSTEM, user, scfg["gemini_model"])
            else:
                data = _template(story, cfg["channel"]["name"])
            data["provider"] = provider
            print(f"[script] guion generado con '{provider}' ({len(data['script'].split())} palabras)")
            return _finish(data, cfg)
        except Exception as exc:
            print(f"[script] '{provider}' falló: {exc}")
    raise RuntimeError("Ningún proveedor pudo escribir el guion")


def _finish(data: dict, cfg: dict) -> dict:
    tags = [t if t.startswith("#") else f"#{t}" for t in data.get("hashtags", [])]
    for must in ("#GTA6", "#Shorts"):
        if must.lower() not in [t.lower() for t in tags]:
            tags.append(must)
    data["hashtags"] = [re.sub(r"\s+", "", t) for t in tags][:8]
    data["title"] = data["title"].strip()[:95]
    data["headline"] = data["headline"].upper().strip()[:60]
    return data
