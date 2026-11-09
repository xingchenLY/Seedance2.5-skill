"""Image routing is optional for preparation and shared result storage."""
import unittest
import asset_state as state
import prepare as prep
from finish_asset import finish
from receive_asset import receive
from input_check import RequestError, normalize_asset_workflow, validate_request
from pipeline_common import atomic_json, read_json, file_hash
from save_one_asset import save_one
from workbook_data import build_payload
import test_pipeline as fixtures
from pathlib import Path


class AssetWorkflowTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    asset = fixtures.PipelineTests.asset
    prepare = fixtures.PipelineTests.prepare
    init = fixtures.PipelineTests.init
    event = fixtures.PipelineTests.event
    actions = fixtures.PipelineTests.actions
    picture = fixtures.PipelineTests.picture
    complete_source = fixtures.PipelineTests.complete_source

    def test_table_and_prompt_requests_do_not_require_an_image_route(self):
        self.raw.pop("asset_workflow")
        for mode in (None, "workbook", "prompts"):
            request = validate_request({**self.raw, "output_mode": mode})
            self.assertIsNone(request["asset_workflow"])
            self.assertFalse(request["generate_images"])
        self.init(output_mode="workbook")
        payload = build_payload(self.state_path)
        self.assertEqual(set(self.actions().values()), {"text_only"})
        self.assertEqual(payload["unresolved_images"], [])
        self.assertEqual(len(payload["sheets"][0]["rows"]), 2)

    def test_aliases_optional_values_and_invalid_choices(self):
        for alias in ("Codex", "CODEX", " codex ", "Codex流程"):
            self.assertEqual(normalize_asset_workflow(alias), "codex")
        for alias in ("剧梦", "剧梦流程", "JUMENG"):
            self.assertEqual(normalize_asset_workflow(alias), "jumeng")
        for value in (None, "", " "):
            self.assertIsNone(normalize_asset_workflow(value))
        for value in (True, 1, {}, [], ["codex", "jumeng"], "auto", "国内"):
            with self.subTest(value=value), self.assertRaises(RequestError):
                validate_request({**self.raw, "asset_workflow": value})

    def test_preparation_is_shared_by_unselected_codex_and_jumeng(self):
        for workflow in (None, "codex", "jumeng"):
            with self.subTest(workflow=workflow):
                self.raw.update(asset_workflow=workflow, output_mode="workbook")
                atomic_json(self.request_path, self.raw)
                result = prep.extract(self.request_path, self.folder / "work")
                self.assertEqual(result["asset_workflow"], workflow)
                self.assertTrue(Path(result["source_path"]).is_file())
                annotations = self.folder / "annotations.json"
                atomic_json(annotations, self.annotations)
                indexed = prep.index(self.request_path, annotations, self.folder / "work")
                self.assertEqual(indexed["asset_workflow"], workflow)
                self.assertFalse(indexed["ready_for_generation"])
                self.init()
                self.assertEqual(len(build_payload(self.state_path)["sheets"][0]["rows"]), 2)

    def test_jumeng_does_not_bypass_shared_region_validation(self):
        for workflow in (None, "codex", "jumeng"):
            with self.subTest(workflow=workflow), self.assertRaisesRegex(RequestError, "请先选择本次模式"):
                validate_request({"asset_workflow": workflow, "script_text": fixtures.SCRIPT})

    def test_jumeng_allows_domestic_and_overseas_table_completion(self):
        for region in ("国内", "海外"):
            self.init(asset_workflow="jumeng", region_mode=region, output_mode="workbook")
            self.assertEqual(set(self.actions().values()), {"text_only"})
            self.assertEqual(build_payload(self.state_path)["unresolved_images"], [])
            self.assertEqual(read_json(self.state_path)["asset_workflow"], "jumeng")

    def test_unselected_route_blocks_only_new_generation(self):
        identifier = self.init(asset_workflow=None)[0]["id"]
        self.assertEqual(self.actions()[identifier], "choose_image_workflow")
        self.assertEqual(len(build_payload(self.state_path)["sheets"][0]["rows"]), 2)
        with self.assertRaisesRegex(ValueError, "出图前须选择"):
            self.event(identifier, "generating", handle="not-authorized")
        self.assertFalse(validate_request(self.raw)["ready_for_generation"])

    def test_jumeng_route_blocks_only_new_generation(self):
        identifier = self.init(asset_workflow="jumeng")[0]["id"]
        self.assertEqual(self.actions()[identifier], "wait_for_workflow_import")
        self.assertEqual(len(build_payload(self.state_path)["sheets"][0]["rows"]), 2)
        with self.assertRaisesRegex(ValueError, "待导入"):
            self.event(identifier, "generating", handle="must-not-submit")
        self.assertFalse(validate_request(self.raw)["ready_for_generation"])

    def test_selecting_codex_preserves_the_prepared_table_and_design(self):
        identifier = self.init(asset_workflow=None)[0]["id"]
        payload = build_payload(self.state_path)
        before = read_json(self.state_path)["assets"][identifier]
        self.prepare(asset_workflow="codex")
        state.initialize(self.state_path, self.request_path, self.plan_path)
        self.assertEqual(build_payload(self.state_path)["content_hash"], payload["content_hash"])
        self.assertEqual(read_json(self.state_path)["assets"][identifier]["design_hash"], before["design_hash"])
        self.assertEqual(self.actions()[identifier], "generate")
        self.assertTrue(validate_request(self.raw)["ready_for_generation"])

    def test_existing_results_use_original_pending_review_and_archive_after_switch(self):
        identifier = self.init(selected_assets={"人物": [{"name": "艾玛", "count": 1}]},
                               output_root=str(self.folder / "delivery"))[0]["id"]
        self.event(identifier, "generating", handle="codex-request", attempt_id="codex-attempt")
        self.prepare(asset_workflow="jumeng")
        state.initialize(self.state_path, self.request_path, self.plan_path)
        source = self.picture()
        received = receive(self.state_path, identifier, "codex-attempt", source)
        self.assertTrue(received["ok"])
        self.assertEqual(received["status"], "pending_saved")
        self.assertEqual(self.actions()[identifier], "inspect")
        self.assertEqual(Path(received["target_path"]).parent, self.folder / "delivery" / "待审" / "人物")
        result = finish(self.state_path, identifier, attempt_id="codex-attempt",
                        review={"approved": True, "single_person": True, "note": "Synthetic fixture reviewed"})
        self.assertTrue(result["ok"])
        item = read_json(self.state_path)["assets"][identifier]
        self.assertEqual(Path(item["saved"]["path"]), self.folder / "delivery" / "人物" / "艾玛-红裙.png")
        self.assertEqual(file_hash(item["saved"]["path"]), file_hash(source))
        self.assertEqual(item["attempts"][0]["asset_workflow"], "codex")
        self.assertEqual(self.actions()[identifier], "deliver")
        self.event(identifier, "delivered")
        self.assertEqual(self.actions()[identifier], "complete")

    def test_saving_existing_images_does_not_depend_on_generation_route(self):
        source = self.picture()
        results = []
        for workflow in (None, "jumeng"):
            identifier = self.init(asset_workflow=workflow,
                                   selected_assets={"人物": [{"name": "艾玛", "count": 1}]},
                                   output_root=str(self.folder / "delivery"))[0]["id"]
            results.append(save_one(self.request_path, source, "人物", "艾玛-红裙", identifier))
        self.assertEqual(results[0]["target_path"], results[1]["target_path"])
        self.assertEqual(results[1]["status"], "reused")

    def test_clearing_route_preserves_accepted_images(self):
        identifier = self.init()[0]["id"]
        source = self.complete_source(identifier)
        self.event(identifier, "delivered")
        accepted = read_json(self.state_path)["assets"][identifier]["accepted_attempt_id"]
        self.init(asset_workflow=None)
        self.assertEqual(self.actions()[identifier], "complete")
        self.assertEqual(read_json(self.state_path)["assets"][identifier]["accepted_attempt_id"], accepted)
        self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
