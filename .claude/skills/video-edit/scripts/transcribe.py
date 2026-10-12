"""Transkrip semua klip di folder input dengan timestamp per kata.

    python transcribe.py <folder_klip> [--model small] [--language id] [--prompt "Claude Code"]

Hasil (di <folder_klip>/_edit/):
    clips.json            id klip (c1, c2, ...) -> nama file + info video
    transcripts/<id>.json kata-kata dengan index, waktu mulai/selesai, dan probabilitas
    transcript.txt        versi yang mudah dibaca untuk menyusun plan.json

File klip asli hanya dibaca. Klip yang sudah ditranskrip dan tidak berubah dilewati.
"""
import argparse
import os
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

from common import face_cache, list_clips, probe, read_json, resolve_input, work_dir, write_json

LINE_BREAK_GAP = 0.6  # detik; jeda lebih panjang dari ini memulai baris baru di transcript.txt
SHOW_GAP = 1.0  # detik; jeda sepanjang ini ditandai eksplisit
LOW_CONFIDENCE = 0.5


def assign_ids(input_dir: Path, wdir: Path) -> dict:
    clips = read_json(wdir / "clips.json", {})
    by_file = {meta["file"]: cid for cid, meta in clips.items()}
    next_num = max((int(cid[1:]) for cid in clips), default=0) + 1
    present = set()
    for path in list_clips(input_dir):
        present.add(path.name)
        stat = path.stat()
        fingerprint = f"{stat.st_size}:{int(stat.st_mtime)}"
        cid = by_file.get(path.name)
        if cid is None:
            cid = f"c{next_num}"
            next_num += 1
            clips[cid] = {"file": path.name}
        if clips[cid].get("fingerprint") != fingerprint:
            clips[cid].update(probe(path), fingerprint=fingerprint, transcribed=False)
    for cid in [c for c, meta in clips.items() if meta["file"] not in present]:
        del clips[cid]
    return clips


def transcribe_clip(model, path: Path, language, prompt):
    segments, info = model.transcribe(
        str(path),
        language=language,
        word_timestamps=True,
        initial_prompt=prompt or None,
        condition_on_previous_text=False,
        vad_filter=False,
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if not text:
                continue
            words.append({"i": len(words), "w": text, "s": round(w.start, 3), "e": round(w.end, 3), "p": round(w.probability, 2)})
    return {"language": info.language, "words": words}


def format_transcript(clips: dict, wdir: Path) -> str:
    out = [
        "# Format: <index>:<kata>. Tanda ? = kemungkinan salah dengar. [detik] = waktu mulai baris.",
        "# Pakai index kata untuk 'from'/'to' di plan.json.",
        "",
    ]
    for cid in sorted(clips, key=lambda c: int(c[1:])):
        meta = clips[cid]
        data = read_json(wdir / "transcripts" / f"{cid}.json", {"words": []})
        size = f"{meta.get('width')}x{meta.get('height')} @ {meta.get('fps')}fps"
        out.append(f"== {cid}  {meta['file']}  ({meta.get('duration', 0):.1f}s, {size})")
        words = data["words"]
        if not words:
            out.append("   (tidak ada ucapan terdeteksi)")
        line, prev_end = [], None
        for w in words:
            gap = w["s"] - prev_end if prev_end is not None else 0
            if line and gap > LINE_BREAK_GAP:
                out.append(" ".join(line))
                line = []
                if gap >= SHOW_GAP:
                    out.append(f"   ~ jeda {gap:.1f}s ~")
            if not line:
                line.append(f"[{w['s']:.2f}]")
            flag = "?" if w["p"] < LOW_CONFIDENCE else ""
            line.append(f"{w['i']}:{w['w']}{flag}")
            prev_end = w["e"]
        if line:
            out.append(" ".join(line))
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_dir")
    ap.add_argument("--model", default="small", help="tiny/base/small/medium/large-v3 (lebih besar = lebih akurat, lebih lambat)")
    ap.add_argument("--language", default="id")
    ap.add_argument("--prompt", default="", help="kosakata yang sering salah dengar, mis. 'Claude Code, Reels'")
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    ap.add_argument("--force", action="store_true", help="transkrip ulang semua klip")
    ap.add_argument("--no-faces", action="store_true", help="lewati analisis wajah")
    args = ap.parse_args()

    input_dir = resolve_input(args.input_dir)
    wdir = work_dir(input_dir)
    clips = assign_ids(input_dir, wdir)
    if not clips:
        raise SystemExit(f"Tidak ada file video di {input_dir}")

    todo = [cid for cid, meta in clips.items() if args.force or not meta.get("transcribed")]
    if todo:
        from faster_whisper import WhisperModel

        compute = "int8" if args.device == "cpu" else "float16"
        print(f"Memuat model {args.model} ({args.device})...", flush=True)
        model = WhisperModel(args.model, device=args.device, compute_type=compute)
        for cid in sorted(todo, key=lambda c: int(c[1:])):
            meta = clips[cid]
            if not meta.get("has_audio"):
                print(f"{cid} {meta['file']}: tidak ada audio, dilewati")
                write_json(wdir / "transcripts" / f"{cid}.json", {"language": None, "words": []})
            else:
                t0 = time.time()
                print(f"{cid} {meta['file']}: transkrip...", flush=True)
                result = transcribe_clip(model, input_dir / meta["file"], args.language, args.prompt)
                write_json(wdir / "transcripts" / f"{cid}.json", result)
                print(f"   {len(result['words'])} kata, {time.time() - t0:.0f}s", flush=True)
            meta["transcribed"] = True
            write_json(wdir / "clips.json", clips)
    write_json(wdir / "clips.json", clips)

    if not args.no_faces:
        from vision import analyze_faces

        for cid in sorted(clips, key=lambda c: int(c[1:])):
            meta = clips[cid]
            cache = face_cache(wdir, cid, meta)
            if cache.exists():
                continue
            t0 = time.time()
            print(f"{cid} {meta['file']}: analisis wajah...", flush=True)
            data = analyze_faces(input_dir / meta["file"], cache)
            found = sum(1 for s in data["samples"] if len(s) == 5)
            print(f"   wajah terdeteksi di {found}/{len(data['samples'])} sampel, {time.time() - t0:.0f}s", flush=True)

    text = format_transcript(clips, wdir)
    (wdir / "transcript.txt").write_text(text, encoding="utf-8")
    print(f"\nTranskrip: {wdir / 'transcript.txt'}")


if __name__ == "__main__":
    main()
