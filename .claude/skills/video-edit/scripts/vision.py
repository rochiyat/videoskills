"""Deteksi wajah dan matte orang (MediaPipe), dengan frame dibaca lewat ffmpeg.

Frame selalu diambil lewat ffmpeg supaya rotasi video HP diterapkan sama persis
dengan saat render, sehingga koordinat wajah cocok dengan frame yang dipotong.
Semua koordinat wajah dinormalisasi 0..1: (cx, cy, w, h).
"""
import os
import subprocess
from pathlib import Path

os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import cv2  # noqa: E402
import numpy as np

from common import MODELS_DIR, ffmpeg_exe, oriented_size, read_json, write_json

FACE_SAMPLE_FPS = 4
ANALYSIS_WIDTH = 640


def read_frames(args, width, height, cwd=None):
    """Jalankan ffmpeg yang menulis rgb24 mentah ke stdout, lalu yield array HxWx3."""
    cmd = [ffmpeg_exe(), "-v", "error", "-nostdin", *map(str, args), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    size = width * height * 3
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, cwd=cwd) as proc:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(height, width, 3)


class FaceDetector:
    def __init__(self):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        self._mp = mp
        options = vision.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(MODELS_DIR / "blaze_face_short_range.tflite")),
            min_detection_confidence=0.5,
        )
        self._detector = vision.FaceDetector.create_from_options(options)

    def detect(self, rgb):
        """Wajah terbesar sebagai (cx, cy, w, h) ternormalisasi, atau None."""
        h, w = rgb.shape[:2]
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        faces = self._detector.detect(image).detections
        if not faces:
            return None
        box = max(faces, key=lambda d: d.bounding_box.width * d.bounding_box.height).bounding_box
        return ((box.origin_x + box.width / 2) / w, (box.origin_y + box.height / 2) / h, box.width / w, box.height / h)

    def close(self):
        self._detector.close()


def analyze_faces(path: Path, cache: Path):
    """Sampel posisi wajah 4x per detik. Di-cache sebagai [[t, cx, cy, w, h] | [t]]."""
    cached = read_json(cache)
    if cached:
        return cached
    src_w, src_h = oriented_size(path)
    width = ANALYSIS_WIDTH
    height = round(src_h * width / src_w / 2) * 2
    detector = FaceDetector()
    samples = []
    try:
        args = ["-i", path, "-vf", f"fps={FACE_SAMPLE_FPS},scale={width}:{height}"]
        for n, frame in enumerate(read_frames(args, width, height)):
            t = round(n / FACE_SAMPLE_FPS, 3)
            box = detector.detect(frame)
            samples.append([t, *[round(v, 4) for v in box]] if box else [t])
    finally:
        detector.close()
    data = {"width": src_w, "height": src_h, "samples": samples}
    write_json(cache, data)
    return data


def face_in_range(faces, t0, t1):
    """Median kotak wajah pada rentang waktu sumber, atau None kalau tidak terdeteksi."""
    if not faces:
        return None
    boxes = [s[1:] for s in faces["samples"] if len(s) == 5 and t0 - 0.25 <= s[0] <= t1 + 0.25]
    if not boxes:
        return None
    return tuple(float(v) for v in np.median(np.array(boxes), axis=0))


class PersonMatte:
    """Matte orang per frame: segmentasi MediaPipe, dihaluskan dengan guided filter dan EMA antar-frame."""

    def __init__(self, smoothing=0.5):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        self._mp = mp
        options = vision.ImageSegmenterOptions(
            base_options=BaseOptions(model_asset_path=str(MODELS_DIR / "selfie_multiclass_256x256.tflite")),
            output_confidence_masks=True,
            output_category_mask=False,
        )
        self._segmenter = vision.ImageSegmenter.create_from_options(options)
        self._prev = None
        self._smoothing = smoothing

    def __call__(self, rgb):
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        masks = self._segmenter.segment(image).confidence_masks
        person = 1.0 - masks[0].numpy_view().astype(np.float32)  # kategori 0 = background
        if person.ndim == 3:
            person = person[..., 0]
        if person.shape != rgb.shape[:2]:
            person = cv2.resize(person, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_LINEAR)
        guide = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        radius = max(4, rgb.shape[1] // 120)
        person = cv2.ximgproc.guidedFilter(guide, person, radius, 1e-3)
        # Pertajam kontras alpha: bagian dalam rambut/badan jadi penuh, tepi tetap halus.
        person = np.clip((person - 0.2) / 0.5, 0.0, 1.0)
        person = person * person * (3 - 2 * person)
        if self._prev is not None:
            person = self._smoothing * self._prev + (1 - self._smoothing) * person
        self._prev = person
        return person

    def reset(self):
        self._prev = None

    def close(self):
        self._segmenter.close()
