"""Ambil beberapa frame dari video dan susun berjajar dalam satu PNG, untuk dicek secara visual.

    python snapshot.py <video.mp4> <detik> [<detik> ...] [-o strip.png] [--width 360]
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw

from common import oriented_size
from vision import read_frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("times", nargs="+", type=float)
    ap.add_argument("-o", "--out")
    ap.add_argument("--width", type=int, default=360)
    args = ap.parse_args()

    src_w, src_h = oriented_size(Path(args.video))
    w = args.width // 2 * 2
    h = round(src_h * w / src_w / 2) * 2
    tiles = []
    for t in args.times:
        frame = next(read_frames(["-ss", f"{t:.3f}", "-i", args.video, "-frames:v", "1", "-vf", f"scale={w}:{h}"],
                                 w, h), None)
        if frame is None:
            print(f"{t:.2f}s: di luar durasi video, dilewati")
            continue
        tile = Image.fromarray(frame)
        ImageDraw.Draw(tile).text((8, 8), f"{t:.2f}s", fill="white", stroke_width=2, stroke_fill="black")
        tiles.append(tile)
    if not tiles:
        raise SystemExit("Tidak ada frame yang bisa diambil.")
    sheet = Image.new("RGB", (w * len(tiles), h), "black")
    for i, tile in enumerate(tiles):
        sheet.paste(tile, (i * w, 0))
    out = Path(args.out) if args.out else Path(args.video).with_suffix(".snapshot.png")
    sheet.save(out)
    print(out)


if __name__ == "__main__":
    main()
