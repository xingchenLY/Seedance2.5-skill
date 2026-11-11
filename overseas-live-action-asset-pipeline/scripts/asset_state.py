"""Atomic per-image progress and resumable next actions, without generation APIs."""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import os
import json
import math
from time import perf_counter
from copy import deepcopy
from uuid import uuid4
from image_check import inspect_image
from input_check import load_and_validate
from pipeline_common import atomic_json, emit, file_hash, object_hash, read_json, io_operation


def now():
    return datetime.now(timezone.utc).isoformat()


PREFLIGHT_FIELDS = ("body_basis", "continuity", "framing", "exclusions")


def checked_preflight(value, design_hash):
    if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or not value[k].strip()
                                          for k in PREFLIGHT_FIELDS):
        raise ValueError("出图前检查须写明 body_basis、continuity、framing、exclusions 四项实际结论")
    return {**{k: value[k].strip() for k in PREFLIGHT_FIELDS}, "design_hash": design_hash, "at": now()}


def ready_preflight(item):
    return item.get("preflight", {}).get("design_hash") == item.get("design_hash") and bool(item.get("preflight"))


def event_time(event):
    value = event.get("at", now())
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed > datetime.now(timezone.utc):
        raise ValueError("事件 at 须为带时区的实际发生时间，不能填未来时间")
    return parsed.astimezone(timezone.utc).isoformat()


def current_attempt(item):
    return next((a for a in item.get("attempts", []) if a["id"] == item.get("current_attempt_id")), None)


@contextmanager
def locked(path):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    try:
        stream = lock.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise ValueError("进度记录正由另一写入者处理；请串行提交，不覆盖现有锁") from exc
    try:
        with stream:
            stream.write(str(os.getpid()))
        yield path
    finally:
        lock.unlink()


def verified_file(record):
    if not record:
        return None
    path = Path(record["path"])
    return str(path) if path.is_file() and file_hash(path) == record["sha256"] else None


def image_source(item):
    return (verified_file(item.get("pending")) or verified_file(item.get("source"))
            or verified_file(item.get("saved")))


def image_enabled(state, item):
    return state["generate_images"] and item.get("generate_image", True)


def reference_source(state, parent):
    """Disabled originals can supply checked existing images without new output."""
    if not image_enabled(state, parent):
        provided = verified_file(parent.get("reference_image"))
        if provided:
            return provided
        return image_source(parent) if parent.get("review", {}).get("approved") else None
    if not parent.get("review", {}).get("approved"):
        return None
    if state["save_assets"]:
        return verified_file(parent.get("saved"))
    return image_source(parent) if parent.get("delivered") else None


def checked_reference_image(value, scope):
    if (scope["kind"] != "original" or not isinstance(value, dict)
            or value.get("single_person") is not True
            or not isinstance(value.get("note"), str) or not value["note"].strip()):
        raise ValueError("已有原照须实际检查为单人，提供 reference_image 的 path、single_person=true 和 note")
    path = Path(value.get("path", ""))
    if not path.is_absolute() or not path.is_file():
        raise ValueError("已有原照须为可读取的绝对路径")
    info = inspect_image(path, scope["category"], scope["kind"], scope["min_short_side"])
    return {"path": str(path.resolve()), "sha256": info["sha256"],
            "single_person": True, "note": value["note"].strip()}


def copy_timing(event):
    value = event.get('timing')
    if value is None:
        return None
    keys = {'copy_seconds', 'verify_seconds', 'save_seconds'}
    if not isinstance(value, dict) or set(value) != keys or any(
            type(x) not in (int, float) or not math.isfinite(x) or x < 0 for x in value.values()):
        raise ValueError('保存计时须为实际非负秒数：copy_seconds/verify_seconds/save_seconds')
    return dict(value)


def references_current(state, item):
    values = []
    for reference in item["reference_paths"]:
        if not verified_file(reference):
            return None
        values.append(reference["sha256"])
    for identifier in item["reference_ids"]:
        parent = state["assets"][identifier]
        source = reference_source(state, parent)
        if not source:
            return None
        if item["kind"] == "turnaround" and identifier == item.get("parent_id"):
            review = (parent["reference_image"] if not image_enabled(state, parent)
                      and verified_file(parent.get("reference_image")) else parent.get("review", {}))
            if review.get("single_person") is not True:
                return None
        values.append(file_hash(source))
    return values


def design_fingerprint(prompt, kind, variant, min_short_side, reference_ids, reference_paths, region_mode):
    # Preserve overseas design hashes so an explicit legacy selection can reuse accepted images.
    values = [prompt, kind, variant, min_short_side, reference_ids, reference_paths]
    if region_mode == "domestic":
        values.append({"region_mode": "domestic"})
    return object_hash(values)


def request_signature(request):
    values = {key: request.get(key) for key in (
        "script_sha256", "asset_workflow", "region_mode", "output_root", "output_mode", "image_types", "generate_images", "save_assets",
        "generate_workbook", "generate_turnarounds", "turnaround_characters", "episodes",
        "asset_requests", "asset_types", "min_short_side", "resolved_assets")}
    if request.get("asset_workflow") == "jumeng" and request.get("jumeng_project_name"):
        values["jumeng_project_name"] = request["jumeng_project_name"]
    if request.get("asset_workflow") == "jumeng" and request.get("jumeng_operation_mode"):
        values["jumeng_operation_mode"] = request["jumeng_operation_mode"]
    return object_hash(values)


def require_current_request(state):
    request = load_and_validate(state["request_path"], require_content=True)
    if request_signature(request) != state["request_signature"]:
        raise ValueError("请求或正文范围已变化，请先用当前 request 与 plan 重新执行 init")


def next_action(state, item):
    if item.get("generation"):
        return "wait"
    if not image_enabled(state, item):
        return "text_only"
    if item.get("parent_id"):
        parent = state["assets"][item["parent_id"]]
        if ((image_enabled(state, parent) or not verified_file(parent.get("reference_image")))
                and parent.get("review", {}).get("approved") and parent["review"].get("single_person") is False):
            return "skipped_multiple_people"
    if not item.get("prompt"):
        return "design"
    source = image_source(item)
    # Route only a new/retry generation. Existing results keep the common
    # inspection, pending-save, archive and delivery lifecycle regardless of route.
    if not source or item.get("review", {}).get("approved") is False:
        if state.get("asset_workflow") is None:
            return "choose_image_workflow"
        if state["asset_workflow"] == "jumeng":
            if not state.get("jumeng_project_name"):
                return "choose_jumeng_project"
            if state.get("jumeng_operation_mode") not in {"web", "cli"}:
                return "choose_jumeng_operation_mode"
    references = references_current(state, item)
    if references is None:
        return "wait_for_reference"
    if item.get("used_reference_hashes") is not None and item["used_reference_hashes"] != references:
        return "replan_references"
    failure = item.get("failure", {})
    if failure.get("stage") in {"generation", "quality"} and failure.get("count", 0) >= 6:  # Initial attempt plus five regeneration retries.
        return "paused"
    if source and item.get("review", {}).get("approved") is False:
        return "retry_generation" if ready_preflight(item) else "preflight"
    if source and not item.get("review", {}).get("approved"):
        return "inspect"
    if source:
        if state["save_assets"] and not verified_file(item.get("saved")):
            return "save"
        if not item.get("delivered"):
            return "deliver"
        return "complete"
    return "generate" if ready_preflight(item) else "preflight"


def validate_dependencies(assets):
    visiting, visited = set(), set()
    def visit(identifier):
        if identifier in visiting:
            raise ValueError("图片引用存在循环依赖")
        if identifier in visited:
            return
        if identifier not in assets:
            raise ValueError("引用了本次计划以外的资产 ID")
        visiting.add(identifier)
        for other in assets[identifier]["reference_ids"]:
            visit(other)
        visiting.remove(identifier)
        visited.add(identifier)
    for identifier in assets:
        visit(identifier)


@io_operation
def initialize(state_path, request_path, plan_path, partial=False):
    request = load_and_validate(request_path, require_content=True)
    plan = read_json(plan_path)
    entries = plan.get("assets", [])
    by_id = {entry["id"]: entry for entry in entries}
    resolved = {entry["id"]: entry for entry in request["resolved_assets"]}
    if partial and not request["generate_images"]:
        raise ValueError("仅提示词或表格模式须提交完整计划，不使用分批设计")
    if len(by_id) != len(entries) or (set(by_id) - set(resolved)) or (not partial and set(by_id) != set(resolved)):
        raise ValueError("提示词计划必须与正文匹配结果逐项一致，不能漏项或增加资产")
    with locked(state_path) as path:
        old = read_json(path) if path.exists() else {"assets": {}, "revision": 0}
        if old["assets"] and old.get("region_mode", "overseas") != request["region_mode"] and partial:
            raise ValueError("切换国内/海外模式须重新核对并提交完整提示词计划，不能自动沿用未提交的旧模式设计")
        assets = {}
        for identifier, scope in resolved.items():
            prior = old["assets"].get(identifier)
            entry = by_id.get(identifier)
            if entry is None and prior and prior.get("prompt"):
                # Partial updates append designs; omission never erases a completed
                # or in-flight asset. Hash reference files again when reinitializing.
                entry = {"prompt": prior["prompt"], "reference_ids": prior["reference_ids"],
                         "reference_paths": [r["path"] for r in prior["reference_paths"]]}
            if entry is None:
                assets[identifier] = {**scope, "prompt": None, "design_hash": None,
                    "reference_ids": [scope["parent_id"]] if scope["parent_id"] else [],
                    "reference_paths": [], "events": [], "created_at": now()}
                continue
            prompt = entry.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("每个计划条目须有完整提示词")
            reference_ids = list(dict.fromkeys(entry.get("reference_ids", []) + ([scope["parent_id"]] if scope["parent_id"] else [])))
            reference_paths = []
            for raw_path in entry.get("reference_paths", []):
                reference = Path(raw_path).expanduser()
                if not reference.is_absolute() or not reference.is_file():
                    raise ValueError("参考图路径必须绝对且可读取")
                reference_paths.append({"path": str(reference.resolve()), "sha256": file_hash(reference)})
            design_hash = design_fingerprint(prompt, scope["kind"], scope["variant"], request["min_short_side"],
                                             reference_ids, reference_paths, request["region_mode"])
            if prior and prior["design_hash"] == design_hash:
                item = dict(prior)
            else:
                if prior and prior.get("generation"):
                    raise ValueError("旧设计仍在生成；先接收原任务结果，再调整设计")
                item = {"events": deepcopy(prior.get("events", [])) if prior else [],
                        "attempts": deepcopy(prior.get("attempts", [])) if prior else [],
                        "created_at": prior.get("created_at", now()) if prior else now()}
                # A prompt repair must not erase the retry budget or prior evidence.
                if prior and prior.get("failure", {}).get("stage") in {"generation", "quality"}:
                    item["failure"] = deepcopy(prior["failure"])
            item.update({**scope, "prompt": prompt, "design_hash": design_hash,
                         "reference_ids": reference_ids, "reference_paths": reference_paths})
            if "reference_image" in entry:
                if entry["reference_image"] is None:
                    item.pop("reference_image", None)
                else:
                    if scope["generate_image"]:
                        raise ValueError("reference_image 用于关闭原照出图时复用已有原照；不能同时要求重生原照")
                    item["reference_image"] = checked_reference_image(entry["reference_image"], scope)
            if "preflight" in entry:
                item["preflight"] = checked_preflight(entry["preflight"], design_hash)
            # Changing only the archive directory must not reuse an old target as the new archive.
            if old.get("output_root") != request["output_root"]:
                if item.get("pending") and not verified_file(item.get("source")):
                    item["source"] = dict(item["pending"])
                item.pop("pending", None)
                if item.get("saved") and not verified_file(item.get("source")):
                    item["source"] = dict(item["saved"])
                item.pop("saved", None)
            assets[identifier] = item
        for identifier, item in old["assets"].items():
            if identifier not in assets and item.get("generation"):
                raise ValueError("有移出范围的任务仍在生成；先处理原任务句柄，不能丢弃")
        validate_dependencies(assets)
        state = {"schema_version": 2, "revision": old["revision"] + 1,
                 "asset_workflow": request["asset_workflow"],
                 "jumeng_project_name": request.get("jumeng_project_name"),
                 "jumeng_operation_mode": request.get("jumeng_operation_mode"),
                 "region_mode": request["region_mode"],
                 "script_sha256": request["script_sha256"], "request_path": str(Path(request_path).resolve()),
                 "output_root": request["output_root"], "save_assets": request["save_assets"],
                 "generate_images": request["generate_images"], "assets": assets, "updated_at": now(),
                 "request_signature": request_signature(request)}
        for item in assets.values():
            current_references = references_current(state, item)
            if (current_references is not None and item.get("used_reference_hashes") is not None
                    and item["used_reference_hashes"] != current_references):
                if item.get("generation"):
                    raise ValueError("引用图变化但原生成任务仍在途，请先接收原任务")
                for key in ("source", "pending", "saved", "review", "delivered", "used_reference_hashes", "accepted_attempt_id", "preflight"):
                    item.pop(key, None)
        atomic_json(path, state)
    return {"ok": True, "state_path": str(path), "assets": len(assets),
            "pending_design": sum(not x.get("prompt") for x in assets.values()), "revision": state["revision"]}


@io_operation
def record_event(state_path, event):
    with locked(state_path) as path:
        state = read_json(path)
        identifier, operation = event["id"], event["event"]
        # Accept the result of an already submitted request even after user scope changes.
        # New work remains blocked until the updated request/plan is initialized.
        receiving = operation == "generated" or (operation == "failed" and event.get("stage") == "generation")
        if not receiving:
            require_current_request(state)
        item = state["assets"][identifier]
        occurred_at = event_time(event)
        fingerprint = object_hash({k: v for k, v in event.items() if k not in {"at", "event_id"}})
        event_id = event.get("event_id")
        if event_id:
            prior_event = next((e for e in item["events"] if e.get("event_id") == event_id), None)
            if prior_event:
                if prior_event.get("fingerprint") != fingerprint:
                    raise ValueError("同一 event_id 不能对应不同事件内容")
                return {"ok": True, "id": identifier, "duplicate": True,
                        "next_action": next_action(state, item), "revision": state["revision"]}
        attempt = current_attempt(item)
        attempt_id = event.get("attempt_id")
        if operation == "generating" and attempt_id:
            previous = next((a for a in item.get("attempts", []) if a["id"] == attempt_id), None)
            if previous:
                if previous.get("start_fingerprint") != fingerprint:
                    raise ValueError("同一 attempt_id 不能用于另一次请求")
                return {"ok": True, "id": identifier, "attempt_id": attempt_id, "duplicate": True,
                        "next_action": next_action(state, item), "revision": state["revision"]}
        elif attempt_id and (not attempt or attempt_id != attempt["id"]):
            raise ValueError("结果的 attempt_id 与当前尝试不符；拒绝把旧结果绑定到新请求")
        outcome_key = operation
        if operation in {"saved", "staged"}:
            outcome_key += ":" + str(event.get("target_path"))
        elif operation == "failed":
            outcome_key += ":" + str(event.get("stage")) + ":" + str(event.get("code"))
        if operation not in {"generating", "preflight", "retry"} and attempt:
            previous = attempt.get("outcomes", {}).get(outcome_key)
            if previous == fingerprint:
                return {"ok": True, "id": identifier, "attempt_id": attempt["id"], "duplicate": True,
                        "next_action": next_action(state, item), "revision": state["revision"]}
            if previous and operation in {"generated", "checked"}:
                raise ValueError("同一次尝试已记录不同结果；不能覆盖历史图片或检查结论")
            if datetime.fromisoformat(occurred_at) < datetime.fromisoformat(attempt["submitted_at"]):
                raise ValueError("事件时间不能早于本次提交时间")
            if (operation in {"checked", "staged", "saved", "delivered"} and attempt.get("returned_at")
                    and datetime.fromisoformat(occurred_at) < datetime.fromisoformat(attempt["returned_at"])):
                raise ValueError("检查或保存时间不能早于图片返回时间")
        action = next_action(state, item)
        if not image_enabled(state, item) and not receiving:
            raise ValueError("本项未选择图片输出，不能记录图片生成任务")
        if operation == "verify_legacy_prompt":
            if item.get("attempts") or item.get("generation") or not image_source(item) or not item.get("review", {}).get("approved"):
                raise ValueError("仅无逐次记录的已检查旧图可核对最终提示词")
            prompt, note = event.get("prompt"), event.get("note")
            if not isinstance(prompt, str) or not prompt.strip() or not isinstance(note, str) or not note.strip():
                raise ValueError("须提供已对照原工具记录及成片核对的完整提示词和说明")
            item["prompt"] = prompt
            item["design_hash"] = design_fingerprint(prompt, item["kind"], item["variant"], item["min_short_side"],
                                                       item["reference_ids"], item["reference_paths"], state["region_mode"])
            item["legacy_prompt_verified"] = {"prompt_hash": object_hash(prompt), "sha256": item["review"]["sha256"],
                                               "note": note, "at": occurred_at}
        elif operation == "preflight":
            if not item.get("prompt") or item.get("generation"):
                raise ValueError("先完成提示词，且不能在生成途中更改检查结论")
            item["preflight"] = checked_preflight(event.get("checks"), item["design_hash"])
        elif operation == "generating":
            supported = state.get("asset_workflow") == "codex" or (state.get("asset_workflow") == "jumeng" and state.get("jumeng_project_name") and state.get("jumeng_operation_mode") in {"web", "cli"})
            if not supported:
                raise ValueError("出图前须确定流程、填写剧梦项目名并选择网页或 Dreama CLI 操作模式")
            if action not in {"generate", "retry_generation"}:
                raise ValueError("本项不能重新提交生成，当前动作：" + action)
            if not isinstance(event.get("handle"), str) or not event["handle"].strip():
                raise ValueError("生成开始须记录请求标识或工具句柄")
            attempt_id = attempt_id or str(uuid4())
            tool_prompt = event.get("tool_prompt", item["prompt"])
            if not isinstance(tool_prompt, str) or not tool_prompt.strip():
                raise ValueError("tool_prompt 须为实际提交的非空提示词")
            actual_references = [dict(r) for r in item["reference_paths"]]
            for ref in item["reference_ids"]:
                parent = state["assets"][ref]
                reference_path = reference_source(state, parent)
                actual_references.append({"id": ref, "path": reference_path, "sha256": file_hash(reference_path)})
            attempt = {"id": attempt_id, "handle": event["handle"], "submitted_at": occurred_at,
                       "asset_workflow": state["asset_workflow"],
                       "prompt": item["prompt"], "tool_prompt": tool_prompt, "design_hash": item["design_hash"],
                       "references": actual_references, "preflight": deepcopy(item["preflight"]),
                       "start_fingerprint": fingerprint, "outcomes": {}}
            if state["asset_workflow"] == "jumeng":
                attempt["jumeng_operation_mode"] = state["jumeng_operation_mode"]
            item.setdefault("attempts", []).append(attempt)
            item["current_attempt_id"] = attempt_id
            item["generation"] = {"handle": event["handle"], "attempt_id": attempt_id, "started_at": occurred_at}
            item["used_reference_hashes"] = references_current(state, item)
            for key in ("source", "pending", "review", "saved", "delivered", "accepted_attempt_id"):
                item.pop(key, None)
        elif operation == "generated":
            if not item.get("generation"):
                raise ValueError("没有对应的在途请求，不能接收生成结果")
            source = Path(event["source_path"])
            if not source.is_absolute() or not source.is_file():
                raise ValueError("生成结果源文件不可读取")
            item["source"] = {"path": str(source.resolve()), "sha256": file_hash(source)}
            if attempt:
                attempt.update(source=dict(item["source"]), returned_at=occurred_at)
            if item.get("failure", {}).get("stage") == "generation":
                item.pop("failure", None)
            item.pop("generation", None)
        elif operation == "staged":
            target = Path(event['target_path']).resolve()
            if not state['save_assets'] or item.get('generation') or not item.get('source'):
                raise ValueError('须先接收本次生成结果，才可记录待审保存')
            root = Path(state['output_root']).resolve()
            pending_root = (root / '待审').resolve()
            if (pending_root == root or not pending_root.is_relative_to(root) or not target.is_relative_to(pending_root)
                    or not target.is_file() or file_hash(target) != item['source']['sha256']):
                raise ValueError('待审文件须在本次待审目录内且与返回源图一致')
            item['pending'] = {'path': str(target), 'sha256': item['source']['sha256']}
            timing = copy_timing(event)
            if attempt:
                attempt.update(pending=dict(item['pending']), staged_at=occurred_at)
                if timing is not None:
                    attempt['pending_timing'] = timing
        elif operation == "checked":
            source = image_source(item)
            if not source or action not in {"inspect", "retry_generation"}:
                raise ValueError("没有待检查的完整源图")
            if type(event.get("approved")) is not bool or not str(event.get("note", "")).strip():
                raise ValueError("视觉检查须有明确结论和简短说明")
            if event["approved"]:
                geometry_started = perf_counter()
                dimensions = inspect_image(source, item["category"], item["kind"], item["min_short_side"])
                geometry_seconds = perf_counter() - geometry_started
                if item["kind"] == "original" and type(event.get("single_person")) is not bool:
                    raise ValueError("人物原照须记录是否只有一人")
                item["review"] = {"approved": True, "single_person": event.get("single_person"), "note": event["note"],
                                  "sha256": dimensions["sha256"], "width": dimensions["width"], "height": dimensions["height"],
                                  "aspect_ratio": dimensions["aspect_ratio"]}
                item.pop("failure", None)
                if attempt:
                    item["accepted_attempt_id"] = attempt["id"]
                    attempt['geometry_seconds'] = geometry_seconds
            else:
                item["review"] = {"approved": False, "note": event["note"]}
                add_failure(item, "quality", event.get("code", "visual_rejected"), event["note"])
            if attempt:
                attempt.update(review=deepcopy(item["review"]), checked_at=occurred_at)
        elif operation == "delivered":
            if not image_source(item) or not item.get("review", {}).get("approved"):
                raise ValueError("只有已检查的有效图片才能记为交付")
            item["delivered"] = True
            if attempt:
                attempt["delivered_at"] = occurred_at
        elif operation == "saved":
            target = Path(event["target_path"]).resolve()
            source = image_source(item)
            if not state["save_assets"] or not source or not item.get("review", {}).get("approved"):
                raise ValueError("本项尚不能记为归档完成")
            if not target.is_relative_to(Path(state["output_root"]).resolve()) or not target.is_file():
                raise ValueError("保存结果未落在本次主路径内")
            if target.is_relative_to((Path(state['output_root']) / '待审').resolve()):
                raise ValueError('待审副本不能记为正式归档')
            if file_hash(target) != file_hash(source):
                raise ValueError("保存结果与已检查源图不一致")
            item["saved"] = {"path": str(target), "sha256": file_hash(target)}
            if attempt:
                attempt.update(saved=dict(item["saved"]), saved_at=occurred_at)
                timing = copy_timing(event)
                if timing is not None:
                    attempt['archive_timing'] = timing
            if item.get("failure", {}).get("stage") == "save":
                item.pop("failure", None)
        elif operation == "retry":
            if action != "paused" or not isinstance(event.get("note"), str) or not event["note"].strip():
                raise ValueError("仅暂停项可恢复；须记录已修正的问题或明确重试依据")
            item.pop("failure", None)
        elif operation == "failed":
            stage = event.get("stage")
            if stage not in {"generation", "save", "quality"}:
                raise ValueError("失败阶段必须为 generation/save/quality")
            if stage == "generation":
                if not item.get("generation") or event.get("terminal") is not True:
                    raise ValueError("仅明确终止的生成请求可记失败；等待超时须继续原任务")
                item.pop("generation", None)
                if attempt:
                    attempt["failed_at"] = occurred_at
            if stage == "quality":
                if not image_source(item) or item.get("review", {}).get("approved"):
                    raise ValueError("质量失败须对应当前待检查或已拒绝源图")
                item["review"] = {"approved": False, "note": event.get("message", "quality check failed")}
                if attempt:
                    attempt["review"] = deepcopy(item["review"])
                    attempt.setdefault("checked_at", occurred_at)
            if stage == "save" and (not image_source(item) or not item.get("review", {}).get("approved")):
                raise ValueError("保存失败须对应已检查合格的源图")
            add_failure(item, stage, event.get("code"), event.get("message"))
        else:
            raise ValueError("未知进度事件")
        if attempt and operation not in {"preflight", "retry"}:
            attempt.setdefault("outcomes", {})[outcome_key] = fingerprint
        item["events"].append({"event": operation, "at": occurred_at, "recorded_at": now(),
                               "attempt_id": attempt["id"] if attempt else None,
                               "event_id": event_id, "fingerprint": fingerprint})
        state["revision"] += 1
        state["updated_at"] = now()
        atomic_json(path, state)
    return {"ok": True, "id": identifier, "attempt_id": item.get("current_attempt_id"),
            "next_action": next_action(state, item), "revision": state["revision"]}


def add_failure(item, stage, code, message):
    if not isinstance(code, str) or not code or not isinstance(message, str) or not message:
        raise ValueError("失败记录须有错误代码和原因")
    old = item.get("failure", {})
    attempt = current_attempt(item)
    # One quality failure per returned image, even checked(false) + failed(quality).
    if attempt:
        failures = attempt.setdefault("failures", {})
        if stage in failures:
            return
        failures[stage] = {"code": code, "message": message}
    identity = item.get("current_attempt_id") or item.get("source", {}).get("sha256")
    if identity and old.get("attempt_id") == identity and old.get("stage") == stage:
        return
    count = old.get("count", 0) + 1 if old.get("stage") == stage else 1
    item["failure"] = {"stage": stage, "code": code, "message": message, "count": count,
                       "attempt_id": identity}


def metrics(state_path):
    state = read_json(state_path)
    require_current_request(state)
    attempts = [a for item in state["assets"].values() for a in item.get("attempts", [])]
    def interval(a, start, end):
        if not a.get(start) or not a.get(end):
            return None
        return round((datetime.fromisoformat(a[end]) - datetime.fromisoformat(a[start])).total_seconds(), 3)
    return {"ok": True, "assets": len(state["assets"]), "recorded_attempts": len(attempts),
            "legacy_assets_without_attempt_log": sum(bool(x.get("source")) and not x.get("attempts")
                                                     for x in state["assets"].values()),
            "accepted_attempts": sum(bool(x.get("accepted_attempt_id")) for x in state["assets"].values()),
            "first_pass_approved_assets": sum(bool(x.get("attempts")) and x["attempts"][0].get("review", {}).get("approved") is True
                                              for x in state["assets"].values()),
            "retried_assets": sum(len(x.get("attempts", [])) > 1 for x in state["assets"].values()),
            "timings": [{"attempt_id": a["id"], "generation_seconds": interval(a, "submitted_at", "returned_at"),
                         "review_seconds": interval(a, "returned_at", "checked_at"),
                         "archive_seconds": interval(a, "checked_at", "saved_at"),
                         "pending_seconds": interval(a, "returned_at", "staged_at"),
                         "geometry_seconds": a.get('geometry_seconds'),
                         "pending_copy_seconds": a.get('pending_timing', {}).get('copy_seconds'),
                         "pending_save_seconds": a.get('pending_timing', {}).get('save_seconds'),
                         "archive_copy_seconds": a.get('archive_timing', {}).get('copy_seconds'),
                         "archive_verify_seconds": a.get('archive_timing', {}).get('verify_seconds'),
                         "archive_save_seconds": a.get('archive_timing', {}).get('save_seconds')} for a in attempts],
            "note": "未记录为 null；generation/review/archive/pending_seconds 是事件间隔，含等待或部分检查；"
                    "copy_seconds 为实际复制及同步计算哈希耗时，save_seconds 为保存函数耗时；嵌套或并行时段不累加"}


@io_operation
def record_events(state_path, events):
    """Commit an ordered batch. Report the committed prefix on any failure."""
    if isinstance(events, dict):
        return record_event(state_path, events)
    if not isinstance(events, list) or not events:
        raise ValueError("事件须为对象或非空事件数组")
    results = []
    for position, event in enumerate(events):
        try:
            results.append(record_event(state_path, event))
        except Exception as exc:
            return {"ok": False, "committed": len(results), "results": results,
                    "failed_index": position, "errors": [str(exc)]}
    return {"ok": True, "committed": len(results), "results": results}


@io_operation
def snapshot(state_path, actions=None, limit=None, include_prompt=False, summarize=True, lanes=False):
    state = read_json(state_path)
    require_current_request(state)
    if limit is not None and limit <= 0:
        raise ValueError("limit 必须为正整数")
    records, counts = [], Counter()
    lane_actions = {'generate': {'generate', 'retry_generation'}, 'postprocess': {'inspect', 'save', 'deliver'}}
    lane_records = {key: [] for key in lane_actions}
    if lanes and (actions or include_prompt):
        raise ValueError('--lanes 不与 --actions 或 --include-prompt 同用；仅实际提交项另取提示词')
    scanned = 0
    for key, item in state["assets"].items():
        if not summarize and limit is not None:
            if (all(len(values) >= limit for values in lane_records.values()) if lanes else len(records) >= limit):
                break
        scanned += 1
        action = next_action(state, item)
        counts[action] += 1
        if actions and action not in actions:
            continue
        lane = next((key for key, values in lane_actions.items() if action in values), None) if lanes else None
        if lanes:
            if lane is None or (limit is not None and len(lane_records[lane]) >= limit):
                continue
        elif limit is not None and len(records) >= limit:
            continue
        entry = {"id": key, "name": item["name"], "kind": item["kind"], "action": action,
                 "source_path": image_source(item), "handle": item.get("generation", {}).get("handle"),
                 "attempt_id": item.get("current_attempt_id")}
        if include_prompt:
            entry["prompt"] = item.get("prompt")
            entry["reference_ids"] = item["reference_ids"]
            entry["reference_paths"] = ([r["path"] for r in item["reference_paths"]] +
                [reference_source(state, state["assets"][ref]) for ref in item["reference_ids"]])
        (lane_records[lane] if lanes else records).append(entry)
    if lanes:
        return {"ok": True, "revision": state["revision"], "queues": lane_records, "counts": dict(counts),
                "counts_complete": scanned == len(state['assets']), "scanned_assets": scanned,
                "note": "每队列独立限额；未返回不等于不存在，平台在途沿用实际句柄映射"}
    return {"ok": True, "revision": state["revision"], "counts": dict(counts), "assets": records,
            "counts_complete": scanned == len(state['assets']), "scanned_assets": scanned}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize_parser = commands.add_parser("init")
    initialize_parser.add_argument("state")
    initialize_parser.add_argument("request")
    initialize_parser.add_argument("plan")
    initialize_parser.add_argument("--partial", action="store_true")
    record_parser = commands.add_parser("record")
    record_parser.add_argument("state")
    record_parser.add_argument("event", nargs="?")
    record_parser.add_argument("--json", dest="event_json")
    next_parser = commands.add_parser("next")
    next_parser.add_argument("state")
    next_parser.add_argument("--actions", nargs="+")
    next_parser.add_argument("--lanes", action="store_true", help="Return separate generation and postprocess queues in one snapshot")
    next_parser.add_argument("--limit", type=int)
    next_parser.add_argument("--include-prompt", action="store_true")
    next_parser.add_argument("--no-summary", action="store_true",
                             help="Stop at limit; counts describe only inspected assets")
    metrics_parser = commands.add_parser("metrics")
    metrics_parser.add_argument("state")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize(args.state, args.request, args.plan, partial=args.partial)
        elif args.command == "record":
            if bool(args.event) == (args.event_json is not None):
                raise ValueError("事件文件与 --json 必须且只能提供一个")
            events = json.loads(args.event_json) if args.event_json is not None else read_json(args.event)
            result = record_events(args.state, events)
        elif args.command == "metrics":
            result = metrics(args.state)
        else:
            result = snapshot(args.state, actions=args.actions, limit=args.limit, include_prompt=args.include_prompt,
                              summarize=not args.no_summary, lanes=args.lanes)
        emit(result)
        return 0 if result["ok"] else 2
    except Exception as exc:
        emit({"ok": False, "errors": [str(exc)]})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
