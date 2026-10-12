"""Cek safe zone Reels frame per frame.

Layer caption dan judul dirender sendiri di atas hitam (resolusi 1/4), lalu setiap frame
dicek: apakah ada piksel teks di area UI Instagram, dan apakah caption mengenai wajah.
Kartu insert dicek dari geometrinya. Hasilnya berupa laporan teks dan gambar preview
dengan area terlarang diarsir.
"""
import numpy as np
from PIL import Image, ImageDraw

from vision import read_frames

# Area UI Reels pada frame 9:16 (relatif): header atas, teks/username bawah, kolom tombol kanan.
ZONES = {
    "atas (header Reels)": (0.0, 0.0, 1.0, 0.10),
    "bawah (username & caption IG)": (0.0, 0.80, 1.0, 1.0),
    "kanan (tombol like/komentar/share)": (0.86, 0.55, 1.0, 0.80),
}
SCALE = 4
TEXT_THRESHOLD = 60


def _zone_masks(w, h):
    masks = {}
    for name, (x0, y0, x1, y1) in ZONES.items():
        m = np.zeros((h, w), bool)
        m[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = True
        masks[name] = m
    return masks


def _ranges(frames, fps):
    """Gabungkan nomor frame berurutan menjadi rentang detik."""
    out = []
    for f in sorted(frames):
        if out and f - out[-1][1] <= 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return [(a / fps, (b + 1) / fps) for a, b in out]


def _scan(ass_files, total, size, fps, cwd):
    w, h = size[0] // SCALE // 2 * 2, size[1] // SCALE // 2 * 2
    vf = ",".join([f"ass={a}" for a in ass_files] + [f"scale={w}:{h}"])
    args = ["-f", "lavfi", "-i", f"color=c=black:s={size[0]}x{size[1]}:r={fps}:d={total:.3f}", "-vf", vf]
    for frame in read_frames(args, w, h, cwd=cwd):
        yield frame.max(axis=2) > TEXT_THRESHOLD


def check(wdir, name, segments, inserts, total, size, fps, has_titles):
    """Kembalikan daftar masalah (string). Kosong berarti aman."""
    from framing import face_at

    issues = []
    layers = [("caption", [f"output/{name}.ass"], True)]
    if has_titles:
        layers.append(("judul", [f"output/{name}.titles.ass"], False))
    w, h = size[0] // SCALE // 2 * 2, size[1] // SCALE // 2 * 2
    zones = _zone_masks(w, h)
    for label, files, check_face in layers:
        if not (wdir / files[0]).exists():
            continue
        hits = {z: set() for z in zones}
        face_hits = set()
        for i, text in enumerate(_scan(files, total, size, fps, wdir)):
            if not text.any():
                continue
            for z, m in zones.items():
                if (text & m).any():
                    hits[z].add(i)
            if check_face:
                face = face_at(segments, i / fps)
                if face:
                    cx, cy, fw, fh = face
                    x0, x1 = int((cx - fw * 0.4) * w), int((cx + fw * 0.4) * w)
                    y0, y1 = int((cy - fh * 0.4) * h), int((cy + fh * 0.4) * h)
                    if text[max(0, y0):y1, max(0, x0):x1].any():
                        face_hits.add(i)
        for z, frames in hits.items():
            for a, b in _ranges(frames, fps):
                issues.append(f"{label} masuk area {z} di {a:.2f}-{b:.2f}s")
        for a, b in _ranges(face_hits, fps):
            issues.append(f"{label} menutupi wajah di {a:.2f}-{b:.2f}s")

    from inserts import CARD_BOX

    for ins in inserts:
        if ins["type"] in ("video",) or ins.get("layout") == "full":
            continue
        x0, y0, x1, y1 = CARD_BOX
        for z, (zx0, zy0, zx1, zy1) in ZONES.items():
            if x0 < zx1 and x1 > zx0 and y0 < zy1 and y1 > zy0:
                issues.append(f"insert {ins['type']} di {ins['start']:.2f}s masuk area {z}")
    return issues


def preview(video_path, out_png, segments, total, every=1.5):
    """Contact sheet frame hasil render dengan area UI Reels diarsir merah dan kotak wajah hijau."""
    from framing import face_at

    tw, th = 216, 384
    times = list(np.arange(0.5, max(0.6, total - 0.2), every))[:10]
    tiles = []
    for t in times:
        frame = next(read_frames(["-ss", f"{t:.3f}", "-i", video_path, "-frames:v", "1", "-vf", f"scale={tw}:{th}"],
                                 tw, th), None)
        if frame is None:
            continue
        img = Image.fromarray(frame).convert("RGBA")
        over = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(over)
        for x0, y0, x1, y1 in ZONES.values():
            draw.rectangle((x0 * tw, y0 * th, x1 * tw, y1 * th), fill=(255, 40, 40, 70))
        face = face_at(segments, t)
        if face:
            cx, cy, fw, fh = face
            draw.rectangle(((cx - fw / 2) * tw, (cy - fh / 2) * th, (cx + fw / 2) * tw, (cy + fh / 2) * th),
                           outline=(60, 255, 120, 255), width=2)
        draw.text((6, 6), f"{t:.1f}s", fill=(255, 255, 255, 255))
        tiles.append(Image.alpha_composite(img, over))
    if not tiles:
        return
    sheet = Image.new("RGB", (tw * len(tiles), th), "black")
    for i, tile in enumerate(tiles):
        sheet.paste(tile.convert("RGB"), (i * tw, 0))
    sheet.save(out_png)
