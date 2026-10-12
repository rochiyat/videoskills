"""Insert animasi di atas reel: kartu gambar, file yang di-scroll, kartu Follow, dan B-roll video.

Setiap insert dirender sekali ke _edit/inserts/<hash>.mkv (ffv1 dengan alpha untuk kartu,
yuv420p untuk B-roll layar penuh), lalu ditumpuk oleh render.py pada waktunya.
Semua ukuran dihitung di koordinat 1080x1920 lalu diskalakan ke ukuran output.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from common import ffmpeg_exe, run_ffmpeg

REF_W, REF_H = 1080, 1920
CARD_BOX = (0.12, 0.13, 0.88, 0.53)  # x0, y0, x1, y1 relatif: di atas caption, kiri dari tombol Reels
CARD_RADIUS = 36
ENTER, EXIT = 0.22, 0.16

FONT_CANDIDATES = {
    "bold": ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"],
    "regular": ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"],
}
FONT_DIRS = [Path("C:/Windows/Fonts"), Path("/System/Library/Fonts/Supplemental"), Path("/Library/Fonts"),
             Path("/System/Library/Fonts"), Path("/usr/share/fonts/truetype/dejavu"),
             Path("/usr/share/fonts/truetype/liberation")]

SFX_FOR = {"image": "pop", "scroll": "pop", "cta": "pop", "video": "whoosh"}


def font(kind, size):
    for name in FONT_CANDIDATES[kind]:
        for d in FONT_DIRS:
            if (d / name).exists():
                return ImageFont.truetype(str(d / name), size)
    return ImageFont.load_default(size)


def ease_out_back(x):
    c = 1.4
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def ease_in_out(x):
    return 3 * x * x - 2 * x * x * x


# ---------- elemen kartu ----------


def rounded(img, radius):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *img.size), radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def with_shadow(card, blur=28, offset=14, opacity=110):
    pad = blur * 2
    canvas = Image.new("RGBA", (card.width + pad * 2, card.height + pad * 2 + offset), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    alpha = card.getchannel("A").point(lambda a: a * opacity // 255)
    shadow.paste((0, 0, 0, 255), (pad, pad + offset), alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(card, (pad, pad))
    return canvas, pad


def fit_card(src: Path):
    x0, y0, x1, y1 = CARD_BOX
    max_w, max_h = (x1 - x0) * REF_W, (y1 - y0) * REF_H
    img = Image.open(src).convert("RGB")
    scale = min(max_w / img.width, max_h / img.height)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    return rounded(img, CARD_RADIUS)


def cta_card(spec):
    w, h = round(REF_W * 0.76), 250
    card = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(card)
    av = 150
    ax, ay = 44, (h - av) // 2
    if spec.get("avatar"):
        avatar = Image.open(spec["avatar"]).convert("RGB")
        side = min(avatar.size)
        avatar = avatar.crop(((avatar.width - side) // 2, (avatar.height - side) // 2,
                              (avatar.width + side) // 2, (avatar.height + side) // 2)).resize((av, av), Image.LANCZOS)
        mask = Image.new("L", (av, av), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, av, av), fill=255)
        card.paste(avatar, (ax, ay), mask)
    else:
        draw.ellipse((ax, ay, ax + av, ay + av), fill=(224, 70, 120))
        initial = (spec.get("name") or "?")[0].upper()
        f = font("bold", 76)
        draw.text((ax + av / 2, ay + av / 2), initial, font=f, fill="white", anchor="mm")
    tx = ax + av + 32
    draw.text((tx, h / 2 - 8), spec.get("name", ""), font=font("bold", 46), fill=(20, 20, 20), anchor="ls")
    draw.text((tx, h / 2 + 46), spec.get("handle", ""), font=font("regular", 36), fill=(120, 120, 120), anchor="ls")
    return rounded(card, CARD_RADIUS)


def follow_button(pressed):
    w, h = 230, 84
    btn = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(btn)
    fill = (239, 239, 239) if pressed else (0, 149, 246)
    draw.rounded_rectangle((0, 0, w, h), 18, fill=fill)
    draw.text((w / 2, h / 2), "Following" if pressed else "Follow", font=font("bold", 36),
              fill=(20, 20, 20) if pressed else "white", anchor="mm")
    return btn


# ---------- render frame ----------


def card_frames(card, n, fps, size, extra=None):
    """Kartu masuk dengan scale-pop, diam, lalu memudar. extra(i, t, canvas_ref) boleh menggambar tambahan."""
    out_w, out_h = size
    sx = out_w / REF_W
    x0, y0, x1, _ = CARD_BOX
    shadowed, pad = with_shadow(card)
    cx = (x0 + x1) / 2 * REF_W
    top = y0 * REF_H
    dur = n / fps
    for i in range(n):
        t = i / fps
        if t < ENTER:
            p = t / ENTER
            scale, alpha = 0.86 + 0.14 * ease_out_back(p), min(1.0, p * 1.6)
        elif t > dur - EXIT:
            p = (dur - t) / EXIT
            scale, alpha = 0.97 + 0.03 * p, max(0.0, p)
        else:
            scale, alpha = 1.0, 1.0
        layer = shadowed if extra is None else extra(t, shadowed.copy(), pad)
        s = scale * sx
        frame_img = layer.resize((max(1, round(layer.width * s)), max(1, round(layer.height * s))), Image.BILINEAR)
        if alpha < 1:
            a = frame_img.getchannel("A").point(lambda v, k=alpha: int(v * k))
            frame_img.putalpha(a)
        canvas = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
        px = round(cx * sx - frame_img.width / 2)
        py = round((top - pad) * sx + (card.height + 2 * pad) * sx * (1 - scale) / 2)
        canvas.alpha_composite(frame_img, (px, py))
        yield np.asarray(canvas)


def scroll_frames(spec, n, fps, size):
    x0, y0, x1, y1 = CARD_BOX
    vw, vh = round((x1 - x0) * REF_W), round((y1 - y0 + 0.05) * REF_H)
    tall = Image.open(spec["src"]).convert("RGB")
    tall = tall.resize((vw, max(vh, round(tall.height * vw / tall.width))), Image.LANCZOS)
    travel = tall.height - vh
    dur = n / fps
    hold = min(0.4, dur * 0.15)

    def draw(t, layer, pad):
        p = 0.0 if t < ENTER + hold else min(1.0, (t - ENTER - hold) / max(0.01, dur - ENTER - EXIT - 2 * hold))
        offset = round(travel * ease_in_out(p))
        view = rounded(tall.crop((0, offset, vw, offset + vh)), CARD_RADIUS)
        layer.alpha_composite(view, (pad, pad))
        return layer

    blank = rounded(Image.new("RGB", (vw, vh), (255, 255, 255)), CARD_RADIUS)
    return card_frames(blank, n, fps, size, extra=draw)


def cta_frames(spec, n, fps, size):
    card = cta_card(spec)
    press_at = spec.get("press_at", 0.55) * n / fps

    def draw(t, layer, pad):
        btn = follow_button(t >= press_at)
        bump = 1.0
        if press_at <= t < press_at + 0.15:
            bump = 0.9 + 0.1 * (t - press_at) / 0.15
        if bump != 1.0:
            btn = btn.resize((round(btn.width * bump), round(btn.height * bump)), Image.BILINEAR)
        bx = pad + card.width - 44 - btn.width
        by = pad + (card.height - btn.height) // 2
        layer.alpha_composite(btn, (bx, by))
        return layer

    return card_frames(card, n, fps, size, extra=draw)


# ---------- API ----------


def insert_key(spec, size, fps):
    src = spec.get("src")
    stamp = Path(src).stat().st_mtime if src and Path(src).exists() else None
    payload = [spec, stamp, size, fps, 3]
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def render_insert(spec, n, fps, size, dest: Path):
    """Render satu insert sepanjang n frame. Mengembalikan True kalau punya alpha (overlay kartu)."""
    kind = spec["type"]
    if kind == "video":
        w, h = size
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},"
              f"tpad=stop_mode=clone:stop={n}")
        run_ffmpeg(["-ss", spec.get("src_start", 0), "-i", spec["src"], "-an", "-vf", vf, "-frames:v", n,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", dest])
        return False
    if kind == "image" and spec.get("layout") == "full":
        w, h = size
        vf = (f"scale={w * 1.06:.0f}:{h * 1.06:.0f}:force_original_aspect_ratio=increase,"
              f"zoompan=z='1.06-0.06*on/{n}':d={n}:s={w}x{h}:fps={fps}")
        run_ffmpeg(["-loop", "1", "-i", spec["src"], "-vf", vf, "-frames:v", n,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", dest])
        return False
    if kind == "image":
        frames = card_frames(fit_card(Path(spec["src"])), n, fps, size)
    elif kind == "scroll":
        frames = scroll_frames(spec, n, fps, size)
    elif kind == "cta":
        frames = cta_frames(spec, n, fps, size)
    else:
        sys.exit(f"plan.json: insert type '{kind}' tidak dikenal (image, scroll, cta, video)")
    w, h = size
    cmd = [ffmpeg_exe(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", str(fps),
           "-i", "-", "-c:v", "ffv1", "-pix_fmt", "yuva420p", str(dest)]
    with subprocess.Popen(cmd, stdin=subprocess.PIPE) as proc:
        for frame in frames:
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            sys.exit(f"ffmpeg gagal merender insert {spec}")
    return True


def resolve_inserts(plan, segments, total, word_time):
    out = []
    for n, spec in enumerate(plan["inserts"]):
        if spec.get("type") not in ("image", "scroll", "cta", "video"):
            sys.exit(f"plan.json: inserts[{n}].type harus image, scroll, cta, atau video")
        if spec["type"] != "cta" and not Path(spec.get("src", "")).is_file():
            sys.exit(f"plan.json: inserts[{n}].src tidak ditemukan: {spec.get('src')}")
        if "start" in spec or "end" in spec:
            sys.exit(f"plan.json: inserts[{n}] pakai 'at'/'until'/'dur' untuk waktu, dan 'src_start' untuk detik awal B-roll")
        start = word_time(spec.get("at"), f"inserts[{n}].at")
        if "until" in spec:
            end = word_time(spec["until"], f"inserts[{n}].until", end=True)
        else:
            end = start + float(spec.get("dur", 2.5))
        out.append({**spec, "start": start, "end": min(total, end)})
    return out
