"""Recolecta noticias de GTA 6 desde feeds RSS y elige la mejor no publicada."""
from __future__ import annotations

import calendar
import hashlib
import html
import re
import time
from dataclasses import dataclass, field

import feedparser
import requests

UA = "Mozilla/5.0 (compatible; GTA6ShortsBot/1.0; +https://github.com)"


@dataclass
class NewsItem:
    title: str
    link: str
    summary: str
    source: str
    published: float
    body: str = ""
    related: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return hashlib.sha1(_norm(self.title).encode()).hexdigest()[:16]


def _norm(text: str) -> str:
    text = re.sub(r"\s+-\s+[^-]+$", "", text)  # quita " - Fuente" de Google News
    text = re.sub(r"[^a-z0-9áéíóúñü ]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _words(text: str) -> set[str]:
    return {w for w in _norm(text).split() if len(w) > 3}


def fetch_items(cfg: dict) -> list[NewsItem]:
    ncfg = cfg["news"]
    must = [m.lower() for m in ncfg["must_match"]]
    block = [b.lower() for b in ncfg["blocklist"]]
    max_age = ncfg.get("max_age_hours", 48) * 3600
    now = time.time()
    items: list[NewsItem] = []

    for url in ncfg["feeds"]:
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as exc:  # un feed caído no detiene el bot
            print(f"[news] no se pudo leer {url[:70]}…: {exc}")
            continue
        for e in feed.entries:
            title = _clean(e.get("title", ""))
            summary = _clean(e.get("summary", ""))[:1200]
            low = f"{title} {summary}".lower()
            if not title or not any(m in low for m in must) or any(b in low for b in block):
                continue
            ts = e.get("published_parsed") or e.get("updated_parsed")
            published = calendar.timegm(ts) if ts else now
            if now - published > max_age:
                continue
            source = ""
            if e.get("source"):
                source = e["source"].get("title", "")
            if not source:
                m = re.search(r"\s-\s([^-]+)$", title)
                source = m.group(1).strip() if m else feed.feed.get("title", "")
            items.append(NewsItem(title, e.get("link", ""), summary, source, published))
        print(f"[news] {url[:60]}… -> {len(feed.entries)} entradas")
    return items


def pick_story(items: list[NewsItem], published_keys: set[str], published_titles: list[str]) -> NewsItem | None:
    """Agrupa titulares sobre el mismo tema y elige el más 'caliente' que no hayamos usado."""
    used_words = [_words(t) for t in published_titles[-60:]]
    clusters: list[list[NewsItem]] = []
    for it in sorted(items, key=lambda x: -x.published):
        w = _words(it.title)
        for cl in clusters:
            cw = _words(cl[0].title)
            if w and cw and len(w & cw) / max(1, min(len(w), len(cw))) >= 0.5:
                cl.append(it)
                break
        else:
            clusters.append([it])

    def is_used(it: NewsItem) -> bool:
        if it.key in published_keys:
            return True
        w = _words(it.title)
        return any(w and u and len(w & u) / max(1, min(len(w), len(u))) >= 0.6 for u in used_words)

    now = time.time()
    scored = []
    for cl in clusters:
        if any(is_used(it) for it in cl):
            continue
        lead = max(cl, key=lambda x: len(x.summary))
        age_h = (now - max(x.published for x in cl)) / 3600
        score = len(cl) * 3 + max(0, 24 - age_h) / 4  # más fuentes + más reciente = mejor
        if re.search(r"\b(lanzamiento|launch|release|tráiler|trailer|precio|price|online|ventas|sales|récord|record|mapa|map)\b", lead.title.lower()):
            score += 2
        lead.related = [x.title for x in cl if x is not lead][:5]
        scored.append((score, lead))
    if not scored:
        return None
    scored.sort(key=lambda s: -s[0])
    story = scored[0][1]
    story.body = fetch_article_text(story.link)
    return story


def fetch_article_text(url: str) -> str:
    """Intenta leer el texto del artículo para que el guion tenga datos reales."""
    if not url or "news.google.com" in url:
        return ""
    try:
        import trafilatura

        resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        text = trafilatura.extract(resp.text) or ""
        return text[:4000]
    except Exception as exc:
        print(f"[news] no se pudo extraer el artículo: {exc}")
        return ""
