"""Framing 9:16 dan punch-in zoom yang mengikuti posisi wajah."""
from common import face_cache, read_json

FACE_Y = 0.38  # tengah wajah ditaruh di 38% tinggi frame saat zoom


def _even(v):
    return int(round(v / 2) * 2)


def base_crop(src_w, src_h, out_w, out_h):
    aspect = out_w / out_h
    if src_w / src_h > aspect:
        return src_h * aspect, src_h
    return src_w, src_w / aspect


def default_punch(meta, out_h):
    _, ch = base_crop(meta["width"], meta["height"], 9, 16)
    return 1.5 if ch >= 1.8 * out_h else 1.3


def assign_zoom(segments, plan, clips):
    """Zoom per segmen. Jump cut (dua segmen berurutan dari klip yang sama) bergantian 1.0x dan punch-in."""
    cfg = plan["zoom"]
    prev = None
    for seg in segments:
        item = plan["keep"][seg["keep"]]
        if seg.get("zoom_lock") and prev is not None:
            zoom = prev["zoom"]  # judul melintasi cut ini: posisi kepala harus tetap
        elif "zoom" in item:
            zoom = float(item["zoom"])
        elif not cfg["auto"]:
            zoom = 1.0
        else:
            punch = cfg.get("punch") or default_punch(clips[seg["clip"]], plan["output"]["height"])
            jump_cut = prev is not None and prev["clip"] == seg["clip"]
            zoom = punch if jump_cut and prev["zoom"] == 1.0 else 1.0
        seg["zoom"] = min(max(zoom, 1.0), cfg["max"])
        prev = seg


def compute_crops(segments, plan, clips, wdir):
    """Isi seg['crop'] = (w, h, x, y) di koordinat sumber, dan seg['face'] di koordinat output (0..1)."""
    from vision import face_in_range

    out_w, out_h, fps = plan["output"]["width"], plan["output"]["height"], plan["output"]["fps"]
    faces_by_clip = {}
    for seg in segments:
        cid = seg["clip"]
        meta = clips[cid]
        if cid not in faces_by_clip:
            faces_by_clip[cid] = read_json(face_cache(wdir, cid, meta))
        src_w, src_h = meta["width"], meta["height"]
        cw, ch = base_crop(src_w, src_h, out_w, out_h)
        cw, ch = min(src_w, _even(cw / seg["zoom"])), min(src_h, _even(ch / seg["zoom"]))

        manual = plan["framing"].get(cid, {})
        face = face_in_range(faces_by_clip[cid], seg["start"], seg["start"] + seg["frames"] / fps)
        if "x" in manual:
            x = (src_w - cw) * manual["x"]
        elif face:
            x = face[0] * src_w - cw / 2
        else:
            x = (src_w - cw) / 2
        if "y" in manual:
            y = (src_h - ch) * manual["y"]
        elif face:
            y = face[1] * src_h - FACE_Y * ch
        else:
            y = (src_h - ch) / 2
        x = int(min(max(x, 0), src_w - cw))
        y = int(min(max(y, 0), src_h - ch))
        seg["crop"] = (cw, ch, x, y)
        seg["face"] = None
        if face:
            fx, fy, fw, fh = face
            seg["face"] = ((fx * src_w - x) / cw, (fy * src_h - y) / ch, fw * src_w / cw, fh * src_h / ch)


def face_at(segments, t):
    """Kotak wajah (output, 0..1) pada waktu output t, atau None."""
    for seg in segments:
        if seg["out"] <= t < seg["out_end"]:
            return seg.get("face")
    return None
