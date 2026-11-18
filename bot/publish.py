"""Publica en YouTube Shorts y te avisa por Telegram con el video listo para TikTok/Reels."""
from __future__ import annotations

import os
from pathlib import Path

import requests


def build_description(meta: dict, story, cfg: dict) -> str:
    ch = cfg["channel"]
    lines = [
        meta["description"],
        "",
        f"Fuente: {story.source}" + (f" — {story.link}" if story.link and "news.google" not in story.link else ""),
        "",
        f"Suscríbete a {ch['name']} para no perderte nada de GTA 6.",
        "Narración generada con voz sintética. Canal de fans, no afiliado a Rockstar Games ni Take-Two.",
        "",
        " ".join(meta["hashtags"]),
    ]
    return "\n".join(lines)


def upload_youtube(video: Path, meta: dict, description: str, cfg: dict, short: bool = True,
                   thumbnail: Path | None = None) -> str | None:
    ycfg = cfg["publish"]["youtube"]
    cid, secret, refresh = (os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))
    if not ycfg.get("enabled") or not all((cid, secret, refresh)):
        print("[youtube] sin credenciales: no se sube (el video queda en Releases/Telegram)")
        return None

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(None, refresh_token=refresh, client_id=cid, client_secret=secret,
                        token_uri="https://oauth2.googleapis.com/token",
                        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    title = meta["title"]
    if short and "#shorts" not in title.lower() and len(title) <= 90:
        title = f"{title} #Shorts"
    tags = list(dict.fromkeys(ycfg.get("default_tags", []) + [t.lstrip("#") for t in meta["hashtags"]]))
    if not short:
        tags = [t for t in tags if t.lower() != "shorts"]
    body = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:30],
                    "categoryId": ycfg.get("category_id", "20"), "defaultLanguage": "es",
                    "defaultAudioLanguage": "es"},
        "status": {"privacyStatus": ycfg.get("privacy", "public"), "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": True},
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    status = resp.get("status", {}).get("privacyStatus")
    url = f"https://youtube.com/shorts/{vid}" if short else f"https://youtu.be/{vid}"
    canal = resp.get("snippet", {}).get("channelTitle", "?")
    print(f"[youtube] subido al canal '{canal}': {url} (estado: {status})")
    if thumbnail and thumbnail.exists():
        try:  # requiere canal verificado por teléfono
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumbnail), mimetype="image/jpeg")).execute()
            print("[youtube] miniatura puesta")
        except Exception as exc:
            print(f"[youtube] no se pudo poner la miniatura (¿canal verificado?): {exc}")
    return url


def send_telegram(video: Path, meta: dict, yt_url: str | None, release_url: str | None) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    caption = f"🎬 {meta['title']}\n\n{meta['description']}\n\n{' '.join(meta['hashtags'])}"
    if yt_url:
        caption += f"\n\nYouTube: {yt_url}"
    elif release_url:
        caption += f"\n\nDescarga: {release_url}"
    try:
        with open(video, "rb") as fh:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendVideo",
                              data={"chat_id": chat, "caption": caption[:1024], "supports_streaming": "true"},
                              files={"video": fh}, timeout=180)
        r.raise_for_status()
        print("[telegram] video enviado a tu celular")
    except Exception as exc:
        print(f"[telegram] falló: {exc}")


def send_telegram_text(text: str, photo: Path | None, link: str | None) -> None:
    """Para videos largos (muy pesados para Telegram): manda la miniatura + enlace."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    caption = text + (f"\n\n{link}" if link else "")
    try:
        if photo and photo.exists():
            with open(photo, "rb") as fh:
                requests.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                              data={"chat_id": chat, "caption": caption[:1024]}, files={"photo": fh}, timeout=60)
        else:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": caption}, timeout=30)
        print("[telegram] aviso enviado")
    except Exception as exc:
        print(f"[telegram] falló: {exc}")
