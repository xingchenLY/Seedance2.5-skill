"""Read-only geometry and file-integrity checks; no visual approval or edits."""
from __future__ import annotations
import argparse
from pathlib import Path
from pipeline_common import emit, file_hash, file_stamp, operation_cache

PROFILES = {"original": ("人物", ((9, 16),)), "group": ("人物", ((4, 3),)),
            "turnaround": ("人物", ((16, 9),)), "scene": ("场景", ((16, 9),)),
            "prop": ("道具", ((9, 16), (16, 9)))}
DEFAULT_MIN_SHORT_SIDE = 512
RATIO_TOLERANCE = 0.01


def image_kind(category, kind=None):
    kind = kind or {"人物": "original", "场景": "scene", "道具": "prop"}.get(category)
    if kind not in PROFILES or PROFILES[kind][0] != category:
        raise ValueError("图片类型与资产类别不一致")
    return kind


def inspect_image(path, category, kind=None, min_short_side=DEFAULT_MIN_SHORT_SIDE):
    from PIL import Image
    kind = image_kind(category, kind)
    path = Path(path).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError("图片必须是可读取的绝对文件路径")
    if type(min_short_side) is not int or min_short_side <= 0:
        raise ValueError("最短边像素必须是正整数")
    stamp = file_stamp(path)
    cache = operation_cache('images')
    key = (str(path.resolve()), stamp, category, kind, min_short_side)
    if cache is not None and key in cache:
        return dict(cache[key])
    before = file_hash(path)
    with Image.open(path) as image:
        image_format = image.format
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("资产图片必须是单帧图片")
        width, height = image.size
        if image.getexif().get(274) in {5, 6, 7, 8}:
            width, height = height, width
        image.load()
    after = file_hash(path, refresh=True)
    if before != after or file_stamp(path) != stamp:
        raise ValueError("图片在校验期间发生变化")
    allowed_ratios = PROFILES[kind][1]
    matched_ratio = next(((rw, rh) for rw, rh in allowed_ratios
                          if abs((width / height) / (rw / rh) - 1) <= RATIO_TOLERANCE), None)
    if matched_ratio is None:
        expected = " 或 ".join(f"{rw}:{rh}" for rw, rh in allowed_ratios)
        raise ValueError(f"画面比例不符：实际 {width}×{height}，要求 {expected}")
    if min(width, height) < min_short_side:
        raise ValueError(f"图片尺寸不足：最短边 {min(width, height)}，要求至少 {min_short_side}")
    result = {"ok": True, "source_path": str(path.resolve()), "sha256": after,
            "width": width, "height": height, "format": image_format,
            "aspect_ratio": f"{matched_ratio[0]}:{matched_ratio[1]}",
            "kind": kind, "category": category, "visual_approved": False}
    if cache is not None:
        cache[key] = dict(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--category", required=True, choices=["人物", "场景", "道具"])
    parser.add_argument("--kind", choices=list(PROFILES))
    parser.add_argument("--min-short-side", type=int, default=DEFAULT_MIN_SHORT_SIDE)
    args = parser.parse_args()
    try:
        emit(inspect_image(args.source, args.category, args.kind, args.min_short_side))
        return 0
    except Exception as exc:
        emit({"ok": False, "errors": [str(exc)]})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
