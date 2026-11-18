"""Genera una muestra de audio por cada preset de voz de config.yaml, para escucharlas y elegir.

Uso:  python scripts/probar_voces.py        (crea la carpeta muestras/)
En GitHub: Actions -> "Probar voces" -> Run workflow -> descarga el artefacto "muestras-de-voz".
"""
import copy
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import tts  # noqa: E402

TEXTO = ("Esto no es un simulacro. GTA seis por fin está aquí, y Leonida es más grande de lo que imaginabas. "
         "Quédate, porque lo que encontraron en Vice City va a cambiar tu forma de jugar.")

cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
out_dir = Path("muestras")
out_dir.mkdir(exist_ok=True)
for name in cfg["voice"]["presets"]:
    c = copy.deepcopy(cfg)
    c["voice"]["preset"] = name
    path, _, dur = tts.synthesize(TEXTO, c, out_dir / f"{name}.mp3")
    print(f"  -> {path} ({dur:.1f}s)")
print("\nListo. Escucha las muestras y pon el nombre que más te guste en config.yaml -> voice -> preset")
