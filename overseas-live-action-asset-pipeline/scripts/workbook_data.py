"""Build fixed five-column workbook data from the authoritative state only."""
from __future__ import annotations
import argparse
from pathlib import Path
import re
from asset_state import current_attempt, next_action, require_current_request
from input_check import load_and_validate
from pipeline_common import atomic_json, emit, file_hash, object_hash, read_json

SHEETS = {
    "人物": ("人物资产", "characters.xlsx", ["角色名", "服装", "场次", "场景", "人物文生图提示词"]),
    "场景": ("场景资产", "scenes.xlsx", ["场景", "场景房间", "时间", "涉及场次", "文生图提示词参考"]),
    "道具": ("道具资产", "props.xlsx", ["序号", "道具名称", "首次出现", "后续出现", "文生图提示词"]),
}


def final_prompt(state, item):
    prompt = item.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("仍有未设计项，不能导出不完整表格：" + item["id"])
    if item.get("review", {}).get("approved") and item.get("accepted_attempt_id"):
        attempt = current_attempt(item)
        if (not attempt or attempt["id"] != item["accepted_attempt_id"]
                or attempt["design_hash"] != item["design_hash"] or attempt["prompt"] != prompt
                or attempt.get("source", {}).get("sha256") != item["review"].get("sha256")):
            raise ValueError("最终提示词与通过检查的图片记录不一致：" + item["id"])
        return attempt["prompt"]
    return prompt


def template_style(path, headers):
    # Reading template metadata only; workbook authoring stays with Artifact Tool.
    from openpyxl import load_workbook
    wb = load_workbook(path)
    sheet = wb.active
    if [sheet.cell(1, n).value for n in range(1, 6)] != headers:
        raise ValueError("模板表头与固定五列不一致：" + path.name)
    cell = sheet.cell(1, 1)
    color = cell.fill.fgColor
    style = {"header_fill": "#" + color.rgb[-6:] if color.type == "rgb" else "#14C0FF",
             "font_name": cell.font.name or "宋体", "header_size": cell.font.sz or 11,
             "header_height": sheet.row_dimensions[1].height or 29,
             "widths": [sheet.column_dimensions[c].width for c in "ABCDE"],
             "template_sha256": file_hash(path)}
    wb.close()
    return style


def group_scene_rows(rows, asset_ids):
    """Group chronological rows by location then room without losing row identity."""
    groups = {}
    for row, asset_id in zip(rows, asset_ids, strict=True):
        groups.setdefault(row[0], {}).setdefault(row[1], []).append((row, asset_id))
    grouped = [entry for rooms in groups.values() for entries in rooms.values() for entry in entries]
    return [row for row, _ in grouped], [asset_id for _, asset_id in grouped]


def group_character_items(items):
    """Keep roles, base outfits and states together in first-appearance order."""
    groups = {}
    for item in items:
        variant = item["variant"]
        states = groups.setdefault(item["name"], {}).setdefault(variant.get("outfit"), {})
        states.setdefault(variant.get("state"), []).append(item)
    return [item for outfits in groups.values() for states in outfits.values()
            for versions in states.values() for item in versions]


def build_payload(state_path, asset_dir=None, previous_payload=None):
    state = read_json(state_path)
    require_current_request(state)
    request = load_and_validate(state["request_path"], require_content=True)
    if not request["generate_workbook"]:
        raise ValueError("本次未选择表格输出")
    if set(state["assets"]) != {x["id"] for x in request["resolved_assets"]}:
        raise ValueError("进度与本次正文范围不一致")
    asset_dir = Path(asset_dir) if asset_dir else Path(__file__).resolve().parent.parent / "assets"
    scenes = {s["id"]: s for s in read_json(request["content_index"])["scenes"]}
    sheets, unresolved, legacy = [], [], []
    for category, (name, template, headers) in SHEETS.items():
        if category not in request["asset_types"]:
            continue
        items = [x for x in state["assets"].values() if x["category"] == category]
        items.sort(key=lambda x: min(q["line"] for o in x["occurrences"] for q in o["evidence"]))
        if category == "人物":
            items = group_character_items(items)
        rows, row_ids = [], []
        for item in items:
            # Image progress covers all tasks, including turnarounds omitted from Excel.
            action = next_action(state, item)
            if action not in {"text_only", "complete", "skipped_multiple_people"}:
                unresolved.append({"id": item["id"], "action": action})
            if category == "人物" and item["kind"] == "turnaround":
                continue
            prompt = final_prompt(state, item)
            occurrences = sorted(item["occurrences"], key=lambda o: min(q["line"] for q in o["evidence"]))
            scene_ids = list(dict.fromkeys(o["scene_id"] for o in occurrences))
            labels = [(f"第{scenes[s]['episode']}集·" if scenes[s]["episode"] else "") + scenes[s].get("label", s)
                      for s in scene_ids]
            variant = item["variant"]
            if category == "人物":
                outfit = "·".join(variant[k] for k in ("outfit", "state") if variant.get(k))
                places = list(dict.fromkeys(scenes[s].get("location", s) for s in scene_ids))
                row = [item["name"], outfit, "、".join(labels), "、".join(places), prompt]
            elif category == "场景":
                row = [item["name"], variant.get("space"), variant.get("time"), "、".join(labels), prompt]
            else:
                row = [len(rows) + 1, item["name"], labels[0], "、".join(labels[1:]) or "—", prompt]
            rows.append(row)
            if any(isinstance(v, str) and re.search(r"[A-Za-z]:[\\/]|(?:https?|file)://|\\\\", v) for v in row):
                raise ValueError("表格内容含文件路径或链接，请在 state 中修正提示词或正文显示字段：" + item["id"])
            row_ids.append(item["id"])
            if item.get("review", {}).get("approved") and not item.get("accepted_attempt_id"):
                verified = item.get("legacy_prompt_verified", {})
                if verified.get("prompt_hash") != object_hash(prompt) or verified.get("sha256") != item["review"].get("sha256"):
                    legacy.append(item["id"])
        if category == "场景":
            rows, row_ids = group_scene_rows(rows, row_ids)
        cached = next((s for s in (previous_payload or {}).get("sheets", []) if s.get("name") == name), {})
        if cached.get("style", {}).get("template_sha256") == file_hash(asset_dir / template):
            style = cached["style"]
        else:
            style = template_style(asset_dir / template, headers)
        sheets.append({"name": name, "headers": headers, "rows": rows, "asset_ids": row_ids, "style": style,
                       "content_hash": object_hash([headers, rows, style])})
    return {"schema_version": 1, "state_revision": state["revision"], "sheets": sheets,
            "unresolved_images": unresolved, "legacy_prompt_verification_required": legacy,
            "content_hash": object_hash([s["content_hash"] for s in sheets])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        previous = read_json(args.output) if Path(args.output).is_file() else None
        result = build_payload(args.state, previous_payload=previous)
        atomic_json(args.output, result)
        emit({"ok": True, "output": str(Path(args.output).resolve()),
              "rows": {s["name"]: len(s["rows"]) for s in result["sheets"]},
              "unresolved_images": result["unresolved_images"],
              "legacy_prompt_verification_required": result["legacy_prompt_verification_required"]})
        return 0
    except Exception as exc:
        emit({"ok": False, "errors": [str(exc)]})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
