"""Cek bahwa semua dependensi skill siap dipakai."""
import subprocess
import sys

from common import ffmpeg_exe

ok = True


def check(label, fn):
    global ok
    try:
        detail = fn()
        print(f"[ok]    {label}: {detail}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[gagal] {label}: {exc}")


def _ffmpeg():
    exe = ffmpeg_exe()
    filters = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    missing = [f for f in ("ass", "loudnorm", "afade") if f" {f} " not in filters]
    if missing:
        raise RuntimeError(f"filter tidak tersedia: {', '.join(missing)} ({exe})")
    return exe


def _module(name):
    def inner():
        mod = __import__(name)
        return getattr(mod, "__version__", "terpasang")

    return inner


def _models():
    from common import MODELS_DIR

    names = ["blaze_face_short_range.tflite", "selfie_multiclass_256x256.tflite"]
    missing = [n for n in names if not (MODELS_DIR / n).exists()]
    if missing:
        raise RuntimeError(f"belum diunduh: {', '.join(missing)} (jalankan setup.py)")
    return str(MODELS_DIR)


check("python", lambda: sys.version.split()[0])
check("faster_whisper", _module("faster_whisper"))
check("av", _module("av"))
check("mediapipe", _module("mediapipe"))
check("model wajah/segmentasi", _models)
check("ffmpeg", _ffmpeg)
sys.exit(0 if ok else 1)
