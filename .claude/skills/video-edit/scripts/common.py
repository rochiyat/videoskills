"""Utilitas bersama: lokasi ffmpeg, probe video, dan layout folder kerja."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
WORK_DIRNAME = "_edit"
SKILL_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = SKILL_DIR / "models"


def ffmpeg_exe() -> str:
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args, cwd=None):
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *map(str, args)]
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        sys.exit(f"ffmpeg gagal:\n{' '.join(cmd)}\n\n{result.stderr.strip()}")


def oriented_size(path: Path):
    """Ukuran frame setelah rotasi metadata diterapkan, sama seperti yang dilihat filter ffmpeg."""
    import cv2
    import numpy as np

    cmd = [ffmpeg_exe(), "-v", "error", "-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"]
    data = subprocess.run(cmd, capture_output=True, check=True).stdout
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    return img.shape[1], img.shape[0]


def probe(path: Path) -> dict:
    import av

    with av.open(str(path)) as container:
        video = next(iter(container.streams.video), None)
        audio = next(iter(container.streams.audio), None)
        info = {
            "duration": float(container.duration / av.time_base) if container.duration else None,
            "has_audio": audio is not None,
        }
        if video is not None:
            rate = video.average_rate or video.guessed_rate
            width, height = oriented_size(path)
            info.update(width=width, height=height, fps=round(float(rate), 3) if rate else None)
        return info


def ass_color(hex_color):
    h = hex_color.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ass_time(t):
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass_escape(text):
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def work_dir(input_dir: Path) -> Path:
    return input_dir / WORK_DIRNAME


def face_cache(wdir: Path, cid: str, meta: dict) -> Path:
    return wdir / "faces" / f"{cid}-{meta['fingerprint'].replace(':', '-')}.json"


def list_clips(input_dir: Path):
    return sorted(
        p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS and not p.name.startswith(".")
    )


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_input(arg: str) -> Path:
    path = Path(arg).expanduser().resolve()
    if not path.is_dir():
        sys.exit(f"Folder tidak ditemukan: {path}")
    if path.name == WORK_DIRNAME:
        path = path.parent
    return path
