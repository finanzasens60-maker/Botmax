"""Respuesta para un pedido APROBADO de otra persona.

Los Releases del bot son borradores (solo los ve el dueño), así que a la otra persona se le entregan
links de descarga temporales (Litterbox, 72 h). Nada de esto se sube a las cuentas del dueño.
Escribe out/externo.md.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

MAX = 15


def litterbox(video: Path) -> str | None:
    if video.stat().st_size > 990_000_000:
        return None
    try:
        out = subprocess.run(["curl", "-sS", "--retry", "2", "-F", "reqtype=fileupload", "-F", "time=72h",
                              "-F", f"fileToUpload=@{video}", "https://litterbox.catbox.moe/resources/internals/api.php"],
                             capture_output=True, text=True, timeout=1800).stdout.strip()
        return out if out.startswith("http") else None
    except Exception as exc:  # noqa: BLE001
        print(f"[externo] no pude subir {video.name}: {exc}")
        return None


def titulo(video: Path) -> str:
    for txt in (video.with_name(f"{video.stem}_publicar.txt"), video.with_suffix(".txt")):
        if txt.exists():
            m = re.search(r"T[ÍI]TULO:\s*\n(.+)", txt.read_text(encoding="utf-8"))
            if m:
                return m.group(1).strip()
    return video.stem


def main() -> None:
    resumen = Path("out/live_summary.md").read_text(encoding="utf-8") if Path("out/live_summary.md").exists() else ""
    links = re.findall(r"https://litter\.catbox\.moe/\S+", resumen)
    lines = [os.environ.get("ENCABEZADO") or "✅ ¡Listo! Tu pedido fue aprobado y ya está procesado.", ""]
    if links:  # el resumen ya creó sus links para compartir
        lines += [f"- Descargar video {i}: {u.rstrip(').,')}" for i, u in enumerate(dict.fromkeys(links), 1)]
    else:
        videos = sorted([p for p in Path("out").rglob("*.mp4") if p.name.startswith(("clip", "westcol", "video", "kick", "parte", "resumen")) or os.environ.get("TODOS")], key=lambda p: p.stat().st_mtime)[:MAX]
        for v in videos:
            u = litterbox(v)
            if u:
                lines.append(f"- {titulo(v)} — {u}")
    if len(lines) == 2:
        lines.append("No pude crear los links de descarga esta vez; el dueño del bot te los puede pasar.")
    else:
        lines += ["", "Los links duran **72 horas**: descárgalos antes de que venzan."]
    Path("out/externo.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
