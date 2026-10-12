"""Kata besar di belakang kepala: teks diletakkan di antara background dan orang.

Untuk setiap judul, frame pada rentangnya diambil dari video yang sudah dipotong,
lalu matte orang dihitung. Posisi vertikal dipilih sedekat mungkin dengan puncak kepala
tanpa menutupi huruf terlalu banyak. Hasilnya berupa file ASS untuk teks dan video
mask grayscale sepanjang reel (hitam di luar judul) yang dipakai ffmpeg untuk
menaruh orang kembali di atas teks.
"""
import subprocess

import cv2
import numpy as np

from common import ass_color, ass_escape, ass_time, ffmpeg_exe
from framing import face_at
from vision import PersonMatte, read_frames

GLYPH_WIDTH = 0.72  # perkiraan lebar rata-rata huruf kapital tebal, relatif terhadap ukuran font
CAP_HEIGHT = 0.78
TOP_SAFE = 0.12  # area atas Reels (nama akun, tombol) dihindari


def resolve_titles(plan, total, word_time):
    out = []
    for n, title in enumerate(plan["titles"]):
        start = word_time(title.get("at"), f"titles[{n}].at")
        dur = float(title.get("dur", plan["title_style"]["dur"]))
        out.append({**title, "start": start, "end": min(total, start + dur)})
    return out


def _font_size(text, style, W):
    return min(style["size"], 0.88 * W / (GLYPH_WIDTH * max(1, len(text))))


def _head_top(occupancy, face, W, H):
    """Baris paling atas kepala, diukur dari matte di kolom selebar wajah."""
    if face:
        x0, x1 = int((face[0] - face[2] / 2) * W), int((face[0] + face[2] / 2) * W)
    else:
        x0, x1 = int(W * 0.4), int(W * 0.6)
    band = occupancy[:, max(0, x0):max(x0 + 1, x1)].mean(axis=1)
    rows = np.nonzero(band > 0.5)[0]
    return float(rows[0]) if len(rows) else 0.3 * H


def _place(occupancy, face, size, style, W, H):
    """Pilih y tengah teks (koordinat PlayRes). occupancy: rata-rata matte, sudah diskalakan ke HxW.

    Target: kepala menutupi bagian bawah huruf sebesar style['overlap'] dari tinggi huruf,
    tapi total huruf yang tertutup tidak lebih dari style['max_cover'].
    """
    box_h = size * CAP_HEIGHT
    box_w = min(W * 0.9, GLYPH_WIDTH * size * style["chars"])
    x0, x1 = int((W - box_w) / 2), int((W + box_w) / 2)
    target = _head_top(occupancy, face, W, H) - box_h / 2 + style["overlap"] * box_h
    lo = TOP_SAFE * H + box_h / 2
    hi = 0.62 * H
    scored = []
    for y in np.arange(lo, hi, H * 0.005):
        y0, y1 = int(y - box_h / 2), int(y + box_h / 2)
        occ = float(occupancy[y0:y1, x0:x1].mean()) if y1 > y0 else 1.0
        scored.append((occ, abs(y - target), y))
    ok = [s for s in scored if s[0] <= style["max_cover"]]
    best = min(ok, key=lambda s: s[1]) if ok else min(scored)
    return best[2], best[0]


def render_title_layers(titles, plan, segments, concat_path, out_size, total_frames, ass_path, mask_path):
    W, H = plan["output"]["width"], plan["output"]["height"]
    fps = plan["output"]["fps"]
    style = plan["title_style"]
    mw, mh = out_size[0] // 4 * 2, out_size[1] // 4 * 2  # mask setengah resolusi output
    matte = PersonMatte()
    masks = {}
    events = []
    try:
        for title in titles:
            text = title["text"].upper() if style["uppercase"] else title["text"]
            f0, f1 = round(title["start"] * fps), min(total_frames, round(title["end"] * fps))
            args = ["-f", "concat", "-safe", "0", "-i", concat_path,
                    "-vf", f"trim=start_frame={f0}:end_frame={f1},setpts=PTS-STARTPTS,scale={mw}:{mh}"]
            matte.reset()
            frames = [matte(frame) for frame in read_frames(args, mw, mh)]
            if not frames:
                continue
            for k, m in enumerate(frames):
                masks[f0 + k] = (m * 255).astype(np.uint8)
            occupancy = cv2.resize(np.mean(frames, axis=0), (W, H), interpolation=cv2.INTER_LINEAR)
            size = _font_size(text, style, W)
            y, cover = _place(occupancy, face_at(segments, title["start"]), size, {**style, "chars": len(text)}, W, H)
            title["y"], title["cover"], title["size"] = y, cover, size
            events.append((title, text))
    finally:
        matte.close()

    ass_path.write_text(_ass(events, style, W, H), encoding="utf-8")
    _write_mask_video(masks, total_frames, (mw, mh), fps, mask_path)


def _ass(events, style, W, H):
    primary = ass_color(style["color"])
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Title,{style['font']},100,{primary},{primary},&H00000000,&H90000000,-1,"
        f"{-1 if style.get('italic') else 0},0,0,100,100,0,0,1,0,"
        f"{style['shadow']},5,0,0,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for title, text in events:
        size = round(title["size"])
        tags = (f"{{\\pos({W // 2},{round(title['y'])})\\fs{size}\\fad(60,120)"
                f"\\fscx82\\fscy82\\t(0,160,\\fscx100\\fscy100)}}")
        lines.append(f"Dialogue: 0,{ass_time(title['start'])},{ass_time(title['end'])},Title,,0,0,0,,"
                     f"{tags}{ass_escape(text)}")
    return "\n".join(lines) + "\n"


def _write_mask_video(masks, total_frames, size, fps, path):
    mw, mh = size
    black = bytes(mw * mh)
    cmd = [ffmpeg_exe(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{mw}x{mh}",
           "-r", str(fps), "-i", "-", "-c:v", "ffv1", "-pix_fmt", "gray", str(path)]
    with subprocess.Popen(cmd, stdin=subprocess.PIPE) as proc:
        for i in range(total_frames):
            m = masks.get(i)
            proc.stdin.write(m.tobytes() if m is not None else black)
        proc.stdin.close()
        if proc.wait() != 0:
            raise SystemExit("ffmpeg gagal menulis mask judul")
