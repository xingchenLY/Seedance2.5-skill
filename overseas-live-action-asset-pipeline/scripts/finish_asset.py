"""Record an actual visual review and archive one image; never generate or display."""
from __future__ import annotations
import argparse
from pathlib import Path
from asset_state import image_source, next_action, read_json, record_event, require_current_request
from pipeline_common import emit, io_operation, file_hash
from save_one_asset import save_one
from cleanup_pending import cleanup_pending


def asset_name(state, item):
    if item["kind"] == "turnaround":
        parent = state["assets"][item["parent_id"]]
        base = Path(parent["saved"]["path"]).stem if parent.get("saved") else asset_name(state, parent)
        return base + "-三视图"
    variant = item["variant"]
    fields = ("outfit", "state") if item["category"] == "人物" else (
        ("space", "time", "state") if item["category"] == "场景" else ("state",))
    return "-".join([item["name"], *[variant[k] for k in fields if variant.get(k)]])


@io_operation
def finish(state_path, identifier, attempt_id=None, review=None, name=None, displayed=False):
    state = read_json(state_path)
    require_current_request(state)
    item = state["assets"][identifier]
    if attempt_id is not None and attempt_id != item.get("current_attempt_id"):
        raise ValueError("attempt_id 与当前图片不符")
    base = {"id": identifier, "attempt_id": attempt_id or item.get("current_attempt_id")}
    if review is not None:
        if set(review) - {"approved", "note", "single_person", "code", "at"}:
            raise ValueError("review 只接受实际检查结论，不能替换资产 ID 或事件类型")
        result = record_event(state_path, {**base, **review, "event": "checked"})
        if not review.get("approved"):
            return {**result, "status": "rejected"}
    state = read_json(state_path)
    item = state["assets"][identifier]
    if next_action(state, item) not in {"save", "deliver", "complete"}:
        raise ValueError("本项尚未检查合格；先接收真实源图并提供视觉结论")
    result = {"ok": True, "status": "not_archived"}
    if state["save_assets"]:
        if next_action(state, item) == "save":
            try:
                result = save_one(state["request_path"], image_source(item), item["category"],
                                  name or asset_name(state, item), identifier,
                                  link_pending=state.get('asset_workflow') == 'jumeng' and bool(item.get('pending')))
                record_event(state_path, {**base, "event": "saved", "target_path": result["target_path"],
                                          "timing": result.get("timing")})
            except Exception as exc:
                try:
                    record_event(state_path, {**base, "event": "failed", "stage": "save",
                                              "code": "archive_error", "message": str(exc)})
                except Exception as recording_error:
                    # A vanished/changed source must not hide the original archive error.
                    raise exc from recording_error
                raise
        else:
            result = {"ok": True, "status": "reused", "target_path": item["saved"]["path"]}
    if state.get('asset_workflow') == 'jumeng' and state['save_assets']:
        try:
            cleanup = cleanup_pending(state_path, identifier, include_history=False)
        except (OSError, ValueError) as exc:
            cleanup = {'removed': [], 'kept': [], 'errors': [{'reason': str(exc)}]}
        if cleanup['removed']:
            result['pending_cleanup'] = 'removed_after_archive'
        elif cleanup['errors']:
            result['pending_cleanup'] = 'pending'
            result['cleanup_error'] = cleanup['errors'][0]['reason']
        elif any(x['reason'] == 'protected_reference' for x in cleanup['kept']):
            result['pending_cleanup'] = 'protected_reference'
    if displayed:
        record_event(state_path, {**base, "event": "delivered"})
    state = read_json(state_path)
    return {**result, "id": identifier, "attempt_id": base["attempt_id"],
            "next_action": next_action(state, state["assets"][identifier])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state")
    parser.add_argument("--id", required=True)
    parser.add_argument("--attempt-id")
    parser.add_argument("--review", help="JSON with actual approved/note/single_person fields")
    parser.add_argument("--name", help="Override the derived filename stem")
    parser.add_argument("--displayed", action="store_true", help="Only after actual native image display")
    args = parser.parse_args()
    try:
        result = finish(args.state, args.id, args.attempt_id,
                        read_json(args.review) if args.review else None, args.name, args.displayed)
        emit(result)
        return 0
    except Exception as exc:
        emit({"ok": False, "errors": [str(exc)]})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
