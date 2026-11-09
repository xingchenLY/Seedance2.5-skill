"""Read-only validation for one user-supplied asset request."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sys
from pipeline_common import script_hash
from image_check import DEFAULT_MIN_SHORT_SIDE

ASSET_CATEGORIES = ("人物", "场景", "道具")


def normalize_asset_types(value):
    if value is None or value == "" or value == []:
        return list(ASSET_CATEGORIES)
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"all", "全部", "全部资产", "所有"}:
            return list(ASSET_CATEGORIES)
        value = re.split(r"[,，、;/；\s]+", text)
    if not isinstance(value, list) or not value:
        raise ValueError("资产类型无效；请使用人物、场景、道具中的一个或多个，或全部")
    aliases = {"人物": "人物", "角色": "人物", "character": "人物", "characters": "人物",
               "场景": "场景", "scene": "场景", "scenes": "场景",
               "道具": "道具", "prop": "道具", "props": "道具"}
    selected = set()
    for item in value:
        key = item.strip().lower() if isinstance(item, str) else None
        if key not in aliases:
            raise ValueError("资产类型无效；明确给出的无效类别不能默认扩大为全部")
        selected.add(aliases[key])
    return [category for category in ASSET_CATEGORIES if category in selected]


def normalize_generate_workbook(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return default
        if text in {"true", "yes", "是", "生成", "保存", "生成并保存", "需要"}:
            return True
        if text in {"false", "no", "否", "不生成", "不保存", "不需要"}:
            return False
    raise ValueError("资产表格选项无效；请使用生成/不生成、是/否或布尔值，未填按本次模式与保存路径采用默认值")


def normalize_names(value, label):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value:
        raise ValueError(label + "必须填写具体名称，不能使用空名单")
    names = []
    for item in value:
        if (not isinstance(item, str) or not item.strip()
                or item.strip().lower() in {"*", "all", "全部", "所有"}):
            raise ValueError(label + "必须填写具体名称，不能以全部或通配符代替")
        name = item.strip()
        if name not in names:
            names.append(name)
    return names


FILTER_FIELDS = {"人物": {"outfit", "state"}, "场景": {"space", "time", "state"},
                 "道具": {"state"}}


def normalize_asset_requests(value):
    if value is None:
        return []
    if not isinstance(value, dict) or not value:
        raise ValueError("指定资产必须按类别填写非空名称或版本条件")
    requests = []
    seen = {}
    for raw_category, entries in value.items():
        categories = normalize_asset_types(raw_category)
        if len(categories) != 1:
            raise ValueError("指定资产的每个键只能是一个类别")
        category = categories[0]
        if isinstance(entries, (str, dict)):
            entries = [entries]
        if not isinstance(entries, list) or not entries:
            raise ValueError("指定资产不能是空名单")
        for entry in entries:
            if isinstance(entry, str):
                entry = {"name": entry}
            if not isinstance(entry, dict) or "name" not in entry:
                raise ValueError("指定资产条目必须包含 name")
            if set(entry) - ({"name", "count"} | FILTER_FIELDS[category]):
                raise ValueError("指定资产含不适用于该类别的版本字段")
            item = {"category": category, "name": normalize_names(entry["name"], "资产名称")[0]}
            if not isinstance(entry["name"], str):
                raise ValueError("每个版本条目的 name 必须是单个名称")
            for field in FILTER_FIELDS[category]:
                if field in entry:
                    if not isinstance(entry[field], str) or not entry[field].strip():
                        raise ValueError("版本筛选条件必须是非空文字：" + field)
                    item[field] = entry[field].strip()
            if "count" in entry:
                if type(entry["count"]) is not int or entry["count"] <= 0:
                    raise ValueError("count 必须是正整数，表示原照/场景/道具的成片张数")
                item["count"] = entry["count"]
            key = tuple(sorted((k, v) for k, v in item.items() if k != "count"))
            if key in seen:
                if seen[key].get("count") != item.get("count"):
                    raise ValueError("同一资产版本的张数要求冲突")
                continue
            seen[key] = item
            requests.append(item)
    return sorted(requests, key=lambda item: ASSET_CATEGORIES.index(item["category"]))


def normalize_selected_assets(value):
    selected = {}
    for item in normalize_asset_requests(value):
        selected.setdefault(item["category"], [])
        if item["name"] not in selected[item["category"]]:
            selected[item["category"]].append(item["name"])
    return selected


def normalize_asset_workflow(value):
    aliases = {"codex": "codex", "codex流程": "codex",
               "剧梦": "jumeng", "剧梦流程": "jumeng", "jumeng": "jumeng"}
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or value.strip().lower() not in aliases:
        raise ValueError("asset_workflow 必须选择 Codex/codex 或剧梦/jumeng，不能自动推断或同时选择")
    return aliases[value.strip().lower()]


def normalize_region_mode(value):
    aliases = {"国内": "domestic", "国内模式": "domestic", "domestic": "domestic",
               "海外": "overseas", "海外模式": "overseas", "overseas": "overseas"}
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("请先选择本次模式：国内（亚洲人物、场景道具中文）或海外（沿用原模板）")
    if not isinstance(value, str) or value.strip().lower() not in aliases:
        raise ValueError("region_mode 必须明确选择国内/domestic 或海外/overseas，不可自动推断或同时选择")
    return aliases[value.strip().lower()]


def normalize_output_mode(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return "workbook"
    aliases = {"assets": "assets", "正常生成": "assets", "完整流程": "assets",
               "images": "images", "只要图片": "images", "只出图": "images",
               "workbook": "workbook", "只要表格": "workbook",
               "prompts": "prompts", "只要提示词": "prompts"}
    if not isinstance(value, str) or value.strip().lower() not in aliases:
        raise ValueError("输出类型必须是 assets/images/workbook/prompts")
    return aliases[value.strip().lower()]


def normalize_image_types(value):
    """None means unspecified; an empty list explicitly disables every image."""
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    aliases = {"人物": "人物", "人物原照": "人物", "character": "人物", "characters": "人物",
               "三视图": "三视图", "人物三视图": "三视图", "turnaround": "三视图", "turnarounds": "三视图",
               "场景": "场景", "scene": "场景", "scenes": "场景",
               "道具": "道具", "prop": "道具", "props": "道具"}
    if not isinstance(value, list) or any(
            not isinstance(x, str) or x.strip().lower() not in aliases for x in value):
        raise ValueError("image_types 只能选择人物、三视图、场景、道具；空数组表示全部关闭")
    return list(dict.fromkeys(aliases[x.strip().lower()] for x in value))


def asset_types_omitted(value):
    return value is None or value == [] or (isinstance(value, str) and not value.strip())


def normalize_generate_turnarounds(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return default
        if text in {"true", "yes", "是", "需要", "生成", "需要三视图", "生成三视图"}:
            return True
        if text in {"false", "no", "否", "不需要", "不生成", "不要三视图", "不生成三视图"}:
            return False
    raise ValueError("三视图选项无效；请使用需要/不需要、是/否或布尔值")


class RequestError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def normalize_episodes(value):
    if value is None or value == "" or value == []:
        return "all"
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"all", "全部", "全剧", "全集"}:
            return "all"
        text = re.sub(r"\s+", "", text)
        for old, new in [("第", ""), ("集", ""), ("，", ","), ("、", ","),
                         ("至", "-"), ("到", "-"), ("~", "-"), ("—", "-"), ("–", "-")]:
            text = text.replace(old, new)
        values = []
        for part in text.split(","):
            match = re.fullmatch(r"([1-9]\d*)(?:-([1-9]\d*))?", part)
            if not match:
                raise ValueError("集数无效；请使用正整数、整数数组、1-3,5 或全部")
            start = int(match.group(1))
            end = int(match.group(2) or start)
            if end < start:
                raise ValueError("集数范围的结束集不能小于开始集")
            values.extend(range(start, end + 1))
        return sorted(set(values))
    if isinstance(value, int) and not isinstance(value, bool):
        value = [value]
    if isinstance(value, list) and value and all(type(x) is int and x > 0 for x in value):
        return sorted(set(value))
    raise ValueError("集数无效；明确给出的无效集数不能默认扩大为全部")


def validate_request(data):
    if not isinstance(data, dict):
        raise RequestError(["请求必须是一个 JSON 对象"])
    try:
        workflow = normalize_asset_workflow(data.get("asset_workflow"))
    except ValueError as exc:
        raise RequestError([str(exc)]) from exc
    errors = []
    result = {"asset_workflow": workflow}
    project_name = data.get("jumeng_project_name")
    if project_name is not None and (not isinstance(project_name, str) or any(c in project_name for c in "\r\n")):
        errors.append("jumeng_project_name 须为单行项目名称，不能用剧名或目录自动代填")
        project_name = None
    result["jumeng_project_name"] = (project_name.strip() or None) if isinstance(project_name, str) else None
    try:
        result["region_mode"] = normalize_region_mode(data.get("region_mode"))
    except ValueError as exc:
        errors.append(str(exc))
    selected = {}
    try:
        result["asset_requests"] = normalize_asset_requests(data.get("selected_assets"))
        selected = normalize_selected_assets(data.get("selected_assets"))
    except ValueError as exc:
        errors.append(str(exc))
    selective = data.get("selected_assets") is not None
    result["generation_mode"] = "selected" if selective else "full"
    result["selected_assets"] = selected
    mode = "workbook"
    image_types = None
    explicit_turns = False
    try:
        image_types = normalize_image_types(data.get("image_types"))
        explicit_turns = normalize_generate_turnarounds(
            data.get("generate_turnarounds"), default=data.get("turnaround_characters") is not None)
        mode = normalize_output_mode(data.get("output_mode"))
        if not str(data.get("output_mode") or "").strip() and (image_types or explicit_turns):
            mode = "assets"
    except ValueError as exc:
        errors.append(str(exc))
    result["output_mode"] = mode
    min_side = data.get("min_short_side", DEFAULT_MIN_SHORT_SIDE)
    if type(min_side) is not int or min_side <= 0:
        errors.append("min_short_side 必须是正整数")
    result["min_short_side"] = min_side
    raw_path = data.get("script_path")
    raw_text = data.get("script_text")
    has_path = isinstance(raw_path, str) and bool(raw_path.strip())
    has_text = isinstance(raw_text, str) and bool(raw_text.strip())
    if not has_path and not has_text:
        errors.append("缺少可用剧本：请提供附件、本地文件或正文")
    elif has_path and has_text:
        errors.append("script_path 与 script_text 只能指定一个，请先确定本次剧本")
    elif has_path:
        try:
            script = Path(raw_path.strip()).expanduser()
            if not script.is_absolute():
                raise ValueError("剧本文件路径必须是绝对路径")
            script = script.resolve()
            if not script.is_file():
                raise ValueError("剧本文件不存在或不是普通文件")
            with script.open("rb") as stream:
                if not stream.read(1):
                    raise ValueError("剧本文件为空")
            result["script"] = {"kind": "file", "path": str(script)}
        except (OSError, ValueError) as exc:
            errors.append("剧本不可用：" + str(exc))
    else:
        result["script"] = {"kind": "text", "characters": len(raw_text.strip())}

    raw_root = data.get("output_root")
    result["output_root"] = None
    root_missing = raw_root is None or (isinstance(raw_root, str) and not raw_root.strip())
    if not root_missing and not isinstance(raw_root, str):
        errors.append("主路径无效：已提供的主路径必须是绝对路径字符串")
    elif not root_missing:
        try:
            root = Path(raw_root.strip()).expanduser()
            if not root.is_absolute():
                raise ValueError("主路径必须是绝对路径")
            root = root.resolve()
            if root.exists() and not root.is_dir():
                raise ValueError("主路径已存在但不是目录")
            ancestor = root
            while not ancestor.exists() and ancestor != ancestor.parent:
                ancestor = ancestor.parent
            if ancestor.exists() and not ancestor.is_dir():
                raise ValueError("主路径的上级路径不是目录")
            result["output_root"] = str(root)
        except (OSError, ValueError) as exc:
            errors.append("主路径无效：" + str(exc))
    try:
        book_default = mode in {"workbook", "assets"}
        book = normalize_generate_workbook(data.get("generate_workbook"), default=book_default)
        if (mode in {"images", "prompts"} and book) or (mode == "workbook" and not book):
            raise ValueError("输出类型与资产表格选项冲突")
        result["generate_workbook"] = book
    except ValueError as exc:
        errors.append(str(exc))
    try:
        result["episodes"] = normalize_episodes(data.get("episodes"))
    except ValueError as exc:
        errors.append(str(exc))
    categories = []
    try:
        raw_categories = data.get("asset_types")
        categories = (list(selected) if selective and asset_types_omitted(raw_categories)
                      else normalize_asset_types(raw_categories))
        if selective and set(categories) != set(selected):
            raise ValueError("资产类型与指定资产不一致；请为每个所选类别点名资产，不能扩大生成范围")
        result["asset_types"] = categories
    except ValueError as exc:
        errors.append(str(exc))
    try:
        requested_characters = data.get("turnaround_characters")
        turnaround_characters = (normalize_names(requested_characters, "三视图角色")
                                 if requested_characters is not None else None)
        make_turnarounds = normalize_generate_turnarounds(
            data.get("generate_turnarounds"),
            default=(turnaround_characters is not None or bool(image_types and "三视图" in image_types)))
        if turnaround_characters and not make_turnarounds:
            raise ValueError("三视图角色名单与不生成三视图的选项冲突")
        if image_types is not None and make_turnarounds != ("三视图" in image_types):
            raise ValueError("image_types 与三视图选项冲突，请统一本次图片选择")
        if make_turnarounds and "人物" not in categories:
            raise ValueError("本次未选择人物，不能增加三视图任务")
        if mode in {"workbook", "prompts"} and image_types:
            raise ValueError("只要表格或提示词时不能同时选择图片类别")
        if image_types is None:
            # Explicit legacy image modes retain base images; turnarounds always opt in.
            # A turnaround-only request without a mode must not silently add originals.
            turns_only = not str(data.get("output_mode") or "").strip() and explicit_turns
            image_types = (([] if turns_only else list(categories)) +
                           (["三视图"] if make_turnarounds else [])) if mode in {"assets", "images"} else []
        for image_type in image_types:
            if ("人物" if image_type == "三视图" else image_type) not in categories:
                raise ValueError("图片选择超出本次资产范围：" + image_type)
        if mode == "images" and not image_types:
            raise ValueError("只要图片时至少选择一种图片类别")
        result["image_types"] = image_types
        result["generate_images"] = bool(image_types)
        result["save_assets"] = result["output_root"] is not None and result["generate_images"]
        if root_missing and not selective and result["generate_images"]:
            raise ValueError("缺少主路径：全量或仅按类别出图时，请提供本次图片保存目录")
        # Resolve aliases and membership against the body index before production.
        result["generate_turnarounds"] = make_turnarounds
        result["turnaround_characters"] = (turnaround_characters if turnaround_characters is not None
                                              else (selected.get("人物") if selective and make_turnarounds else None))
    except ValueError as exc:
        errors.append(str(exc))
    if errors:
        raise RequestError(errors)
    if result["generate_images"] and workflow is None:
        workflow = "jumeng"
        result["asset_workflow"] = workflow
    # Excel destination is independent of the image archive directory.
    result["workbook_output_dir"] = None
    workbook_dir = data.get("workbook_output_dir")
    if workbook_dir is not None and not isinstance(workbook_dir, str):
        raise RequestError(["workbook_output_dir 须为 Excel 保存目录的绝对路径"])
    if isinstance(workbook_dir, str) and workbook_dir.strip():
        target = Path(workbook_dir.strip()).expanduser()
        if not target.is_absolute():
            raise RequestError(["Excel 保存目录必须是绝对路径"])
        target = target.resolve()
        ancestor = target
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        if ancestor.exists() and not ancestor.is_dir():
            raise RequestError(["Excel 保存目录或其上级路径不是目录"])
        workbook_dir = str(target)
    else:
        workbook_dir = None
    if result["generate_workbook"]:
        result["workbook_output_dir"] = workbook_dir
        if result["workbook_output_dir"] is None and result["script"]["kind"] == "file":
            result["workbook_output_dir"] = str(Path(result["script"]["path"]).parent)
    try:
        result["script_sha256"] = script_hash(data)
    except OSError as exc:
        raise RequestError(["无法读取当前剧本内容：" + str(exc)]) from exc
    result["content_validated"] = False
    result["ready_for_generation"] = False
    if data.get("content_index"):
        try:
            from script_index import resolve_request
            result["resolved_assets"] = resolve_request(data["content_index"], result)
            result["content_index"] = str(Path(data["content_index"]).resolve())
            result["content_validated"] = True
            result["ready_for_generation"] = result["generate_images"] and workflow == "codex"
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise RequestError(["正文内容校验失败：" + str(exc)]) from exc
    jumeng_images = workflow == "jumeng" and result["generate_images"]
    result["jumeng_project_required"] = jumeng_images and not result["jumeng_project_name"]
    result["ready_for_jumeng_setup"] = bool(jumeng_images and result["jumeng_project_name"] and result["content_validated"])
    categories = {x["category"] for x in result.get("resolved_assets", []) if x.get("generate_image")}
    result["jumeng_episode_names"] = [c + "资产" for c in ("人物", "场景", "道具") if jumeng_images and c in categories]
    enabled = [x for x in result.get("resolved_assets", []) if x.get("generate_image")]
    if result["ready_for_jumeng_setup"] and enabled:
        result["ready_for_generation"] = True
    return {"ok": True, **result}


def load_and_validate(request_path, require_content=False):
    try:
        data = json.loads(Path(request_path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise RequestError(["无法读取请求记录：" + str(exc)]) from exc
    result = validate_request(data)
    if require_content and not result["content_validated"]:
        raise RequestError(["尚未完成正文索引匹配；请先建立 content_index，再开始资产生产"])
    return result


def main():
    parser = argparse.ArgumentParser(description="Validate inputs without creating output files")
    parser.add_argument("request", help="Internal request JSON file")
    parser.add_argument("--require-content", action="store_true")
    args = parser.parse_args()
    try:
        result = load_and_validate(args.request, args.require_content)
    except RequestError as exc:
        print(json.dumps({"ok": False, "errors": exc.errors}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
