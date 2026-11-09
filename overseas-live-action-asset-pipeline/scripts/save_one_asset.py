"""Archive exactly one completed image without overwriting existing assets."""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import sys
from time import perf_counter

from input_check import RequestError, load_and_validate
from image_check import inspect_image, image_kind
from pipeline_common import file_hash, io_operation

EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "TIFF": ".tif",
              "BMP": ".bmp", "GIF": ".gif"}
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


def digest(path):
    return file_hash(path)


def copy_and_digest(input_stream, output):
    value = hashlib.sha256()
    for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
        output.write(chunk)
        value.update(chunk)
    return value.hexdigest()


def source_stamp(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def require_unchanged(path, stamp):
    if source_stamp(path) != stamp:
        raise ValueError("归档期间源图片发生变化，请等待源文件写入完成后重试保存")


def safe_stem(value):
    name = value.strip()
    suffix = Path(name).suffix.lower()
    if suffix in set(EXTENSIONS.values()) | {".jpeg", ".tiff"}:
        name = name[:-len(suffix)]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name).rstrip(" .").lstrip(" ")
    if not name or name in {".", ".."}:
        raise ValueError("资产名称不能为空或仅为点号")
    if name.split(".")[0].upper() in RESERVED:
        name = "_" + name
    return name


def inside(path, root):
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("目标路径超出本次主路径")
    return resolved


def _save_one(validated, source_path, category, name, kind=None):
    started = perf_counter()
    if category not in {"人物", "场景", "道具"}:
        raise ValueError("资产类别只能是人物、场景或道具")
    if category not in validated["asset_types"]:
        raise ValueError("本次未选择该资产类别：" + category)
    if not validated["save_assets"]:
        return {"ok": True, "status": "not_saved", "category": category,
                "reason": "本次未指定保存路径，不执行归档；图片交付须由调用方检查"}
    from PIL import Image
    source = Path(source_path).expanduser()
    if not source.is_absolute():
        raise ValueError("源图片路径必须是绝对路径")
    source = source.resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise ValueError("源图片不存在或为空")
    original_stamp = source_stamp(source)
    checked = inspect_image(source, category, kind, validated["min_short_side"])
    image_format = checked["format"]
    width, height = checked["width"], checked["height"]
    if image_format not in EXTENSIONS:
        raise ValueError("暂不支持该真实图片格式：" + str(image_format))
    stem = safe_stem(name)
    expected_hash = checked["sha256"]
    root = Path(validated["output_root"])
    folder = inside(root / category, root)
    folder.mkdir(parents=True, exist_ok=True)
    folder = inside(folder, root)
    extension = EXTENSIONS[image_format]
    result = {"ok": True, "category": category, "width": width, "height": height,
              "format": image_format}

    for version in itertools.count(1):
        suffix = "" if version == 1 else f"-v{version}"
        target = inside(folder / (stem + suffix + extension), root)
        if target.exists():
            if target.is_file():
                # Hash before copying only when existing content must be compared.
                if expected_hash is None:
                    expected_hash = digest(source)
                    require_unchanged(source, original_stamp)
                if digest(target) == expected_hash:
                    require_unchanged(source, original_stamp)
                    return {**result, "sha256": expected_hash,
                            "status": "reused", "target_path": str(target),
                            "timing": {"copy_seconds": 0.0, "verify_seconds": 0.0,
                                       "save_seconds": perf_counter() - started}}
            continue
        try:
            fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        except FileExistsError:
            continue
        try:
            copy_started = perf_counter()
            with os.fdopen(fd, "wb") as output, source.open("rb") as input_stream:
                copied_hash = copy_and_digest(input_stream, output)
            copy_seconds = perf_counter() - copy_started
            verify_started = perf_counter()
            require_unchanged(source, original_stamp)
            if expected_hash is not None and copied_hash != expected_hash:
                raise ValueError("归档期间源图片内容发生变化，请重试保存")
            with Image.open(target) as saved:
                saved.verify()
            if target.stat().st_size == 0 or digest(target) != copied_hash:
                raise ValueError("保存后的图片内容校验失败")
            return {**result, "sha256": copied_hash,
                    "status": "saved", "target_path": str(target),
                    "timing": {"copy_seconds": copy_seconds,
                               "verify_seconds": perf_counter() - verify_started,
                               "save_seconds": perf_counter() - started}}
        except Exception:
            # Remove only the incomplete file created by this invocation.
            own_target = inside(target, root)
            if own_target.is_file():
                own_target.unlink()
            raise


class AssetSaver:
    """Reuse one validated request; every save still verifies exactly one image."""

    def __init__(self, request_path):
        self._validated = load_and_validate(request_path, require_content=True)

    @io_operation
    def save(self, source_path, category, name, asset_id=None, kind=None):
        if not self._validated["generate_images"]:
            raise ValueError("本次输出类型不包含图片，不能执行图片保存")
        inferred_kind = kind or ("turnaround" if "-三视图" in name else None)
        if asset_id is not None:
            matches = [x for x in self._validated["resolved_assets"] if x["id"] == asset_id]
        else:
            wanted_kind = image_kind(category, inferred_kind)
            matches = [x for x in self._validated["resolved_assets"] if x["category"] == category and x["kind"] == wanted_kind]
        if len(matches) != 1 or matches[0]["category"] != category:
            raise ValueError("保存条目不属于本次唯一匹配的计划，请提供正确 asset_id")
        task = matches[0]
        if not task["generate_image"]:
            raise ValueError("本项未选择图片输出，不能执行图片保存")
        if kind is not None and task["kind"] != kind:
            raise ValueError("保存图片类型与本次资产计划不一致")
        result = _save_one(self._validated, source_path, category, name, task["kind"])
        return {**result, "asset_id": task["id"]}

    def ready(self):
        return {"ok": True, "status": "ready",
                "output_root": self._validated["output_root"],
                "save_assets": self._validated["save_assets"],
                "asset_types": list(self._validated["asset_types"])}


@io_operation
def save_one(request_path, source_path, category, name, asset_id=None, kind=None):
    # Keep the original import and command-line API available.
    return AssetSaver(request_path).save(source_path, category, name, asset_id, kind)


def emit(result):
    print(json.dumps(result, ensure_ascii=False), flush=True)


def run_stream(saver):
    """One JSON line in, one independently verified save result out, immediately."""
    emit(saver.ready())
    for line in sys.stdin:
        request_id = None
        try:
            command = json.loads(line)
            if command == {"op": "close"}:
                emit({"ok": True, "status": "closed"})
                return 0
            if not isinstance(command, dict):
                raise ValueError("每行只能提交一个单图对象，不接受列表或批次")
            if isinstance(command.get("id"), str):
                request_id = command["id"]
            fields = {"id", "source", "category", "name"}
            if (not fields.issubset(command) or set(command) - (fields | {"asset_id", "kind"})) or any(
                not isinstance(command[key], str) or not command[key].strip()
                for key in fields
            ):
                raise ValueError("每行须有非空 id、source、category、name，可选 asset_id、kind")
            result = saver.save(command["source"], command["category"], command["name"], command.get("asset_id"), command.get("kind"))
            emit({"id": request_id, **result})
        except Exception as exc:
            # A failed image must not discard subsequent independent commands.
            emit({"id": request_id, "ok": False, "errors": [str(exc)]})
    return 0


def main():
    parser = argparse.ArgumentParser(description="Save exactly one image to its asset folder")
    parser.add_argument("request", help="Validated user request JSON")
    parser.add_argument("--stream", action="store_true", help="Reuse this process for individual JSON-line saves")
    parser.add_argument("--source", help="One generated image path")
    parser.add_argument("--category", choices=["人物", "场景", "道具"])
    parser.add_argument("--name", help="Asset filename without extension")
    parser.add_argument("--asset-id")
    parser.add_argument("--kind", choices=["original", "group", "turnaround", "scene", "prop"])
    args = parser.parse_args()
    image_args = (args.source, args.category, args.name)
    if args.stream and any(value is not None for value in (*image_args, args.asset_id, args.kind)):
        parser.error("--stream 不能与 --source、--category、--name 同时使用")
    if not args.stream and any(value is None for value in image_args):
        parser.error("单次保存必须同时提供 --source、--category、--name")
    try:
        if args.stream:
            return run_stream(AssetSaver(args.request))
        result = save_one(args.request, args.source, args.category, args.name, args.asset_id, args.kind)
    except RequestError as exc:
        emit({"ok": False, "errors": exc.errors})
        return 2
    except Exception as exc:
        emit({"ok": False, "errors": [str(exc)]})
        return 2
    emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
