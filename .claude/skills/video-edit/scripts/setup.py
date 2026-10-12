"""Buat virtualenv skill dan pasang dependensi.

Jalankan sekali dengan Python sistem (3.9+):
    python scripts/setup.py
"""
import os
import shutil
import subprocess
import sys
import urllib.request
import venv
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
VENV = SKILL / ".venv"
REQ = SKILL / "requirements.txt"
MODELS = SKILL / "models"
MODEL_URLS = {
    "blaze_face_short_range.tflite": "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite",
    "selfie_multiclass_256x256.tflite": "https://storage.googleapis.com/mediapipe-models/image_segmenter/"
    "selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite",
}


def download_models():
    MODELS.mkdir(exist_ok=True)
    for name, url in MODEL_URLS.items():
        dest = MODELS / name
        if dest.exists() and dest.stat().st_size > 0:
            continue
        print(f"Unduh {name}...", flush=True)
        tmp = dest.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(dest)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(cmd):
    print("$", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def main():
    if sys.version_info < (3, 9):
        sys.exit("Butuh Python 3.9 atau lebih baru.")

    uv = shutil.which("uv")
    if not venv_python().exists():
        if uv:
            run([uv, "venv", VENV, "--python", sys.executable])
        else:
            venv.create(VENV, with_pip=True)

    if uv:
        run([uv, "pip", "install", "--python", venv_python(), "-r", REQ])
    else:
        run([venv_python(), "-m", "pip", "install", "-r", REQ])
    download_models()

    run([venv_python(), SKILL / "scripts" / "doctor.py"])
    print(f"\nSiap. Python skill: {venv_python()}")


if __name__ == "__main__":
    main()
