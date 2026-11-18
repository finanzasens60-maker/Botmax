"""Escribe el guion del Short con IA gratuita (Google Gemini, o Groq de respaldo) y una plantilla de emergencia.

Nota: GitHub Models fue retirado por GitHub en julio de 2026, por eso ahora se usa Gemini (gratis con
una clave de aistudio.google.com/apikey guardada en el secreto GEMINI_API_KEY).
"""
from __future__ import annotations

import json
import os
import random
import re

import requests

from .news import NewsItem

SYSTEM = """Eres el guionista y la voz de un canal de YouTube Shorts en español latino sobre GTA 6.
Escribes como habla un creador de contenido real: cercano, con energía y ritmo, como si se lo contaras a un amigo.

ESTILO HABLADO (muy importante, el texto lo lee una voz sintética y debe sonar humano):
- Frases cortas y variadas. Mezcla frases de 3-6 palabras con otras más largas.
- Usa comas y puntos para marcar pausas naturales. Usa signos ¿? y ¡! cuando aporten emoción.
- Habla en segunda persona ("mira esto", "imagínate", "¿te diste cuenta?").
- Nada de listas, viñetas, emojis, paréntesis, comillas ni mayúsculas sostenidas.
- NUNCA repitas el titular ni la misma idea dos veces. Cada frase debe aportar algo nuevo.
- Gancho brutal en la primera frase (máximo 10 palabras): dato sorprendente, pregunta o contradicción.
  Varía el tipo de gancho; no empieces con "Atención" ni con "Hola".
- Luego: qué pasó, por qué importa y un detalle curioso o una opinión corta.
- Cierra con una pregunta concreta para comentarios o una invitación a seguir el canal (varía la frase).
- Escribe los números como se dicen ("diecinueve de noviembre", "setenta dólares").
- Di "GTA seis" (no "GTA 6" ni "GTA VI").

REGLAS DE CONTENIDO:
- Usa SOLO los datos de la noticia. No inventes cifras, fechas, nombres ni detalles.
- Si es rumor, filtración o viene de redes, dilo ("según rumores", "un usuario asegura").
- Nada de groserías, piratería, mods ni trampas.

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


def _gemini_call(system: str, user: str, model: str, key: str) -> str:
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.95, "responseMimeType": "application/json"},
        },
        timeout=120,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini {model} HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _gemini_models(key: str) -> list[str]:
    """Pregunta a Google qué modelos de texto hay disponibles (los nombres cambian con el tiempo)."""
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                         headers={"x-goog-api-key": key}, timeout=30)
        names = [m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
    except Exception:
        return []
    bad = ("image", "tts", "audio", "embedding", "vision", "live", "robotics", "nano", "lite")
    ok = [n for n in names if "gemini" in n and not any(b in n for b in bad)]
    ver = lambda n: float((re.findall(r"\d+(?:\.\d+)?", n) or ["0"])[0])
    # primero los "flash" estables más nuevos, luego previews y "pro"
    rank = lambda n: ("flash" in n, "preview" not in n and "exp" not in n, ver(n))
    return sorted(ok, key=rank, reverse=True)[:4]


def _groq_call(system: str, user: str, key: str) -> str:
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
              "temperature": 0.95, "response_format": {"type": "json_object"}},
        timeout=120,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Groq HTTP {r.status_code}: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]


def _gemini(system: str, user: str, model: str, parse=None) -> dict:
    """IA gratis: prueba Gemini (varios modelos) y, si hay clave, Groq de respaldo."""
    parse = parse or _parse_json
    errors = []
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        for m in dict.fromkeys([model, "gemini-flash-latest", *_gemini_models(key)]):
            try:
                out = parse(_gemini_call(system, user, m, key))
                print(f"[script] modelo de IA: {m}")
                return out
            except Exception as exc:
                errors.append(str(exc).replace("\n", " ")[:160])
    else:
        errors.append("no hay GEMINI_API_KEY")
    gkey = os.environ.get("GROQ_API_KEY")
    if gkey:
        try:
            return parse(_groq_call(system, user, gkey))
        except Exception as exc:
            errors.append(str(exc)[:160])
    raise RuntimeError(" | ".join(errors))


_github = _gemini  # compatibilidad: GitHub Models fue retirado


def _template(story: NewsItem, channel: str) -> dict:
    """Plantilla de emergencia (sin IA). Evita repetir el titular."""
    title = re.sub(r"\s+-\s+[^-]+$", "", story.title).strip()
    tw = set(re.findall(r"\w{4,}", title.lower()))
    summary = re.sub(r"\s+-\s+[^-]+$", "", story.summary or "").strip()
    sw = set(re.findall(r"\w{4,}", summary.lower()))
    if not summary or (sw and len(sw & tw) / len(sw) > 0.5):
        summary = ""
    hooks = ["Esto no te lo esperabas.", "Ojo con lo que acaba de salir.", "Hay novedades, y son importantes."]
    closers = ["¿Tú qué opinas? Te leo en los comentarios.", "Sígueme para no perderte nada."]
    parts = [random.choice(hooks), f"{title}."]
    if summary:
        parts.append(summary[:300].rstrip(".") + ".")
    parts += [f"Lo informó {story.source or 'la prensa especializada'}.", random.choice(closers)]
    text = " ".join(parts)
    return {
        "script": re.sub(r"\bGTA ?(6|VI)\b", "GTA seis", text),
        "headline": "ÚLTIMA HORA GTA 6",
        "title": title[:70],
        "description": f"{title}. Fuente: {story.source}.",
        "hashtags": ["#GTA6", "#GTAVI", "#Shorts"],
    }


def _dedupe(text: str) -> str:
    """Quita frases repetidas (o casi iguales) para que la voz no repita la misma idea."""
    sents = re.split(r"(?<=[.!?¡¿])\s+", text.strip())
    out, seen = [], []
    for s in sents:
        w = set(re.findall(r"\w{4,}", s.lower()))
        if w and any(len(w & p) / max(1, min(len(w), len(p))) > 0.7 for p in seen):
            continue
        seen.append(w)
        out.append(s)
    return " ".join(out)


def write_script(story: NewsItem, cfg: dict) -> dict:
    scfg = cfg["script"]
    user = _user_prompt(story, scfg.get("target_words", 120), cfg["channel"]["name"])
    for provider in scfg["providers"]:
        try:
            if provider == "template":
                data = _template(story, cfg["channel"]["name"])
            else:
                data = _gemini(SYSTEM, user, scfg.get("gemini_model", "gemini-2.5-flash"))
            data["provider"] = provider
            print(f"[script] guion generado con '{provider}' ({len(data['script'].split())} palabras)")
            return _finish(data, cfg)
        except Exception as exc:
            print(f"[script] '{provider}' falló: {exc}")
    raise RuntimeError("Ningún proveedor pudo escribir el guion")


def _finish(data: dict, cfg: dict) -> dict:
    data["script"] = _dedupe(re.sub(r"\bGTA ?(6|VI)\b", "GTA seis", data["script"]))
    tags = [t if t.startswith("#") else f"#{t}" for t in data.get("hashtags", [])]
    for must in ("#GTA6", "#Shorts"):
        if must.lower() not in [t.lower() for t in tags]:
            tags.append(must)
    data["hashtags"] = [re.sub(r"\s+", "", t) for t in tags][:8]
    data["title"] = data["title"].strip()[:95]
    data["headline"] = data["headline"].upper().strip()[:60]
    return data
