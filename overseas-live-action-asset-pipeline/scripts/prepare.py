"""Reusable request/extraction/index preparation; never generates or archives assets."""
from __future__ import annotations
import argparse
from pathlib import Path
import tempfile
from input_check import validate_request
from pipeline_common import atomic_json, emit, hash_bytes, read_json
from script_index import build_index, episode_headers, extract_text, numbered_headers


def extract(request_path, work_dir, text_path=None):
    raw = read_json(request_path)
    request = validate_request({k: v for k, v in raw.items() if k != "content_index"})
    work = Path(work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    source_path, cache_path = work / "source.txt", work / "extraction.json"
    supplied_text_hash = hash_bytes(Path(text_path).read_bytes()) if text_path else None
    cache_key = [1, request["script_sha256"], supplied_text_hash]
    cached = read_json(cache_path) if cache_path.is_file() else {}
    text = source_path.read_text(encoding="utf-8") if source_path.is_file() else None
    reused = (cached.get("cache_key") == cache_key and text is not None
              and hash_bytes(text.encode("utf-8")) == cached.get("text_sha256"))
    if not reused:
        text = extract_text(raw, text_path)
        source_path.write_text(text, encoding="utf-8", newline="\n")
    lines = text.splitlines()
    headers = episode_headers(lines)
    candidates = numbered_headers(lines) if not headers else []
    # A numeric heading is only a reading aid here. The index requires the
    # agent to explicitly confirm this convention after inspecting the body.
    provisional = not headers and bool(candidates)
    reading_headers = headers or candidates
    ranges = []
    for n, (line, number) in enumerate(reading_headers):
        if request["episodes"] == "all" or number in request["episodes"]:
            end = reading_headers[n + 1][0] - 1 if n + 1 < len(reading_headers) else len(lines)
            ranges.append({"episode": number, "start_line": line, "end_line": end})
    if not reading_headers:
        ranges = [{"episode": None, "start_line": 1, "end_line": len(lines)}]
    reading_path = work / "reading.txt"
    reading = "\n\n".join("\n".join(f"{n:04d} {lines[n - 1]}"
                           for n in range(span["start_line"], span["end_line"] + 1)) for span in ranges)
    reading_path.write_text(reading, encoding="utf-8", newline="\n")
    metadata = {"cache_key": cache_key, "script_sha256": request["script_sha256"],
                "text_sha256": hash_bytes(text.encode("utf-8")), "line_count": len(lines),
                "headers": headers, "numbered_heading_candidates": candidates,
                "selected_ranges": ranges, "selection_provisional": provisional,
                "candidate_preface_end": reading_headers[0][0] - 1 if reading_headers else None}
    atomic_json(cache_path, metadata)
    return {"ok": True, "asset_workflow": request["asset_workflow"], "region_mode": request["region_mode"], "script_sha256": request["script_sha256"], "reused_extraction": reused,
            "reading_path": str(reading_path), "source_path": str(source_path),
            "metadata_path": str(cache_path), "selected_ranges": ranges,
            "selection_provisional": provisional, "content_validated": False,
            "ready_for_generation": False, "output_mode": request["output_mode"],
            "generate_images": request["generate_images"], "image_types": request["image_types"],
            "generate_workbook": request["generate_workbook"],
            "note": "独立数字只是候选分集；阅读确认后在标注中设置 confirmed_numbered_episodes=true" if provisional else None}


def index(request_path, annotations_path, work_dir, text_path=None):
    raw = read_json(request_path)
    validate_request({k: v for k, v in raw.items() if k != "content_index"})
    indexed = build_index(raw, read_json(annotations_path), text_path)
    work = Path(work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    # Do not change the request or its current index if scope resolution fails.
    with tempfile.TemporaryDirectory(prefix="index-check-", dir=work) as temporary:
        trial = Path(temporary) / "index.json"
        atomic_json(trial, indexed)
        request = validate_request({**raw, "content_index": str(trial)})
    index_path = work / "content-index.json"
    raw["content_index"] = str(index_path)
    request["content_index"] = str(index_path)
    atomic_json(index_path, indexed)
    atomic_json(request_path, raw)
    resolved_path = work / "resolved-assets.json"
    atomic_json(resolved_path, request)
    return {"ok": True, "asset_workflow": request["asset_workflow"], "region_mode": request["region_mode"], "content_validated": True, "ready_for_generation": request["ready_for_generation"],
            "image_types": request["image_types"], "generate_images": request["generate_images"],
            "generate_workbook": request["generate_workbook"],
            "index_path": str(index_path), "resolved_path": str(resolved_path),
            "asset_count": len(request["resolved_assets"]),
            "assets": [{k: item[k] for k in ("id", "catalog_id", "name", "category", "kind", "parent_id", "generate_image")}
                       for item in request["resolved_assets"]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["extract", "index"])
    parser.add_argument("request")
    parser.add_argument("annotations", nargs="?")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--text")
    args = parser.parse_args()
    try:
        if args.command == "extract":
            if args.annotations:
                raise ValueError("extract 不接受 annotations 参数")
            result = extract(args.request, args.work_dir, args.text)
        else:
            if not args.annotations:
                raise ValueError("index 需要正文标注文件")
            result = index(args.request, args.annotations, args.work_dir, args.text)
        emit(result)
        return 0
    except Exception as exc:
        emit({"ok": False, "errors": getattr(exc, "errors", [str(exc)])})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
