"""Une fondo + voz + subtítulos estilo karaoke + titular en un MP4 vertical listo para Shorts."""
from __future__ import annotations

import random
import subprocess
from pathlib import Path


def _ass_color(hex_color: str) -> str:
    c = hex_color.lstrip("#")
    return f"&H00{c[4:6]}{c[2:4]}{c[0:2]}&".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(words: list[dict], cfg: dict, out: Path) -> Path:
    v = cfg["video"]
    n = max(1, int(v.get("words_per_caption", 3)))
    hl = _ass_color(v["highlight_color"])
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {v['width']}
PlayResY: {v['height']}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Poppins,{v.get('caption_size', 100)},&H00FFFFFF&,&H00FFFFFF&,&H00000000&,&H80000000&,-1,0,0,0,100,100,0,0,1,7,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    cx, cy = v["width"] // 2, int(v["height"] * v.get("caption_y", 0.62))
    clean = [w for w in words if w["word"].strip()]
    for i in range(0, len(clean), n):
        chunk = clean[i:i + n]
        for j, cur in enumerate(chunk):
            start = cur["start"]
            nxt = chunk[j + 1]["start"] if j + 1 < len(chunk) else (
                clean[i + n]["start"] if i + n < len(clean) else cur["end"] + 0.5)
            parts = []
            for k, wd in enumerate(chunk):
                txt = wd["word"].upper().replace("{", "").replace("}", "")
                if k == j:
                    parts.append(r"{\c" + hl + r"\fscx112\fscy112}" + txt + r"{\c&H00FFFFFF&\fscx100\fscy100}")
                else:
                    parts.append(txt)
            pop = r"{\pos(" + f"{cx},{cy}" + r")" + (r"\fad(60,0)" if j == 0 else "") + "}"
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(nxt)},Cap,,0,0,0,,{pop}{' '.join(parts)}")
    out.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return out


def _has_audio(path: Path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return bool(out.stdout.strip())


def render(cfg: dict, background: Path, header_png: Path, voice: Path, ass: Path,
           duration: float, out: Path, offset: float = 0.0, grand_total: float | None = None,
           tail: float = 0.8) -> Path:
    v = cfg["video"]
    total = duration + tail
    grand_total = grand_total or total
    music = [Path(v["music_file"])] if v.get("music_file") else [p for p in Path("assets/music").glob("*") if p.suffix.lower() in {".mp3", ".wav", ".m4a", ".ogg"}]
    accent = v["accent_color"].lstrip("#")
    fonts_dir = str(Path(v["font"]).parent.resolve())

    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-stream_loop", "-1", "-i", str(background),
           "-loop", "1", "-i", str(header_png),
           "-i", str(voice),
           "-f", "lavfi", "-i", f"color=c=0x{accent}:s={v['width']}x14:r={v['fps']}"]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(random.choice(music))]

    ass_path = str(ass.resolve()).replace(":", r"\:")
    vf = (
        f"[0:v]trim=0:{total:.2f},setpts=PTS-STARTPTS,"
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.28:t=fill[bg];"
        f"[bg][1:v]overlay=0:0:shortest=1[hd];"
        f"[hd]subtitles='{ass_path}':fontsdir='{fonts_dir}'[sub];"
        f"[sub][3:v]overlay=x='-w+w*(t+{offset:.2f})/{grand_total:.2f}':y=0:shortest=1,format=yuv420p[v]"
    )
    # --- Audio: voz al frente, sonido del juego de fondo que baja solo cuando habla la voz ---
    game_vol = float(v.get("game_audio_volume", 0))
    has_game = game_vol > 0 and _has_audio(background)
    voice_chain = f"[2:a]apad=pad_dur={tail},aresample=48000"
    parts, labels = [], []
    if has_game:
        parts.append(voice_chain + ",asplit=2[vo][sc]")
        parts.append(f"[0:a]atrim=0:{total:.2f},asetpts=PTS-STARTPTS,aresample=48000,volume={game_vol}[ga]")
        parts.append("[ga][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350:makeup=1[gd]")
        labels += ["[vo]", "[gd]"]
    else:
        parts.append(voice_chain + "[vo]")
        labels.append("[vo]")
    if music:
        parts.append(f"[4:a]aresample=48000,volume={v['music_volume']}[mu]")
        labels.append("[mu]")
    mix = (f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:dropout_transition=0:normalize=0,"
           if len(labels) > 1 else f"{labels[0]}")
    parts.append(f"{mix}loudnorm=I=-14:TP=-1.5:LRA=11[a]" if len(labels) > 1
                 else f"{labels[0]}loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    af = ";".join(parts)

    cmd += ["-filter_complex", vf + ";" + af, "-map", "[v]", "-map", "[a]",
            "-t", f"{total:.2f}", "-r", str(v["fps"]),
            "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-profile:v", "high",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    return out
