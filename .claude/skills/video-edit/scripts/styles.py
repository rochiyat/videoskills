"""Preset look: gaya caption, gaya judul, dan color grade dalam satu nama.

Urutan prioritas: DEFAULTS di render.py < LOOKS[look] < isi plan.json.
Satu keep bisa memakai look lain lewat `keep[].look`; caption dan grade
bagian itu ikut berganti, sedangkan field yang ditulis user di plan tetap menang.
"""
import copy

LOOKS = {
    "bold": {
        "captions": {
            "font": "Arial Black", "size": 80, "bold": True, "italic": False, "uppercase": True,
            "color": "#FFFFFF", "highlight": "#FFE14D", "outline": 8, "box": False,
        },
        "title_style": {"font": "Arial Black", "color": "#FFFFFF", "uppercase": True, "italic": False, "shadow": 4},
        "grade": {"contrast": 1.08, "saturation": 1.12, "warmth": 0.0},
    },
    "clean": {
        "captions": {
            "font": "Arial", "size": 72, "bold": True, "italic": False, "uppercase": False,
            "color": "#FFFFFF", "highlight": "#7FD6FF", "outline": 0, "box": True,
        },
        "title_style": {"font": "Arial Black", "color": "#FFFFFF", "uppercase": False, "italic": False, "shadow": 2},
        "grade": {"contrast": 1.03, "saturation": 1.02, "warmth": 0.0},
    },
    "editorial": {
        "captions": {
            "font": "Georgia", "size": 78, "bold": True, "italic": True, "uppercase": False,
            "color": "#F5EBDD", "highlight": "#FFB38A", "outline": 5, "box": False,
        },
        "title_style": {"font": "Georgia", "color": "#F5EBDD", "uppercase": False, "italic": True, "shadow": 3},
        "grade": {"contrast": 0.97, "saturation": 0.84, "warmth": 0.04},
    },
}


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def check_look(name, where="look"):
    if name not in LOOKS:
        raise SystemExit(f"plan.json: {where} '{name}' tidak dikenal (pilihan: {', '.join(LOOKS)})")


def build_plan(defaults, user_plan):
    """Gabungkan default, look utama, dan plan user. Simpan juga config per look untuk keep yang berbeda."""
    look = user_plan.get("look", "bold")
    check_look(look)
    plan = deep_merge(deep_merge(defaults, LOOKS[look]), user_plan)
    plan["look"] = look
    plan["looks"] = {}
    for n, item in enumerate(plan["keep"]):
        name = item.get("look", look)
        check_look(name, f"keep[{n}].look")
        if name not in plan["looks"]:
            per_look = deep_merge(deep_merge(defaults, LOOKS[name]), user_plan)
            plan["looks"][name] = {k: per_look[k] for k in ("captions", "title_style", "grade")}
    return plan


def grade_filter(grade):
    """Filter ffmpeg untuk color grade, atau string kosong kalau netral."""
    parts = []
    if grade["contrast"] != 1 or grade["saturation"] != 1:
        parts.append(f"eq=contrast={grade['contrast']}:saturation={grade['saturation']}")
    w = grade.get("warmth", 0)
    if w:
        parts.append(f"colorbalance=rs={w}:gs={w / 3:.3f}:bs={-w}:rm={w * 0.75:.3f}:bm={-w * 0.75:.3f}")
    return ",".join(parts)
