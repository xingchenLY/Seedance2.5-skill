"""Build a source-grounded asset index and resolve precise generation scope."""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pipeline_common import atomic_json, emit, hash_bytes, object_hash, read_json, script_hash

CATEGORIES = {"人物", "场景", "道具"}
FILTERS = {"人物": {"outfit", "state"}, "场景": {"space", "time", "state"}, "道具": {"state"}}


class IndexErrors(ValueError):
    """All independently detectable annotation errors, without partial output."""
    def __init__(self, errors):
        self.errors = errors
        super().__init__("\n".join(errors))


def normalized(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


def extract_text(request, text_path=None):
    if request.get("script_text"):
        original = request["script_text"]
    else:
        path = Path(request["script_path"])
        if path.suffix.lower() in {".txt", ".md", ".text"}:
            original = path.read_text(encoding="utf-8-sig")
        elif path.suffix.lower() == ".docx":
            with zipfile.ZipFile(path) as archive:
                tree = ET.fromstring(archive.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            original = "\n".join("".join(t.text or "" for t in p.findall(".//w:t", ns))
                                 for p in tree.findall(".//w:p", ns))
        elif text_path:
            original = Path(text_path).read_text(encoding="utf-8-sig")
        else:
            raise ValueError("该剧本格式须先提取正文，再用 --text 指定 UTF-8 文本；不能仅凭文件存在出图")
    if text_path and Path(text_path).read_text(encoding="utf-8-sig").replace("\r\n", "\n") != original.replace("\r\n", "\n"):
        raise ValueError("提供的正文文本与可直接解析的剧本不一致")
    if not original.strip():
        raise ValueError("提取的剧本正文为空")
    return original.replace("\r\n", "\n")


def chinese_number(text):
    if text.isdigit():
        return int(text)
    digits = dict(zip("零一二三四五六七八九", range(10)))
    digits["两"] = 2
    total, digit = 0, 0
    for char in text:
        if char in digits:
            digit = digits[char]
        elif char in {"十", "百"}:
            total += (digit or 1) * {"十": 10, "百": 100}[char]
            digit = 0
        else:
            raise ValueError("无法识别的中文集数")
    return total + digit


def numbered_headers(lines):
    """Return possible bare-number headings; these require human confirmation."""
    return [(n, int(line.strip())) for n, line in enumerate(lines, 1)
            if re.fullmatch(r"0*[1-9]\d{0,2}", line.strip())]


def episode_headers(lines, confirmed_numbered=False):
    headers = []
    for line_number, line in enumerate(lines, 1):
        line = re.sub(r"^\s*#{1,6}\s*", "", line).strip()
        match = re.match(r"^第\s*([0-9零一二三四五六七八九十百两]+)\s*集(?:\s|[：:、.\-—]|$)", line)
        foreign = re.match(r"^(?:EPISODE|EP\.?|EPISODIO|EPISÓDIO|CAPÍTULO|CAPITULO)\s*(\d+)(?:\s|[：:.\-—]|$)", line, re.I)
        if match or foreign:
            number = chinese_number((match or foreign).group(1))
            if number <= 0:
                raise ValueError("剧本集数必须为正整数")
            headers.append((line_number, number))
    if confirmed_numbered:
        candidates = numbered_headers(lines)
        if not candidates:
            raise ValueError("已指定独立数字分集，但正文没有数字标题")
        if any(b[1] != a[1] + 1 for a, b in zip(candidates, candidates[1:])):
            raise ValueError("独立数字标题不是连续集数；请核对并排除页码或非正文数字")
        headers = sorted(headers + candidates)
        if len({number for _, number in headers}) != len(headers):
            raise ValueError("分集标题重复；请核对正文或目录排除范围")
    return headers


def build_index(request, annotations, text_path=None):
    source_hash = script_hash(request)
    if annotations.get("source_sha256") != source_hash:
        raise ValueError("分析标注不属于当前剧本；请使用当前 script_sha256")
    text = extract_text(request, text_path)
    lines = text.splitlines()
    scenes, errors = {}, []
    used_lines = set()
    excluded = set()
    for span in annotations.get("excluded_ranges", []):
        start, end = span["start_line"], span["end_line"]
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            errors.append("排除段落的行号无效")
            continue
        excluded.update(range(start, end + 1))
    confirmed_numbered = annotations.get("confirmed_numbered_episodes", False)
    if type(confirmed_numbered) is not bool:
        raise ValueError("confirmed_numbered_episodes 须为布尔值")
    body_lines = ["" if n in excluded else line for n, line in enumerate(lines, 1)]
    headers = episode_headers(body_lines, confirmed_numbered=confirmed_numbered)
    for scene in annotations.get("scenes", []):
        start, end = scene["start_line"], scene["end_line"]
        if not isinstance(scene.get("id"), str) or not scene["id"].strip() or scene["id"] in scenes:
            errors.append("场次 ID 必须非空且唯一：" + str(scene.get("id")))
            continue
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            errors.append(f"场次 {scene['id']}：场次行号越界")
            continue
        positions = set(range(start, end + 1))
        if positions & (used_lines | excluded):
            errors.append(f"场次 {scene['id']}：场次有重叠或落入人物小传等排除段落")
        if any(start < line <= end for line, _ in headers):
            errors.append(f"场次 {scene['id']}：单个场次不能跨越分集标题")
        preceding = [number for line, number in headers if line <= start]
        episode = preceding[-1] if preceding else None
        if "episode" in scene and scene["episode"] != episode:
            errors.append(f"场次 {scene['id']}：场次集数与正文标题不符")
        scenes[scene["id"]] = {"id": scene["id"], "start_line": start, "end_line": end, "episode": episode}
        for field in ("label", "location"):
            if field in scene:
                if not isinstance(scene[field], str) or not scene[field].strip():
                    errors.append(f"场次 {scene['id']}：{field} 须为已核对的非空文字")
                else:
                    scenes[scene["id"]][field] = scene[field].strip()
        used_lines.update(positions)
    if not scenes:
        errors.append("索引缺少正文场次，不能依据人物小传建立资产")
    assets, ids, signatures = [], set(), set()
    for raw in annotations.get("assets", []):
        item = dict(raw)
        category, name, identifier = item.get("category"), item.get("name"), item.get("id")
        if category not in CATEGORIES or not isinstance(name, str) or not name.strip():
            errors.append(f"资产 {identifier}：资产类别或名称无效")
            continue
        if not isinstance(identifier, str) or not identifier.strip() or identifier in ids:
            errors.append(f"资产 {identifier}：资产 ID 必须非空且唯一")
            continue
        ids.add(identifier)
        kind = item.get("kind", {"人物": "original", "场景": "scene", "道具": "prop"}[category])
        if kind not in ({"original", "group"} if category == "人物" else {"scene" if category == "场景" else "prop"}):
            errors.append(f"资产 {identifier}：正文索引只收录原始资产，不能预先加入三视图")
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list) or any(not isinstance(x, str) or not x.strip() for x in aliases):
            errors.append(f"资产 {identifier}：资产别名必须是非空文字数组")
            aliases = []
        variant = item.get("variant", {})
        if not isinstance(variant, dict) or set(variant) - FILTERS[category]:
            errors.append(f"资产 {identifier}：版本字段与类别不一致")
            variant = {}
        if any(not isinstance(v, str) or not v.strip() for v in variant.values()):
            errors.append(f"资产 {identifier}：版本字段须有确定描述，未知时间应省略字段")
        signature = object_hash([category, normalized(name), variant, kind])
        if signature in signatures:
            errors.append(f"资产 {identifier}：同一资产版本重复；请合并出现场次")
        occurrences = []
        for occurrence in item.get("occurrences", []):
            scene = scenes.get(occurrence.get("scene_id"))
            evidence = occurrence.get("evidence", [])
            if scene is None or not evidence:
                errors.append(f"资产 {identifier}，场次 {occurrence.get('scene_id')}：每次资产出现须有有效场次及正文证据")
                continue
            for quote in evidence:
                line, fragment = quote.get("line"), quote.get("quote")
                if type(line) is not int or not scene["start_line"] <= line <= scene["end_line"]:
                    errors.append(f"资产 {identifier}，场次 {scene['id']}，第 {line} 行：证据行号不属于对应场次")
                    continue
                if not isinstance(fragment, str) or not fragment.strip() or fragment not in lines[line - 1]:
                    matches = ([n for n in range(scene['start_line'], scene['end_line'] + 1)
                                if fragment in lines[n - 1]] if isinstance(fragment, str) and fragment.strip() else [])
                    hint = f"；相同引文在本场第 {matches} 行，请核对" if matches else ""
                    errors.append(f"资产 {identifier}，场次 {scene['id']}，第 {line} 行：引文与正文不符{hint}")
            if not any(normalized(alias) in normalized(quote.get("quote", ""))
                       for alias in [name, *aliases] for quote in evidence if isinstance(quote.get("quote"), str)):
                errors.append(f"资产 {identifier}，场次 {scene['id']}：证据未出现资产名或已核对别名；请核对称呼，不自动新增别名")
            occurrences.append({"scene_id": scene["id"], "episode": scene["episode"], "evidence": evidence})
        if not occurrences:
            errors.append(f"资产 {identifier}：资产没有正文出场依据")
        assets.append({"id": identifier, "category": category, "name": name.strip(),
                       "aliases": aliases, "variant": variant, "kind": kind, "occurrences": occurrences})
        signatures.add(signature)
    if errors:
        raise IndexErrors(errors)
    result = {"schema_version": 1, "source_sha256": source_hash, "text_sha256": hash_bytes(text.encode("utf-8")),
              "episodes": sorted(set(number for _, number in headers)), "scenes": list(scenes.values()), "assets": assets}
    result["index_sha256"] = object_hash(result)
    return result


def resolve_request(index_path, request):
    index = read_json(index_path)
    unsigned = {k: v for k, v in index.items() if k != "index_sha256"}
    if index.get("schema_version") != 1 or object_hash(unsigned) != index.get("index_sha256"):
        raise ValueError("正文索引损坏或已修改，请重新建立")
    if index["source_sha256"] != request["script_sha256"]:
        raise ValueError("剧本内容已改变，不能复用旧索引")
    episodes = request["episodes"]
    if episodes != "all" and not set(episodes).issubset(index["episodes"]):
        raise ValueError("所选集数未出现在正文分集标题中")
    catalog = []
    for item in index["assets"]:
        occurrences = [x for x in item["occurrences"] if episodes == "all" or x["episode"] in episodes]
        if occurrences and item["category"] in request["asset_types"]:
            catalog.append({**item, "occurrences": occurrences})
    catalog.sort(key=lambda x: min(q["line"] for o in x["occurrences"] for q in o["evidence"]))
    selected, counts = {}, {}
    for query in request.get("asset_requests", []):
        candidates = [x for x in catalog if x["category"] == query["category"]
                      and normalized(query["name"]) in {normalized(v) for v in [x["name"], *x["aliases"]]}]
        canonical = {x["name"] for x in candidates}
        if len(canonical) != 1:
            raise ValueError("指定资产不存在、超出集数或别名不唯一：" + query["name"])
        for field in FILTERS[query["category"]]:
            if field in query:
                candidates = [x for x in candidates if normalized(x["variant"].get(field, "")) == normalized(query[field])]
        if not candidates:
            raise ValueError("指定服装/状态/空间/时间未匹配到正文资产：" + query["name"])
        count = query.get("count", len(candidates))
        query_counts = {}
        for number in range(count):
            item = candidates[number] if number < len(candidates) else candidates[0]
            selected[item["id"]] = item
            query_counts[item["id"]] = query_counts.get(item["id"], 0) + 1
        for identifier, amount in query_counts.items():
            counts[identifier] = max(counts.get(identifier, 0), amount)
    if request["generation_mode"] == "full":
        selected = {x["id"]: x for x in catalog}
        counts = {x["id"]: 1 for x in catalog}
    turns = request.get("turnaround_characters")
    turn_names = set()
    if turns is not None:
        for name in turns:
            matches = {x["name"] for x in selected.values() if x["category"] == "人物"
                       and normalized(name) in {normalized(v) for v in [x["name"], *x["aliases"]]}}
            if len(matches) != 1:
                raise ValueError("三视图角色不属于本次唯一匹配的人物：" + name)
            turn_names.update(matches)
    tasks = []
    for item in selected.values():
        for sample in range(1, counts[item["id"]] + 1):
            identifier = object_hash([index["source_sha256"], item["id"], item["variant"], sample])[:24]
            task = {**item, "id": identifier, "catalog_id": item["id"], "sample": sample,
                    "parent_id": None, "min_short_side": request["min_short_side"],
                    "generate_image": item["category"] in request["image_types"]}
            tasks.append(task)
            if (request["generate_turnarounds"] and item["kind"] == "original"
                    and (turns is None or item["name"] in turn_names)):
                tasks.append({**task, "id": identifier + "-turnaround", "kind": "turnaround",
                              "parent_id": identifier, "generate_image": "三视图" in request["image_types"]})
    return tasks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("annotations")
    parser.add_argument("--output", required=True)
    parser.add_argument("--text")
    args = parser.parse_args()
    try:
        request = read_json(args.request)
        from input_check import validate_request
        validate_request({k: v for k, v in request.items() if k != "content_index"})
        result = build_index(request, read_json(args.annotations), args.text)
        atomic_json(args.output, result)
        emit({"ok": True, "index_path": str(Path(args.output).resolve()), "assets": len(result["assets"]), "episodes": result["episodes"]})
        return 0
    except Exception as exc:
        emit({"ok": False, "errors": getattr(exc, "errors", [str(exc)])})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
