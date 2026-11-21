"""Conecta la cuenta de TikTok: cambia el código de autorización por los tokens y los guarda cifrados.

Lo dispara el Issue "[tiktok] conectar" que abre la página tiktok.html después de iniciar sesión en TikTok.
Uso: python scripts/tiktok_conectar.py "<texto del issue con el código>"
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import tiktok  # noqa: E402


def main() -> None:
    texto = sys.argv[1] if len(sys.argv) > 1 else ""
    m = re.search(r"(?:codigo|code)\s*[:=]\s*([^\s&]+)", texto, re.I)
    out = Path("out")
    out.mkdir(exist_ok=True)
    if not m:
        (out / "tiktok_conectar.md").write_text("❌ No encontré el código de TikTok en este pedido. "
                                                "Vuelve a la página de TikTok y toca **Conectar** otra vez.\n")
        sys.exit(1)
    try:
        tiktok.exchange_code(unquote(m.group(1)))
        nombre = tiktok.user_name()
    except Exception as exc:  # noqa: BLE001
        (out / "tiktok_conectar.md").write_text(f"❌ No pude conectar TikTok: {exc}\n\n"
                                                "El código dura pocos minutos: vuelve a tocar **Conectar** en la página.\n")
        sys.exit(1)
    (out / "tiktok_conectar.md").write_text(
        f"✅ TikTok conectado: **{nombre}**.\n\n"
        "Desde ahora la cola manda los clips a tu **bandeja de TikTok** (hasta 12 al día). Te llega una notificación "
        "en la app: ábrela, pega el texto que te dejo en el pedido #3 y toca Publicar.\n")
    print(f"[tiktok] conectado: {nombre}")


if __name__ == "__main__":
    main()
