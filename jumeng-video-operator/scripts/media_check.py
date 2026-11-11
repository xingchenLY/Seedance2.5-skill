#!/usr/bin/env python3
"""Read-only media checks. A technical pass never implies visual acceptance."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
from fractions import Fraction


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def signature(path):
    stat = Path(path).stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino]


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".report-", suffix=".json", delete=False) as out:
        json.dump(data, out, ensure_ascii=False, indent=2, allow_nan=False)
        temp = out.name
    os.replace(temp, path)


def run_text(args):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} failed: {result.stderr[-2000:]}")
    return result.stdout


def number(value):
    try:
        parsed = float(Fraction(str(value)))
        return parsed if math.isfinite(parsed) else None
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def summarize(probe):
    videos = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    if len(videos) != 1:
        raise ValueError("Expected exactly one non-attached video stream")
    video = videos[0]
    rotation_values = [number(s["rotation"]) for s in video.get("side_data_list", [])
                       if "rotation" in s]
    if "rotate" in video.get("tags", {}):
        rotation_values.append(number(video["tags"]["rotate"]))
    rotations = [r % 360 for r in rotation_values if r is not None]
    rotation = rotations[0] if rotations else 0.0
    rotation_known = (len(rotations) == len(rotation_values)
                      and all(abs(r - rotation) < 0.001 for r in rotations)
                      and any(abs(rotation - r) < 0.001 for r in (0, 90, 180, 270)))
    if rotation_known:
        rotation = min((0, 90, 180, 270), key=lambda r: abs(rotation - r))
    width, height = video.get("width"), video.get("height")
    if rotation_known and rotation in (90, 270):
        display_width, display_height = height, width
    elif rotation_known:
        display_width, display_height = width, height
    else:
        display_width = display_height = None
    duration = number(video.get("duration"))
    time_base = number(video.get("time_base"))
    if duration is None and time_base is not None:
        duration_ticks = number(video.get("duration_ts"))
        if duration_ticks is not None:
            duration = duration_ticks * time_base
    return {
        "stream_index": video["index"],
        "video_duration": duration,
        "container_duration": number(probe.get("format", {}).get("duration")),
        "width": width, "height": height,
        "rotation": rotation, "rotation_known": rotation_known,
        "display_width": display_width, "display_height": display_height,
        "sample_aspect_ratio": video.get("sample_aspect_ratio"),
        "average_frame_rate": number(video.get("avg_frame_rate")),
        "nominal_frame_rate": number(video.get("r_frame_rate")),
        "time_base": time_base,
    }


def compare_parameters(actual, seconds, width, height):
    reasons = []
    tolerance = 0.0
    avg, nominal = actual["average_frame_rate"], actual["nominal_frame_rate"]
    base = actual["time_base"]
    if avg and nominal and avg > 0 and abs(avg - nominal) < 1e-9 and base and base > 0:
        # Metadata agreement permits only a small rounding bound, not general padding.
        tolerance = 1 / avg + base
    durations = {k: actual[k] for k in ("video_duration", "container_duration")}
    differences = {}
    if seconds is None:
        reasons.append("target_duration_unknown")
    for key, duration in durations.items():
        if duration is None or duration <= 0:
            reasons.append(key + "_unknown")
        elif seconds is not None:
            differences[key] = duration - seconds
            if abs(duration - seconds) > tolerance + 1e-9:
                reasons.append(key + "_mismatch")
    if width is None or height is None:
        reasons.append("target_dimensions_unknown")
    elif (actual["display_width"], actual["display_height"]) != (width, height):
        reasons.append("dimensions_mismatch")
    if not actual["rotation_known"]:
        reasons.append("rotation_requires_review")
    aspect = number(actual["sample_aspect_ratio"].replace(":", "/")) if actual.get("sample_aspect_ratio") else None
    if aspect is None or abs(aspect - 1) > 1e-9:
        reasons.append("sample_aspect_ratio_requires_review")
    return {"status": "pending" if reasons else "passed", "reasons": reasons,
            "duration_tolerance_seconds": tolerance, "duration_differences": differences,
            "tolerance_basis": "matching average/nominal fps plus time_base" if tolerance else "no automatic rounding allowance"}


def valid_cache(path, key):
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
        if cache["key"] != key or not cache["decode_passed"]:
            return None
        required = {cache["probe_path"], cache["decode_log_path"], *cache["sheet_paths"]}
        if required != {item["path"] for item in cache["artifacts"]}:
            return None
        if key.get("contact_sheet") and not cache["sheet_paths"]:
            return None
        for item in cache["artifacts"]:
            artifact = path.parent / item["path"]
            if not artifact.resolve().is_relative_to(path.parent.resolve()):
                return None
            if sha256(artifact) != item["sha256"]:
                return None
        # Metadata/log are separately verified artifacts, not unverified values in the manifest.
        probe = json.loads((path.parent / cache["probe_path"]).read_text(encoding="utf-8"))
        log = path.parent / cache["decode_log_path"]
        if log.read_text(encoding="utf-8").strip():
            return None
        return cache, probe
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def inspect(args):
    start = time.perf_counter()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "report.json"
    video = Path(args.video).resolve()
    download = Path(args.download).resolve() if args.download else None
    if video == report_path or download == report_path:
        raise ValueError("Report path must not overwrite an input")
    report = {"status": "error", "video": str(video), "visual_review": "not_performed",
              "voice_review": "not_performed", "timings_seconds": {}, "cache_hit": False,
              "targets": {"seconds": args.expected_seconds, "display_width": args.expected_width,
                          "display_height": args.expected_height}}
    try:
        initial = signature(video)
        download_initial = signature(download) if download else None
        same_input = download is not None and video.samefile(download)
        versions = {"ffmpeg": run_text([args.ffmpeg, "-version"]).splitlines()[0],
                    "ffprobe": run_text([args.ffprobe, "-version"]).splitlines()[0]}
        report["tools"] = versions
        stamp = time.perf_counter()
        video_hash = sha256(video)
        report["sha256"] = video_hash
        report["timings_seconds"]["source_hash"] = time.perf_counter() - stamp
        download_hash = video_hash if same_input else (sha256(download) if download else None)
        report["download"] = {"path": str(download) if download else None, "sha256": download_hash,
                              "status": "unknown" if download is None else ("matched" if video_hash == download_hash else "mismatch"),
                              "distinct_path": download is not None and download != video,
                              "same_file": same_input, "hash_reused": same_input}
        key = {"video_sha256": video_hash, "script_sha256": sha256(__file__),
               "tools": versions}
        cache_id = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
        cache_dir = output / cache_id
        cache_dir.mkdir(exist_ok=True)
        manifest = cache_dir / "manifest.json"
        cached = valid_cache(manifest, key) if manifest.exists() else None
        sheet_key = {**key, "contact_sheet": True, "sheet_settings": "1fps-320w-4x4-v1"}
        sheet_manifest = cache_dir / "sheets-manifest.json"
        sheet_cached = (valid_cache(sheet_manifest, sheet_key)
                        if args.contact_sheet and sheet_manifest.exists() else None)
        report["decode_cache_hit"] = bool(cached)
        report["sheet_cache_hit"] = bool(cached and sheet_cached) if args.contact_sheet else None
        report["cache_hit"] = bool(cached and (not args.contact_sheet or sheet_cached))
        stamp = time.perf_counter()
        if cached:
            cache, probe = cached
        sheet_cache = sheet_cached[0] if sheet_cached else None
        if not cached or (args.contact_sheet and not sheet_cached):
            run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=cache_dir))
            if not cached:
                probe = json.loads(run_text([args.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)]))
            actual = summarize(probe)  # Reject ambiguous video selection before caching.
            probe_path = run_dir / "probe.json"
            atomic_json(probe_path, probe)
            log_path = run_dir / "decode.log"
            command = [args.ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-i", str(video)]
            if args.contact_sheet and cached:
                # Decode only the video needed for new sheets; keep the valid full-decode proof.
                command += ["-map", f"0:{actual['stream_index']}", "-an", "-vf",
                            "fps=1,scale=320:-2,tile=4x4:padding=2:margin=2",
                            "-fps_mode", "vfr", "-q:v", "3", str(run_dir / "sheet-%04d.jpg")]
            elif args.contact_sheet:
                graph = f"[0:{actual['stream_index']}]split=2[full][thumb];[thumb]fps=1,scale=320:-2,tile=4x4:padding=2:margin=2[sheet]"
                command += ["-filter_complex", graph, "-map", "[full]", "-map", "0:a?", "-f", "null", "-",
                            "-map", "[sheet]", "-fps_mode", "vfr", "-q:v", "3", str(run_dir / "sheet-%04d.jpg")]
            else:
                command += ["-map", "0:v", "-map", "0:a?", "-f", "null", "-"]
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=log)
            sheets = sorted(run_dir.glob("sheet-*.jpg"))
            passed = process.returncode == 0 and not log_path.read_text(encoding="utf-8", errors="replace").strip()
            if args.contact_sheet and not sheets:
                passed = False
            artifacts = [{"path": str(p.relative_to(cache_dir)), "sha256": sha256(p)}
                         for p in [probe_path, log_path, *sheets]]
            result = {"decode_passed": passed, "returncode": process.returncode,
                      "probe_path": str(probe_path.relative_to(cache_dir)),
                      "decode_log_path": str(log_path.relative_to(cache_dir))}
            if not cached:
                cache = {**result, "key": key, "sheet_paths": [], "artifacts": artifacts[:2]}
            if args.contact_sheet:
                sheet_cache = {**result, "key": sheet_key,
                               "sheet_paths": [str(p.relative_to(cache_dir)) for p in sheets],
                               "artifacts": artifacts}
            # Do not cache checks from a file modified while processing.
            if passed and signature(video) == initial:
                if not cached:
                    atomic_json(manifest, cache)
                if args.contact_sheet:
                    atomic_json(sheet_manifest, sheet_cache)
        report["timings_seconds"]["probe_decode_sheets_or_cache"] = time.perf_counter() - stamp
        report["actual"] = summarize(probe)
        report["decode"] = {"status": "passed" if cache["decode_passed"] else "pending",
                            "log": str(cache_dir / cache["decode_log_path"])}
        report["contact_sheets"] = ([str(cache_dir / p) for p in sheet_cache["sheet_paths"]]
                                    if args.contact_sheet else [])
        if args.contact_sheet:
            report["sheet_generation"] = {
                "status": "passed" if sheet_cache["decode_passed"] else "pending",
                "log": str(cache_dir / sheet_cache["decode_log_path"])}
        report["contact_sheet_note"] = "1 fps, 4x4 pages, approximate sampling positions; last page may have empty cells. Initial screening only."
        report["parameters"] = compare_parameters(report["actual"], args.expected_seconds,
                                                   args.expected_width, args.expected_height)
        reasons = list(report["parameters"]["reasons"])
        if not cache["decode_passed"] or (args.contact_sheet and not sheet_cache["decode_passed"]):
            reasons.append("full_decode_or_requested_sheets_failed")
        if download is None or download_hash != video_hash:
            reasons.append("download_hash_unverified_or_mismatch")
        if (signature(video) != initial or (download and signature(download) != download_initial)
                or (same_input and not video.samefile(download))):
            reasons.append("input_changed_during_check")
        report["pending_reasons"] = reasons
        report["status"] = "pending" if reasons else "passed"
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    report["timings_seconds"]["total"] = time.perf_counter() - start
    history_path = output / "reports" / (uuid.uuid4().hex + ".json")
    atomic_json(history_path, report)
    atomic_json(report_path, report)
    print(json.dumps({"status": report["status"], "report": str(history_path), "latest_report": str(report_path),
                      "cache_hit": report["cache_hit"]}, ensure_ascii=False))
    return {"passed": 0, "pending": 2, "error": 1}[report["status"]]


def positive_float(value):
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return parsed


def positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True)
    parser.add_argument("--download")
    parser.add_argument("--output", required=True, help="Dedicated task check/cache directory")
    parser.add_argument("--expected-seconds", type=positive_float)
    parser.add_argument("--expected-width", type=positive_int)
    parser.add_argument("--expected-height", type=positive_int)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--contact-sheet", action="store_true")
    args = parser.parse_args()
    try:
        return inspect(args)
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
