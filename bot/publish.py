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


# ------------------------------------------------------------------ Instagram (Reels)
IG_API = "https://graph.instagram.com/v23.0"


def _ig_user(token: str) -> str:
    uid = os.environ.get("IG_USER_ID")
    if uid:
        return uid
    r = requests.get(f"{IG_API}/me", params={"fields": "user_id,username", "access_token": token}, timeout=30)
    r.raise_for_status()
    d = r.json()
    print(f"[instagram] cuenta: @{d.get('username')}")
    return str(d.get("user_id") or d["id"])


def upload_instagram(video: Path, caption: str, cfg: dict) -> str | None:
    """Publica el video como Reel (API de Instagram con inicio de sesión de Instagram, subida directa del archivo)."""
    icfg = cfg["publish"].get("instagram", {})
    token = os.environ.get("IG_ACCESS_TOKEN")
    if not icfg.get("enabled") or not token:
        print("[instagram] sin token: no se sube")
        return None
    import time

    uid = _ig_user(token)
    caption = caption[:2150]
    r = requests.post(f"{IG_API}/{uid}/media", timeout=60, data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption,
        "share_to_feed": "true", "access_token": token})
    if r.status_code >= 400:
        raise RuntimeError(f"Instagram (crear) HTTP {r.status_code}: {r.text[:300]}")
    cid = r.json()["id"]
    data = video.read_bytes()
    up = requests.post(f"https://rupload.facebook.com/ig-api-upload/v23.0/{cid}", data=data, timeout=600,
                       headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data))})
    if up.status_code >= 400:
        raise RuntimeError(f"Instagram (subir) HTTP {up.status_code}: {up.text[:300]}")
    for _ in range(60):  # Instagram procesa el video (hasta ~5 min)
        st = requests.get(f"{IG_API}/{cid}", params={"fields": "status_code,status", "access_token": token},
                          timeout=30).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram no pudo procesar el video: {st}")
        time.sleep(5)
    pub = requests.post(f"{IG_API}/{uid}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=60)
    if pub.status_code >= 400:
        raise RuntimeError(f"Instagram (publicar) HTTP {pub.status_code}: {pub.text[:300]}")
    mid = pub.json()["id"]
    link = requests.get(f"{IG_API}/{mid}", params={"fields": "permalink", "access_token": token},
                        timeout=30).json().get("permalink") or f"https://www.instagram.com/ (id {mid})"
    print(f"[instagram] Reel publicado: {link}")
    return link


def refresh_instagram_token() -> str | None:
    """Renueva el token de Instagram (dura 60 días). Devuelve el nuevo token o None."""
    token = os.environ.get("IG_ACCESS_TOKEN")
    if not token:
        return None
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=30)
    if r.status_code >= 400:
        print(f"[instagram] no pude renovar el token: {r.text[:200]}")
        return None
    return r.json().get("access_token")
