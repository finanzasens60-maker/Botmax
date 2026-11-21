"""TikTok: manda videos a la BANDEJA (borradores) de la cuenta del canal.

Cada video llega como notificación en la app de TikTok; el dueño lo abre, pega el texto y lo publica.
Usa la Content Posting API (modo Upload, scope video.upload): no necesita la revisión de TikTok
y la cuenta puede ser pública.

Los tokens se guardan CIFRADOS en data/tiktok_token.enc (clave = secreto TIKTOK_CLIENT_SECRET),
porque TikTok los renueva y un workflow no puede reescribir los secretos del repo.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import requests

API = "https://open.tiktokapis.com"
TOKEN_FILE = Path("data/tiktok_token.enc")
REDIRECT_URI = "https://finanzasens60-maker.github.io/videos-10-minutos/tiktok.html"
SCOPES = "user.info.basic,video.upload"
MB = 1024 * 1024


class TikTokLimit(Exception):
    """TikTok cortó por el tope diario de subidas (se reintenta en la próxima revisión)."""


def _secret() -> str:
    s = os.environ.get("TIKTOK_CLIENT_SECRET", "")
    if not s:
        raise RuntimeError("falta el secreto TIKTOK_CLIENT_SECRET")
    return s


def _openssl(args: list[str], data: bytes) -> bytes:
    return subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-a", "-A",
                           *args, "-pass", "env:TIKTOK_CLIENT_SECRET"],
                          input=data, capture_output=True, check=True,
                          env={**os.environ, "TIKTOK_CLIENT_SECRET": _secret()}).stdout


def save_token(tok: dict) -> None:
    tok = {**tok, "saved_at": int(time.time())}
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_bytes(_openssl([], json.dumps(tok).encode()) + b"\n")


def load_token() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    return json.loads(_openssl(["-d"], TOKEN_FILE.read_bytes().strip()))


def exchange_code(code: str) -> dict:
    r = requests.post(f"{API}/v2/oauth/token/", timeout=30, data={
        "client_key": os.environ["TIKTOK_CLIENT_KEY"], "client_secret": _secret(), "code": code,
        "grant_type": "authorization_code", "redirect_uri": REDIRECT_URI})
    tok = r.json()
    if "access_token" not in tok:
        raise RuntimeError(f"TikTok no aceptó el código: {tok.get('error')} {tok.get('error_description')}")
    tok["access_expires_at"] = int(time.time()) + int(tok.get("expires_in", 0)) - 120
    save_token(tok)
    return tok


def access_token() -> str:
    tok = load_token()
    if not tok:
        raise RuntimeError("TikTok no está conectado (falta data/tiktok_token.enc)")
    if tok.get("access_expires_at", 0) > time.time():
        return tok["access_token"]
    r = requests.post(f"{API}/v2/oauth/token/", timeout=30, data={
        "client_key": os.environ["TIKTOK_CLIENT_KEY"], "client_secret": _secret(),
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    new = r.json()
    if "access_token" not in new:
        raise RuntimeError(f"no pude renovar el token de TikTok: {new.get('error')} {new.get('error_description')}"
                           " — vuelve a conectar la cuenta desde la página de TikTok")
    new["access_expires_at"] = int(time.time()) + int(new.get("expires_in", 0)) - 120
    save_token({**tok, **new})
    return new["access_token"]


def user_name() -> str:
    r = requests.get(f"{API}/v2/user/info/", params={"fields": "display_name"}, timeout=30,
                     headers={"Authorization": f"Bearer {access_token()}"})
    return r.json().get("data", {}).get("user", {}).get("display_name", "?")


def _check(resp: requests.Response) -> dict:
    data = resp.json()
    err = data.get("error", {})
    code = err.get("code", "ok")
    if code != "ok":
        if code.startswith("spam_risk") or code in ("rate_limit_exceeded", "reached_active_user_cap"):
            raise TikTokLimit(f"{code}: {err.get('message')}")
        raise RuntimeError(f"TikTok: {code}: {err.get('message')}")
    return data.get("data", {})


def upload_inbox(video: Path) -> str:
    """Sube el video a la bandeja de TikTok. Devuelve el publish_id."""
    size = video.stat().st_size
    if size <= 64 * MB:
        chunk, count = size, 1
    else:
        chunk = 10 * MB
        count = size // chunk  # el último trozo se lleva el sobrante (permitido hasta 128 MB)
    hdr = {"Authorization": f"Bearer {access_token()}", "Content-Type": "application/json; charset=UTF-8"}
    init = _check(requests.post(f"{API}/v2/post/publish/inbox/video/init/", headers=hdr, timeout=60, json={
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                        "total_chunk_count": count}}))
    url, pid = init["upload_url"], init["publish_id"]
    with video.open("rb") as f:
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1
            f.seek(start)
            body = f.read(end - start + 1)
            r = requests.put(url, data=body, timeout=300, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(body)),
                "Content-Range": f"bytes {start}-{end}/{size}"})
            if r.status_code not in (200, 201, 206):
                raise RuntimeError(f"TikTok rechazó el trozo {i + 1}/{count}: HTTP {r.status_code} {r.text[:200]}")
    # esperar a que TikTok lo procese y lo mande a la bandeja
    for _ in range(30):
        st = _check(requests.post(f"{API}/v2/post/publish/status/fetch/", headers=hdr, timeout=30,
                                  json={"publish_id": pid}))
        status = st.get("status", "")
        if status in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            print(f"[tiktok] en la bandeja de TikTok: {video.name} ({pid})")
            return pid
        if status == "FAILED":
            raise RuntimeError(f"TikTok no pudo procesar el video: {st.get('fail_reason')}")
        time.sleep(5)
    print(f"[tiktok] {video.name}: TikTok sigue procesando ({pid}); debería llegar a la bandeja en minutos")
    return pid
