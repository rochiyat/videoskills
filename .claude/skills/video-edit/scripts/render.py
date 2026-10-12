"""Render plan.json menjadi reel 9:16.

    python render.py <folder_klip>             render penuh -> _edit/output/vN.mp4
    python render.py <folder_klip> --draft     540x960, cepat, untuk cek alur
    python render.py <folder_klip> --dry-run   cetak naskah hasil potongan, tanpa render

Lihat SKILL.md untuk format plan.json.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import (
    SKILL_DIR,
    ass_color,
    ass_escape,
    ass_time,
    face_cache,
    read_json,
    resolve_input,
    run_ffmpeg,
    work_dir,
    write_json,
)
from framing import assign_zoom, compute_crops
from styles import build_plan, grade_filter

DEFAULTS = {
    "look": "bold",
    "cut": {"max_gap": 0.35, "pad_before": 0.08, "pad_after": 0.15},
    "output": {"width": 1080, "height": 1920, "fps": 30},
    "framing": {},
    "zoom": {"auto": True, "punch": None, "max": 1.9},
    "titles": [],
    "title_style": {"size": 300, "dur": 1.4, "overlap": 0.3, "max_cover": 0.2},
    "captions": {
        "enabled": True,
        "max_words": 3,
        "max_chars": 18,
        "position": 0.70,
    },
    "grade": {"contrast": 1.0, "saturation": 1.0, "warmth": 0.0},
    "inserts": [],
    "sfx": {"enabled": True, "volume_db": -12, "titles": True, "inserts": True, "cuts": False, "events": []},
    "audio": {"loudnorm": True, "target_lufs": -14},
    "safe_zone": {"check": True},
    "replacements": {},
    "word_fix": {},
    "keep": [],
}

QUALITY = {
    "final": {"scale": 1.0, "seg_preset": "fast", "seg_crf": 16, "preset": "fast", "crf": 20},
    "draft": {"scale": 0.5, "seg_preset": "ultrafast", "seg_crf": 26, "preset": "ultrafast", "crf": 28},
}

MIN_CAPTION_MARGIN = 0.20  # caption tidak boleh lebih rendah dari 80% tinggi (area tombol/caption Reels)
CAPTION_SIDE_MARGIN = 0.14  # caption tidak sampai ke kolom tombol di kanan
SENTENCE_BREAK = re.compile(r"[.,!?;:]$")
EDGE_PUNCT = re.compile(r"^([\"'(\[]*)(.*?)([\"')\].,!?;:]*)$")
FADE = 0.012
ENV_HOP = 0.01  # detik per frame envelope
WINDOW_SLACK = 0.3  # detik; timestamp Whisper boleh meleset sejauh ini


def fail(msg):
    sys.exit(f"plan.json: {msg}")


# ---------- plan -> segmen ----------


def caption_text(cid, word, plan):
    key = f"{cid}:{word['i']}"
    if key in plan["word_fix"]:
        return plan["word_fix"][key]
    lead, core, trail = EDGE_PUNCT.match(word["w"]).groups()
    for wrong, right in plan["replacements"].items():
        if core.lower() == wrong.lower():
            core = right
            break
    return f"{lead}{core}{trail}"


def envelope_path(wdir, cid, meta):
    return wdir / "envelopes" / f"{cid}-{meta['fingerprint'].replace(':', '-')}.npy"


def load_envelope(path: Path, cache: Path):
    """Level audio (dBFS) per 10 ms, mono 16 kHz. Di-cache per klip."""
    import av
    import numpy as np

    if cache.exists():
        return np.load(cache)
    rate = 16000
    hop = int(rate * ENV_HOP)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
    chunks = []
    with av.open(str(path)) as container:
        if container.streams.audio:
            for frame in container.decode(audio=0):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray().reshape(-1))
    samples = np.concatenate(chunks).astype(np.float32) / 32768.0 if chunks else np.zeros(hop, np.float32)
    n = len(samples) // hop
    rms = np.sqrt(np.mean(samples[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    db = np.maximum(20 * np.log10(rms), -100).astype(np.float32)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, db)
    return db


def speech_threshold(db, override):
    import numpy as np

    if override is not None:
        return float(override)
    floor, peak = np.percentile(db, 10), np.percentile(db, 95)
    return max(floor + 6, floor + 0.35 * (peak - floor))


def active_regions(db, lo, hi, thresh, max_gap):
    """Rentang [s, e] (detik) dengan suara di atas ambang, jeda <= max_gap digabung."""
    a, b = max(0, int(lo / ENV_HOP)), min(len(db), int(hi / ENV_HOP) + 1)
    regions = []
    for i in range(a, b):
        if db[i] < thresh:
            continue
        t = i * ENV_HOP
        if regions and t - regions[-1][1] <= max_gap:
            regions[-1][1] = t + ENV_HOP
        else:
            regions.append([t, t + ENV_HOP])
    return regions


def word_spans(chosen, words, lo, hi, db, thresh, cut, duration):
    """Potong rentang kata menjadi span bersuara; jeda panjang di dalamnya dibuang."""
    pad_b, pad_a = cut["pad_before"], cut["pad_after"]
    max_gap = max(cut["max_gap"], pad_b + pad_a)
    left = max(chosen[0]["s"] - WINDOW_SLACK, words[lo - 1]["e"] if lo > 0 else 0.0, 0.0)
    right = min(chosen[-1]["e"] + WINDOW_SLACK, words[hi + 1]["s"] if hi + 1 < len(words) else duration, duration)

    regions = [
        r for r in active_regions(db, left, right, thresh, max_gap)
        if any(w["s"] < r[1] and w["e"] > r[0] for w in chosen)
    ]
    if not regions:  # audio terlalu pelan untuk dianalisis: pakai timestamp Whisper saja
        regions = [[chosen[0]["s"], chosen[-1]["e"]]]

    spans = [(max(left, rs - pad_b), min(right, re_ + pad_a), []) for rs, re_ in regions]
    for w in chosen:
        # Whisper sering menaruh awal kata pertama setelah jeda terlalu dini,
        # jadi kata masuk ke span yang paling banyak tumpang-tindih (atau terdekat).
        def overlap(sp):
            return min(w["e"], sp[1]) - max(w["s"], sp[0])

        max(spans, key=overlap)[2].append(w)
    return spans


def build_segments(plan, clips, transcripts, input_dir, wdir):
    cut = plan["cut"]
    fps = plan["output"]["fps"]

    segments = []
    for n, item in enumerate(plan["keep"]):
        cid = item.get("clip")
        if cid not in clips:
            fail(f"keep[{n}]: clip '{cid}' tidak ada (pilihan: {', '.join(sorted(clips))})")
        words = transcripts[cid]["words"]
        duration = clips[cid]["duration"]

        if "from" in item:
            lo, hi = item["from"], item.get("to", item["from"])
            if not (0 <= lo <= hi < len(words)):
                fail(f"keep[{n}]: rentang kata {lo}-{hi} di luar 0-{len(words) - 1} untuk {cid}")
            db = load_envelope(input_dir / clips[cid]["file"], envelope_path(wdir, cid, clips[cid]))
            thresh = speech_threshold(db, cut.get("silence_db"))
            spans = word_spans(words[lo : hi + 1], words, lo, hi, db, thresh, cut, duration)
        elif "start" in item and "end" in item:
            s, e = float(item["start"]), float(item["end"])
            spans = [(s, e, [w for w in words if w["e"] > s and w["s"] < e])]
        else:
            fail(f"keep[{n}]: butuh 'from'/'to' (index kata) atau 'start'/'end' (detik)")

        for start, end, group in spans:
            start = round(max(0.0, start) * fps) / fps
            end = round(min(duration, end) * fps) / fps
            frames = round((end - start) * fps)
            if frames < 2:
                continue
            segments.append({"clip": cid, "start": start, "frames": frames, "words": group, "keep": n,
                             "look": item.get("look", plan["look"]),
                             "has_audio": clips[cid].get("has_audio", True)})
    if not segments:
        fail("tidak ada segmen yang dipakai. Isi 'keep' dulu.")
    return segments


def timeline_words(segments, plan):
    fps = plan["output"]["fps"]
    out, offset, prev_keep = [], 0.0, None
    for seg in segments:
        dur = seg["frames"] / fps
        seg["out"], seg["out_end"] = offset, offset + dur
        new_keep = seg["keep"] != prev_keep
        for w in seg["words"]:
            text = caption_text(seg["clip"], w, plan)
            if not text.strip():
                continue
            s = offset + max(w["s"], seg["start"]) - seg["start"]
            e = offset + min(w["e"], seg["start"] + dur) - seg["start"]
            if e > s:
                out.append({"text": text, "s": s, "e": e, "break": new_keep, "look": seg["look"]})
                new_keep = False
        offset += dur
        prev_keep = seg["keep"]
    return out, offset


def make_word_time(segments):
    """Fungsi yang mengubah 'clip:index' menjadi waktu output (awal kata, atau akhir kalau end=True)."""

    def word_time(ref, where, end=False):
        cid, _, idx = str(ref or "").partition(":")
        for seg in segments:
            if seg["clip"] != cid:
                continue
            word = next((w for w in seg["words"] if str(w["i"]) == idx), None)
            if word:
                seg_len = seg["out_end"] - seg["out"]
                t = min(max(word["e"] if end else word["s"], seg["start"]), seg["start"] + seg_len)
                return seg["out"] + t - seg["start"]
        fail(f"{where} '{ref}' bukan kata yang dipakai di reel (format 'c1:23')")

    return word_time


# ---------- caption ASS ----------


def chunk_words(words, cap):
    chunks, current, chars = [], [], 0
    for w in words:
        gap = w["s"] - current[-1]["e"] if current else 0
        too_long = current and (len(current) >= cap["max_words"] or chars + 1 + len(w["text"]) > cap["max_chars"])
        new_look = current and w["look"] != current[-1]["look"]
        if current and (too_long or gap > 0.5 or w["break"] or new_look or SENTENCE_BREAK.search(current[-1]["text"])):
            chunks.append(current)
            current, chars = [], 0
        current.append(w)
        chars += len(w["text"]) + (1 if chars else 0)
    if current:
        chunks.append(current)
    return chunks


def caption_margin(chunk, segments, cap, W, H, default):
    """MarginV untuk satu potongan caption: turunkan caption kalau akan menutupi wajah."""
    line_h = cap["size"] * 1.25
    usable = W * (1 - 2 * CAPTION_SIDE_MARGIN)
    lines = 1 + int(sum(len(w["text"]) + 1 for w in chunk) * cap["size"] * 0.6 > usable)
    bottoms = [
        (seg["face"][1] + seg["face"][3] / 2) * H
        for seg in segments
        if seg.get("face") and seg["out"] < chunk[-1]["e"] and seg["out_end"] > chunk[0]["s"]
    ]
    if not bottoms:
        return default
    top_needed = max(bottoms) + H * 0.01
    margin = H - top_needed - lines * line_h
    return round(min(default, max(margin, H * MIN_CAPTION_MARGIN)))


def caption_style(name, cap, W, margin_v):
    base = ass_color(cap["color"])
    if cap["box"]:
        border, outline_color, outline, shadow = 3, "&H50000000", 14, 0
    else:
        border, outline_color, outline, shadow = 1, "&H00000000", cap["outline"], 0
    margin_h = round(W * CAPTION_SIDE_MARGIN)
    return (f"Style: {name},{cap['font']},{cap['size']},{base},{base},{outline_color},&H64000000,"
            f"{-1 if cap['bold'] else 0},{-1 if cap['italic'] else 0},0,0,100,100,0,0,{border},{outline},{shadow},"
            f"2,{margin_h},{margin_h},{margin_v},1")


def build_ass(words, plan, total, segments):
    W, H = plan["output"]["width"], plan["output"]["height"]
    main_cap = plan["captions"]
    margin_v = round(H * (1 - main_cap["position"]))
    caps = {look: cfg["captions"] for look, cfg in plan["looks"].items()}

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {W}",
        f"PlayResY: {H}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        *[caption_style(f"Cap_{look}", cap, W, margin_v) for look, cap in caps.items()],
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    chunks = chunk_words(words, main_cap)
    for ci, chunk in enumerate(chunks):
        look = chunk[0]["look"]
        cap = caps[look]
        base, hl = ass_color(cap["color"]), ass_color(cap["highlight"])
        next_start = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else total
        end = chunk[-1]["e"]
        end = next_start if next_start - end < 0.4 else min(end + 0.15, next_start)
        texts = [ass_escape(_display(w["text"], cap)) for w in chunk]
        margin = caption_margin(chunk, segments, cap, W, H, margin_v)
        for k, w in enumerate(chunk):
            ev_start = chunk[0]["s"] if k == 0 else w["s"]
            ev_end = chunk[k + 1]["s"] if k + 1 < len(chunk) else end
            if ev_end <= ev_start:
                continue
            parts = [f"{{\\c{hl}&}}{t}{{\\c{base}&}}" if j == k else t for j, t in enumerate(texts)]
            lines.append(f"Dialogue: 0,{ass_time(ev_start)},{ass_time(ev_end)},Cap_{look},,0,0,{margin},,"
                         f"{' '.join(parts)}")
    return "\n".join(lines) + "\n"


def _display(text, cap):
    text = text.rstrip(".,;:")
    return text.upper() if cap["uppercase"] else text


# ---------- ffmpeg ----------


def segment_key(seg, clips, plan, q):
    meta = clips[seg["clip"]]
    grade = plan["looks"][seg["look"]]["grade"]
    payload = [meta["file"], meta.get("fingerprint"), seg["start"], seg["frames"], plan["output"], q, seg["crop"], grade]
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def extract_segment(seg, src, dest, plan, q, size):
    fps = plan["output"]["fps"]
    w, h = size
    dur = seg["frames"] / fps
    cw, ch, x, y = seg["crop"]
    grade = grade_filter(plan["looks"][seg["look"]]["grade"])
    vf = f"crop={cw}:{ch}:{x}:{y},scale={w}:{h}:flags=lanczos,setsar=1,fps={fps}" + (f",{grade}" if grade else "")
    af = (f"aresample=48000,afade=t=in:d={FADE},afade=t=out:st={max(0, dur - FADE):.3f}:d={FADE},"
          f"apad,atrim=duration={dur:.6f}")
    tmp = dest.with_suffix(".tmp.mkv")
    silent = [] if seg["has_audio"] else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    run_ffmpeg([
        "-ss", f"{seg['start']:.6f}", "-i", src, *silent, "-t", f"{dur:.6f}",
        "-map", "0:v:0", "-map", "1:a:0" if silent else "0:a:0",
        "-vf", vf, "-af", af, "-frames:v", seg["frames"], "-ac", "2",
        "-c:v", "libx264", "-preset", q["seg_preset"], "-crf", q["seg_crf"], "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le", tmp,
    ])
    tmp.replace(dest)


def next_version(out_dir: Path) -> int:
    nums = [int(m.group(1)) for p in out_dir.glob("v*.mp4") if (m := re.fullmatch(r"v(\d+)", p.stem))]
    return max(nums, default=0) + 1


def sfx_events(plan, segments, titles, inserts, word_time):
    cfg = plan["sfx"]
    events = []
    if cfg["titles"]:
        events += [(t["start"], "pop") for t in titles]
    if cfg["inserts"]:
        from inserts import SFX_FOR

        for ins in inserts:
            sound = ins.get("sfx", SFX_FOR[ins["type"]])
            if sound:
                events.append((ins["start"], sound))
            if ins["type"] == "cta":
                events.append((ins["start"] + ins.get("press_at", 0.55) * (ins["end"] - ins["start"]), "tick"))
            if ins["type"] == "video" or ins.get("layout") == "full":
                events.append((ins["end"], "whoosh"))
    if cfg["cuts"]:
        events += [(s["out"], "whoosh") for p, s in zip(segments, segments[1:]) if s["zoom"] != p["zoom"]]
    for n, ev in enumerate(cfg["events"]):
        events.append((word_time(ev.get("at"), f"sfx.events[{n}].at"), ev.get("sound", "ding")))
    return events


# ---------- main ----------


def print_dry_run(segments, total, plan, clips, titles, inserts):
    by_keep = {}
    for seg in segments:
        by_keep.setdefault(seg["keep"], []).append(seg)
    print("Naskah hasil potongan (/ = jeda dibuang):\n")
    for n, segs in by_keep.items():
        item = plan["keep"][n]
        text = " / ".join(" ".join(caption_text(s["clip"], w, plan) for w in s["words"]) for s in segs)
        note = f"  ({item['note']})" if item.get("note") else ""
        look = f"  [look {item['look']}]" if item.get("look") else ""
        print(f"[{n}] {item['clip']}{note}{look}\n    {text}\n")
    doubtful = [
        f"{s['clip']}:{w['i']}={w['w']}" for s in segments for w in s["words"]
        if w["p"] < 0.5 and f"{s['clip']}:{w['i']}" not in plan["word_fix"]
    ]
    if doubtful:
        print("Kata ragu (cek atau perbaiki di word_fix):", ", ".join(doubtful), "\n")
    print(f"Look: {plan['look']}")
    print("Segmen:")
    for seg in segments:
        face = "wajah ok" if seg.get("face") else "wajah tidak terdeteksi"
        print(f"  {seg['out']:6.2f}s  {seg['clip']}  zoom {seg['zoom']:.2f}x  {face}")
    for t in titles:
        print(f"Judul : {t['text']!r} {t['start']:.2f}-{t['end']:.2f}s")
    for ins in inserts:
        print(f"Insert: {ins['type']} {ins['start']:.2f}-{ins['end']:.2f}s {ins.get('src', ins.get('name', ''))}")
    print()
    raw = sum(clips[c]["duration"] or 0 for c in {s["clip"] for s in segments})
    print(f"Segmen: {len(segments)}  |  Durasi hasil: {total:.1f}s  |  Total klip sumber terpakai: {raw:.1f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_dir")
    ap.add_argument("--plan", help="default: <folder>/_edit/plan.json")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--jobs", type=int, default=3)
    args = ap.parse_args()

    input_dir = resolve_input(args.input_dir)
    wdir = work_dir(input_dir)
    clips = read_json(wdir / "clips.json")
    if not clips:
        sys.exit("Belum ada transkrip. Jalankan transcribe.py dulu.")
    plan_path = Path(args.plan) if args.plan else wdir / "plan.json"
    if not plan_path.exists():
        sys.exit(f"plan.json tidak ditemukan: {plan_path}")
    try:
        user_plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))  # -sig: toleran BOM dari Notepad/PowerShell
    except json.JSONDecodeError as exc:
        sys.exit(f"plan.json bukan JSON yang valid: {exc}")
    plan = build_plan(DEFAULTS, user_plan)
    fps = plan["output"]["fps"]
    transcripts = {cid: read_json(wdir / "transcripts" / f"{cid}.json", {"words": []}) for cid in clips}

    segments = build_segments(plan, clips, transcripts, input_dir, wdir)
    words, total = timeline_words(segments, plan)
    word_time = make_word_time(segments)
    for cid in sorted({s["clip"] for s in segments}):
        if not face_cache(wdir, cid, clips[cid]).exists():
            from vision import analyze_faces

            print(f"{cid}: analisis wajah...", flush=True)
            analyze_faces(input_dir / clips[cid]["file"], face_cache(wdir, cid, clips[cid]))

    titles = []
    if plan["titles"]:
        from titles import resolve_titles

        titles = resolve_titles(plan, total, word_time)
        for t in titles:
            covered = [s for s in segments if s["out"] < t["end"] and s["out_end"] > t["start"]]
            for seg in covered[1:]:
                seg["zoom_lock"] = True
    inserts = []
    if plan["inserts"]:
        from inserts import resolve_inserts

        inserts = resolve_inserts(plan, segments, total, word_time)
    assign_zoom(segments, plan, clips)
    compute_crops(segments, plan, clips, wdir)

    if args.dry_run:
        print_dry_run(segments, total, plan, clips, titles, inserts)
        return

    t0 = time.time()
    q = QUALITY["draft" if args.draft else "final"]
    size = tuple(round(plan["output"][k] * q["scale"] / 2) * 2 for k in ("width", "height"))
    seg_dir = wdir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)

    jobs = []
    for seg in segments:
        dest = seg_dir / f"{segment_key(seg, clips, plan, q)}.mkv"
        seg["file"] = dest
        if not dest.exists():
            jobs.append((seg, input_dir / clips[seg["clip"]]["file"], dest))
    print(f"{len(segments)} segmen ({len(jobs)} baru dipotong, {len(segments) - len(jobs)} dari cache)...", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for future in [pool.submit(extract_segment, s, src, d, plan, q, size) for s, src, d in jobs]:
            future.result()

    out_dir = wdir / "output"
    out_dir.mkdir(exist_ok=True)
    version = next_version(out_dir)
    name = f"v{version}{'-draft' if args.draft else ''}"

    concat = wdir / "concat.txt"
    concat.write_text("".join(f"file 'segments/{s['file'].name}'\n" for s in segments), encoding="utf-8")

    fontsdir = ""
    fonts = SKILL_DIR / "assets" / "fonts"
    if fonts.is_dir() and any(fonts.iterdir()):
        shutil.copytree(fonts, wdir / "fonts", dirs_exist_ok=True)
        fontsdir = ":fontsdir=fonts"

    inputs = ["-f", "concat", "-safe", "0", "-i", "concat.txt"]
    graph, video = [], "0:v"
    n_inputs = 1  # input 0 = concat segmen; setiap -i berikutnya menambah satu
    if titles:
        from titles import render_title_layers

        print("Judul di belakang kepala: menghitung matte...", flush=True)
        total_frames = sum(s["frames"] for s in segments)
        render_title_layers(titles, plan, segments, concat, size, total_frames,
                            out_dir / f"{name}.titles.ass", wdir / "titles_mask.mkv")
        for t in titles:
            if t.get("cover", 0) > plan["title_style"]["max_cover"]:
                print(f"  Peringatan: judul {t['text']!r} tertutup {t['cover']:.0%} oleh badan/kepala")
        inputs += ["-i", "titles_mask.mkv"]
        graph += [
            "[0:v]split=2[bg][fg]",
            f"[bg]ass=output/{name}.titles.ass{fontsdir}[tx]",
            f"[{n_inputs}:v]scale={size[0]}:{size[1]},format=gray[m]",
            "[fg][m]alphamerge[person]",
            "[tx][person]overlay=format=auto[comp]",
        ]
        n_inputs += 1
        video = "comp"

    if inserts:
        from inserts import insert_key, render_insert

        ins_dir = wdir / "inserts"
        ins_dir.mkdir(exist_ok=True)
        print(f"{len(inserts)} insert...", flush=True)
        for k, ins in enumerate(inserts):
            frames = max(2, round((ins["end"] - ins["start"]) * fps))
            spec = {key: v for key, v in ins.items() if key not in ("start", "end")}
            dest = ins_dir / f"{insert_key(spec, size, fps)}-{frames}.mkv"
            if not dest.exists():
                render_insert(spec, frames, fps, size, dest)
            inputs += ["-i", dest.relative_to(wdir).as_posix()]
            graph += [
                f"[{n_inputs}:v]setpts=PTS-STARTPTS+{ins['start']:.4f}/TB[ins{k}]",
                f"[{video}][ins{k}]overlay=eof_action=pass:format=auto[vi{k}]",
            ]
            n_inputs += 1
            video = f"vi{k}"

    if plan["captions"]["enabled"] and words:
        (out_dir / f"{name}.ass").write_text(build_ass(words, plan, total, segments), encoding="utf-8")
        graph.append(f"[{video}]ass=output/{name}.ass{fontsdir}[cap]")
        video = "cap"
    graph.append(f"[{video}]format=yuv420p[vout]")

    audio = "0:a"
    if plan["sfx"]["enabled"]:
        from sfx import render_track, voice_level_db

        events = sfx_events(plan, segments, titles, inserts, word_time)
        if events:
            envelopes = {cid: load_envelope(input_dir / clips[cid]["file"], envelope_path(wdir, cid, clips[cid]))
                         for cid in {s["clip"] for s in segments}}
            voice_db = voice_level_db(segments, envelopes, fps)
            render_track(events, total, voice_db, plan["sfx"]["volume_db"], wdir / "sfx.wav")
            inputs += ["-i", "sfx.wav"]
            graph.append(f"[0:a][{n_inputs}:a]amix=inputs=2:normalize=0:duration=first[mix]")
            n_inputs += 1
            audio = "mix"
    if plan["audio"]["loudnorm"]:
        graph.append(f"[{audio}]loudnorm=I={plan['audio']['target_lufs']}:TP=-1.5:LRA=11[aout]")
        audio = "aout"
    audio_map = f"[{audio}]" if audio != "0:a" else "0:a"

    print("Render akhir...", flush=True)
    run_ffmpeg([
        *inputs,
        "-filter_complex", ";".join(graph), "-map", "[vout]", "-map", audio_map,
        "-c:v", "libx264", "-preset", q["preset"], "-crf", q["crf"],
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
        f"output/{name}.mp4",
    ], cwd=wdir)
    write_json(out_dir / f"{name}.plan.json", user_plan)

    out = out_dir / f"{name}.mp4"
    print(f"\nSelesai dalam {time.time() - t0:.0f}s")
    print(f"Video : {out}")
    print(f"Durasi: {total:.1f}s, {len(segments)} segmen, {size[0]}x{size[1]}")

    if plan["safe_zone"]["check"]:
        from safezone import check, preview

        issues = check(wdir, name, segments, inserts, total, size, fps, bool(titles))
        preview(str(out), out_dir / f"{name}.safezone.png", segments, total)
        if issues:
            print("\nSafe zone: ada masalah")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("\nSafe zone: aman (semua frame dicek)")
        print(f"Preview safe zone: {out_dir / f'{name}.safezone.png'}")


if __name__ == "__main__":
    main()
