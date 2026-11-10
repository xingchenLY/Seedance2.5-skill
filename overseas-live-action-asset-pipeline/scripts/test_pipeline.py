"""Offline regression tests; synthetic scripts/images, no generation service calls."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import zipfile
import json
import subprocess
import sys
from PIL import Image

PREFLIGHT = dict(body_basis="Synthetic body verified", continuity="Same identity and state",
                 framing="Correct aspect and full margins", exclusions="No extra subjects")
import asset_state as state
from image_check import inspect_image
from input_check import RequestError, validate_request, load_and_validate
from pipeline_common import atomic_json, file_hash, read_json, script_hash
from save_one_asset import save_one
from script_index import build_index, extract_text, episode_headers

SCRIPT = "\n".join([
    "CHARACTER BIOGRAPHIES", "Emma is 90 in this discarded biography.",
    "EPISODE 99", "EPISODE 1", "INT. HOSPITAL - DAY",
    "Emma wears a red dress and carries the silver key.",
    "Liam enters the hospital in a dark suit.",
    "EPISODE 2", "INT. HOSPITAL - NIGHT",
    "Emma wears a blue coat and holds the silver key."])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="asset-pipeline-test-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.request_path = self.folder / "request.json"
        self.index_path = self.folder / "index.json"
        self.plan_path = self.folder / "plan.json"
        self.state_path = self.folder / "state.json"
        self.raw = {"asset_workflow": "codex", "region_mode": "overseas", "script_text": SCRIPT, "selected_assets": {"人物": ["艾玛"]}, "output_mode": "assets"}
        self.annotations = {
            "source_sha256": script_hash(self.raw),
            "excluded_ranges": [{"start_line": 1, "end_line": 3}],
            "scenes": [{"id": "s1", "start_line": 5, "end_line": 7, "episode": 1},
                       {"id": "s2", "start_line": 9, "end_line": 10, "episode": 2}],
            "assets": [
                self.asset("emma-red", "人物", "艾玛", "Emma", {"outfit": "红裙"}, "s1", 6),
                self.asset("emma-blue", "人物", "艾玛", "Emma", {"outfit": "蓝外套"}, "s2", 10),
                self.asset("liam", "人物", "利亚姆", "Liam", {"outfit": "深色西装"}, "s1", 7),
                self.asset("hospital", "场景", "医院", "HOSPITAL", {"space": "急诊室", "time": "白天"}, "s1", 5),
                self.asset("key", "道具", "银色钥匙", "silver key", {}, "s1", 6)]}

    def asset(self, identifier, category, name, alias, variant, scene, line):
        return {"id": identifier, "category": category, "name": name, "aliases": [alias],
                "variant": variant, "occurrences": [{"scene_id": scene,
                "evidence": [{"line": line, "quote": SCRIPT.splitlines()[line - 1]}]}]}

    def prepare(self, **changes):
        self.raw.update(changes)
        atomic_json(self.index_path, build_index(self.raw, self.annotations))
        self.raw["content_index"] = str(self.index_path)
        atomic_json(self.request_path, self.raw)
        return load_and_validate(self.request_path, require_content=True)

    def init(self, **changes):
        request = self.prepare(**changes)
        plan = {"assets": [{"id": x["id"], "preflight": PREFLIGHT, "prompt": "完整测试提示词 " + x["id"]}
                           for x in request["resolved_assets"]]}
        atomic_json(self.plan_path, plan)
        state.initialize(self.state_path, self.request_path, self.plan_path)
        return request["resolved_assets"]

    def event(self, identifier, event, **values):
        return state.record_event(self.state_path, {"id": identifier, "event": event, **values})

    def actions(self):
        return {x["id"]: x["action"] for x in state.snapshot(self.state_path)["assets"]}

    def picture(self, name="image.png", size=(576, 1024), color="red"):
        path = self.folder / name
        Image.new("RGB", size, color).save(path)
        return path

    def complete_source(self, identifier, single=True):
        source = self.picture(identifier + ".png")
        self.event(identifier, "generating", handle="tool-" + identifier)
        self.event(identifier, "generated", source_path=str(source))
        self.event(identifier, "checked", approved=True, note="人工视觉检查完成", single_person=single)
        return source

    def test_defaults_and_output_gates(self):
        selected = validate_request({k: v for k, v in self.raw.items() if k != "output_mode"})
        self.assertFalse(selected["save_assets"])
        self.assertTrue(selected["generate_workbook"])
        self.assertFalse(selected["generate_images"])
        self.assertEqual(selected["image_types"], [])
        self.assertFalse(selected["generate_turnarounds"])
        self.assertEqual(selected["episodes"], "all")
        self.assertFalse(selected["ready_for_generation"])
        full = {"asset_workflow": "codex", "region_mode": "overseas", "script_text": SCRIPT, "output_root": str(self.folder / "out")}
        self.assertTrue(validate_request(full)["generate_workbook"])
        self.assertFalse(validate_request(full)["generate_turnarounds"])
        self.assertEqual(validate_request({"asset_workflow": "codex", "region_mode": "overseas", "script_text": SCRIPT})["output_mode"], "workbook")
        for mode in ("prompts", "workbook", "images"):
            actual = validate_request({**self.raw, "output_mode": mode})
            self.assertEqual(actual["generate_images"], mode == "images")
            self.assertEqual(actual["generate_workbook"], mode == "workbook")
        self.assertFalse(validate_request({"asset_workflow": "codex", "region_mode": "overseas", "script_text": SCRIPT, "output_mode": "prompts"})["generate_images"])
        with self.assertRaises(RequestError):
            validate_request({**self.raw, "output_mode": "images", "generate_workbook": True})

    def test_invalid_explicit_scope_does_not_expand(self):
        for selection in ({}, {"人物": []}, {"人物": "*"}, {"人物": {"name": "艾玛", "count": True}},
                          {"人物": {"name": "艾玛", "count": 0}}, {"人物": {"name": "艾玛", "space": "大厅"}}):
            with self.subTest(selection=selection), self.assertRaises(RequestError):
                validate_request({**self.raw, "selected_assets": selection})
        with self.assertRaises(RequestError):
            validate_request({**self.raw, "asset_types": ["人物", "道具"]})
        with self.assertRaises(RequestError):
            load_and_validate(self.write_request(), require_content=True)

    def write_request(self):
        atomic_json(self.request_path, self.raw)
        return self.request_path

    def test_source_evidence_and_biography_exclusion(self):
        index = build_index(self.raw, self.annotations)
        self.assertEqual(index["episodes"], [1, 2])
        for modification in ("quote", "episode", "overlap", "bio", "hash"):
            ann = deepcopy(self.annotations)
            if modification == "quote": ann["assets"][0]["occurrences"][0]["evidence"][0]["quote"] = "invented"
            elif modification == "episode": ann["scenes"][0]["episode"] = 2
            elif modification == "overlap": ann["scenes"][1]["start_line"] = 6
            elif modification == "bio": ann["scenes"][0].update(start_line=2, end_line=2, episode=None)
            else: ann["source_sha256"] = "wrong"
            with self.subTest(modification=modification), self.assertRaises(ValueError):
                build_index(self.raw, ann)

    def test_scope_alias_filter_counts_and_episodes(self):
        actual = self.prepare(selected_assets={"人物": [{"name": "Emma", "outfit": "红裙", "count": 3}]})
        self.assertEqual([x["catalog_id"] for x in actual["resolved_assets"]], ["emma-red"] * 3)
        self.assertEqual([x["sample"] for x in actual["resolved_assets"]], [1, 2, 3])
        actual = self.prepare(selected_assets={"人物": [{"name": "艾玛", "count": 1}]})
        self.assertEqual(actual["resolved_assets"][0]["catalog_id"], "emma-red")
        actual = self.prepare(selected_assets={"人物": ["艾玛", {"name": "艾玛", "outfit": "红裙"}]})
        self.assertEqual(len(actual["resolved_assets"]), 2)
        for change in ({"selected_assets": {"人物": ["不存在"]}},
                       {"selected_assets": {"人物": [{"name": "艾玛", "outfit": "婚纱"}]}},
                       {"episodes": [99]}, {"episodes": [1], "selected_assets": {"人物": [{"name": "艾玛", "outfit": "蓝外套"}]}}):
            with self.subTest(change=change), self.assertRaises(RequestError):
                validate_request({**self.raw, **change})

    def test_stale_or_modified_index(self):
        self.prepare()
        with self.assertRaises(RequestError):
            validate_request({**self.raw, "script_text": SCRIPT + "\nChanged"})
        altered = read_json(self.index_path)
        altered["assets"][0]["name"] = "Other"
        atomic_json(self.index_path, altered)
        with self.assertRaises(RequestError):
            validate_request(self.raw)

    def test_alias_ambiguity_and_unsegmented_script(self):
        self.annotations["assets"][2]["aliases"].append("Emma")
        with self.assertRaises(RequestError):
            self.prepare(selected_assets={"人物": ["Emma"]})
        self.assertEqual(episode_headers(["第十二集", "EPISÓDIO 2", "CAPÍTULO 3", "EP. 4"]),
                         [(1, 12), (2, 2), (3, 3), (4, 4)])
        self.assertEqual(episode_headers(["A film without episode headers"]), [])

    def test_docx_extraction(self):
        path = self.folder / "script.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>EPISODE 1</w:t></w:r></w:p><w:p><w:r><w:t>Emma</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(extract_text({"script_path": str(path)}), "EPISODE 1\nEmma")

    def test_geometry(self):
        for category, kind, size in (("人物", "original", (576, 1024)), ("人物", "group", (800, 600)),
                                     ("人物", "turnaround", (1024, 576)), ("场景", "scene", (1024, 576)),
                                     ("道具", "prop", (576, 1024))):
            result = inspect_image(self.picture(kind + ".png", size), category, kind)
            self.assertTrue(result["ok"])
            self.assertFalse(result["visual_approved"])
        for size in ((1024, 1024), (288, 512)):
            with self.assertRaises(ValueError):
                inspect_image(self.picture("wrong.png", size), "人物")
        broken = self.folder / "broken.png"
        broken.write_bytes(b"not an image")
        with self.assertRaises(Exception): inspect_image(broken, "人物")

    def test_save_scope_geometry_and_versioning(self):
        output = self.folder / "archive"
        request = self.prepare(output_root=str(output), selected_assets={"人物": [{"name": "艾玛", "outfit": "红裙"}]})
        identifier = request["resolved_assets"][0]["id"]
        source = self.picture()
        original_hash = file_hash(source)
        with self.assertRaises(ValueError):
            save_one(self.request_path, source, "人物", "艾玛-红裙", "invalid")
        with self.assertRaises(ValueError):
            save_one(self.request_path, self.picture("wrong.png", (1024, 1024)), "人物", "艾玛-红裙", identifier)
        self.assertFalse(output.exists())
        first = save_one(self.request_path, source, "人物", "艾玛-红裙", identifier)
        second = save_one(self.request_path, source, "人物", "艾玛-红裙", identifier)
        third = save_one(self.request_path, self.picture("blue.png", color="blue"), "人物", "艾玛-红裙", identifier)
        self.assertEqual(first["status"], "saved")
        self.assertEqual(second["status"], "reused")
        self.assertTrue(third["target_path"].endswith("-v2.png"))
        self.assertEqual(file_hash(source), original_hash)

    def test_progress_wait_inspect_and_direct_delivery(self):
        tasks = self.init(selected_assets={"人物": [{"name": "艾玛", "outfit": "红裙"}]}, generate_turnarounds=True)
        parent, child = [x["id"] for x in tasks]
        self.assertEqual(self.actions(), {parent: "generate", child: "wait_for_reference"})
        self.event(parent, "generating", handle="actual-tool-handle")
        self.assertEqual(self.actions()[parent], "wait")
        with self.assertRaises(ValueError): self.event(parent, "generating", handle="duplicate")
        with self.assertRaises(ValueError): self.event(parent, "failed", stage="generation", code="timeout", message="waiting")
        self.event(parent, "generated", source_path=str(self.picture()))
        self.assertEqual(self.actions()[parent], "inspect")
        self.event(parent, "checked", approved=True, note="已查看", single_person=True)
        self.assertEqual(self.actions()[parent], "deliver")
        self.assertEqual(self.actions()[child], "wait_for_reference")
        self.event(parent, "delivered")
        self.assertEqual(self.actions(), {parent: "complete", child: "generate"})

    def test_multiple_people_skip_turnaround(self):
        tasks = self.init(selected_assets={"人物": [{"name": "艾玛", "count": 1}]}, generate_turnarounds=True)
        parent, child = [x["id"] for x in tasks]
        self.complete_source(parent, single=False)
        self.assertEqual(self.actions()[child], "skipped_multiple_people")

    def test_save_failure_and_only_archive_path_changes(self):
        tasks = self.init(selected_assets={"人物": [{"name": "艾玛", "count": 1}]}, output_root=str(self.folder / "first"))
        identifier = tasks[0]["id"]
        source = self.complete_source(identifier)
        self.event(identifier, "failed", stage="save", code="access", message="save denied")
        self.assertEqual(self.actions()[identifier], "save")
        saved = save_one(self.request_path, source, "人物", "艾玛-红裙", identifier)
        self.event(identifier, "saved", target_path=saved["target_path"])
        self.event(identifier, "delivered")
        source.unlink()
        self.assertEqual(self.actions()[identifier], "complete")
        self.raw["output_root"] = str(self.folder / "second")
        self.write_request()
        with self.assertRaises(ValueError): self.actions()
        state.initialize(self.state_path, self.request_path, self.plan_path)
        self.assertEqual(self.actions()[identifier], "save")

    def test_add_turnaround_and_change_workbook_preserve_original(self):
        original = self.init(selected_assets={"人物": [{"name": "艾玛", "count": 1}]})[0]["id"]
        self.complete_source(original)
        self.event(original, "delivered")
        self.init(generate_workbook=True, generate_turnarounds=True)
        self.assertEqual(self.actions(), {original: "complete", original + "-turnaround": "generate"})

    def test_repeated_terminal_error_pauses_only_one_asset(self):
        first, second = [x["id"] for x in self.init()]
        for number in range(6):
            self.assertEqual(self.actions()[first], "generate")
            self.event(first, "generating", handle="handle-" + str(number))
            self.event(first, "failed", stage="generation", terminal=True, code="service", message="terminal failure")
        self.assertEqual(self.actions(), {first: "paused", second: "generate"})

    def test_plan_integrity_cycle_and_atomic_failure(self):
        ids = [x["id"] for x in self.init()]
        previous = self.state_path.read_bytes()
        plan = read_json(self.plan_path)
        plan["assets"][0]["reference_ids"] = [ids[1]]
        plan["assets"][1]["reference_ids"] = [ids[0]]
        atomic_json(self.plan_path, plan)
        with self.assertRaises(ValueError): state.initialize(self.state_path, self.request_path, self.plan_path)
        self.assertEqual(self.state_path.read_bytes(), previous)
        self.assertFalse(self.state_path.with_suffix(".json.lock").exists())
        plan["assets"].pop()
        atomic_json(self.plan_path, plan)
        with self.assertRaises(ValueError): state.initialize(self.state_path, self.request_path, self.plan_path)

    def test_reference_changes_require_regeneration(self):
        identifier = self.init(selected_assets={"人物": [{"name": "艾玛", "count": 1}]})[0]["id"]
        reference = self.picture("reference.png")
        plan = read_json(self.plan_path)
        plan["assets"][0]["reference_paths"] = [str(reference)]
        atomic_json(self.plan_path, plan)
        state.initialize(self.state_path, self.request_path, self.plan_path)
        self.complete_source(identifier)
        self.event(identifier, "delivered")
        self.picture("reference.png", color="blue")
        self.assertEqual(self.actions()[identifier], "wait_for_reference")
        state.initialize(self.state_path, self.request_path, self.plan_path)
        self.assertEqual(self.actions()[identifier], "generate")

    def test_text_only_cannot_generate_or_archive(self):
        identifier = self.init(output_mode="prompts")[0]["id"]
        self.assertEqual(self.actions()[identifier], "text_only")
        with self.assertRaises(ValueError): self.event(identifier, "generating", handle="unexpected")
        with self.assertRaises(ValueError): save_one(self.request_path, self.picture(), "人物", "艾玛-红裙", identifier)

    def test_receive_running_result_after_request_changes(self):
        identifier = self.init(selected_assets={"人物": [{"name": "艾玛", "count": 1}]})[0]["id"]
        self.event(identifier, "generating", handle="existing-handle")
        self.raw["output_mode"] = "workbook"
        self.write_request()
        self.event(identifier, "generated", source_path=str(self.picture()))
        with self.assertRaises(ValueError):
            self.event(identifier, "checked", approved=True, single_person=True, note="stale scope")
        state.initialize(self.state_path, self.request_path, self.plan_path)
        self.assertEqual(self.actions()[identifier], "text_only")

    def test_five_quality_retries_then_pause(self):
        identifier = self.init(selected_assets={"人物": [{"name": "艾玛", "count": 1}]})[0]["id"]
        for number in range(6):
            self.assertIn(self.actions()[identifier], {"generate", "retry_generation"})
            self.event(identifier, "generating", handle="quality-" + str(number))
            self.event(identifier, "generated", source_path=str(self.picture()))
            self.event(identifier, "checked", approved=False, single_person=True,
                       code="quality-" + str(number), note="visible defect")
            self.assertEqual(self.actions()[identifier], "paused" if number == 5 else "retry_generation")

    def test_resume_paused_after_fix(self):
        identifier = self.init(selected_assets={"人物": [{"name": "艾玛", "count": 1}]})[0]["id"]
        for _ in range(6):
            self.event(identifier, "generating", handle="existing-handle")
            self.event(identifier, "failed", stage="generation", terminal=True, code="service", message="unavailable")
        with self.assertRaises(ValueError): self.event(identifier, "retry")
        self.event(identifier, "retry", note="Service recovery verified")
        self.assertEqual(self.actions()[identifier], "generate")

    def test_cli_build_index_with_turnaround_alias(self):
        self.raw["turnaround_characters"] = ["Emma"]
        self.write_request()
        annotations_path = self.folder / "annotations.json"
        atomic_json(annotations_path, self.annotations)
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(Path(__file__).with_name("script_index.py")),
                                 str(self.request_path), str(annotations_path), "--output", str(self.index_path)],
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.raw["content_index"] = str(self.index_path)
        self.write_request()
        request = load_and_validate(self.request_path, require_content=True)
        self.assertEqual(len(request["resolved_assets"]), 4)

    def test_stream_save_continues_after_invalid_item(self):
        request = self.prepare(output_root=str(self.folder / "out"), selected_assets={"人物": [{"name": "艾玛", "count": 1}]})
        identifier = request["resolved_assets"][0]["id"]
        command = {"id": "request-1", "asset_id": "unknown", "source": str(self.picture()), "category": "人物", "name": "艾玛-红裙"}
        lines = [command, {**command, "id": "request-2", "asset_id": identifier}, {"op": "close"}]
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(Path(__file__).with_name("save_one_asset.py")),
                                 str(self.request_path), "--stream"], input="\n".join(json.dumps(x) for x in lines) + "\n",
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(records[0]["status"], "ready")
        self.assertFalse(records[1]["ok"])
        self.assertEqual(records[2]["status"], "saved")
        self.assertEqual(records[3]["status"], "closed")


if __name__ == "__main__":
    unittest.main()
