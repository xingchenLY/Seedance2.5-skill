"""Offline tests for explicit region selection and design continuity."""
import unittest
import asset_state as state
from input_check import RequestError, validate_request
from pipeline_common import atomic_json, read_json, object_hash
import prepare as prep
import test_pipeline as fixtures
from workbook_data import build_payload


class RegionModeTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    asset = fixtures.PipelineTests.asset
    prepare = fixtures.PipelineTests.prepare
    init = fixtures.PipelineTests.init
    event = fixtures.PipelineTests.event
    actions = fixtures.PipelineTests.actions
    picture = fixtures.PipelineTests.picture
    complete_source = fixtures.PipelineTests.complete_source

    def test_mode_is_required_for_every_output(self):
        for mode in (None, "workbook", "prompts", "images", "assets"):
            for script in (fixtures.SCRIPT, "第一集\n内景 医院 日\n林安走进急诊室。"):
                raw = {**self.raw, "script_text": script, "output_mode": mode}
                raw.pop("region_mode")
                with self.subTest(mode=mode, script=script), self.assertRaisesRegex(RequestError, "请先选择"):
                    validate_request(raw)

    def test_aliases_normalize_without_changing_output_defaults(self):
        for alias, expected in (("国内", "domestic"), ("国内模式", "domestic"),
                                (" DOMESTIC ", "domestic"), ("海外", "overseas"),
                                ("海外模式", "overseas"), ("OVERSEAS", "overseas")):
            request = validate_request({**self.raw, "output_mode": None, "region_mode": alias})
            self.assertEqual(request["region_mode"], expected)
            self.assertTrue(request["generate_workbook"])
            self.assertFalse(request["generate_images"])
            self.assertFalse(request["generate_turnarounds"])
            self.assertEqual(request["image_types"], [])

    def test_invalid_or_multiple_choices_are_not_guessed(self):
        for value in (None, "", " ", True, 1, "auto", "中文", "国内和海外",
                      ["domestic", "overseas"], {}):
            with self.subTest(value=value), self.assertRaises(RequestError):
                validate_request({**self.raw, "region_mode": value})

    def test_extract_stops_before_creating_output_when_mode_missing(self):
        self.raw.pop("region_mode")
        atomic_json(self.request_path, self.raw)
        work = self.folder / "unselected-work"
        with self.assertRaises(RequestError):
            prep.extract(self.request_path, work)
        self.assertFalse(work.exists())

    def test_mode_is_reported_and_persisted_through_preparation(self):
        self.raw.update(region_mode="国内", output_mode="workbook")
        atomic_json(self.request_path, self.raw)
        result = prep.extract(self.request_path, self.folder / "work")
        self.assertEqual(result["region_mode"], "domestic")
        annotations = self.folder / "annotations.json"
        atomic_json(annotations, self.annotations)
        result = prep.index(self.request_path, annotations, self.folder / "work")
        self.assertEqual(result["region_mode"], "domestic")
        self.assertFalse(result["ready_for_generation"])
        self.assertEqual(read_json(result["resolved_path"])["region_mode"], "domestic")
        self.init()
        self.assertEqual(read_json(self.state_path)["region_mode"], "domestic")
        payload = build_payload(self.state_path)
        self.assertEqual(payload["unresolved_images"], [])
        self.assertEqual(len(payload["sheets"][0]["headers"]), 5)

    def test_region_change_invalidates_accepted_image_but_keeps_source_history(self):
        identifier = self.init()[0]["id"]
        source = self.complete_source(identifier)
        self.event(identifier, "delivered")
        before = read_json(self.state_path)["assets"][identifier]
        self.prepare(region_mode="domestic")
        with self.assertRaisesRegex(ValueError, "变化"):
            state.snapshot(self.state_path)
        state.initialize(self.state_path, self.request_path, self.plan_path)
        after = read_json(self.state_path)["assets"][identifier]
        self.assertNotEqual(before["design_hash"], after["design_hash"])
        self.assertNotIn("review", after)
        self.assertNotIn("accepted_attempt_id", after)
        self.assertEqual(after["attempts"], before["attempts"])
        self.assertTrue(source.exists())
        self.assertEqual(self.actions()[identifier], "generate")
        self.assertEqual(read_json(self.state_path)["region_mode"], "domestic")

    def test_region_change_rejects_partial_reuse(self):
        self.init()
        before = self.state_path.read_bytes()
        self.prepare(region_mode="domestic")
        with self.assertRaisesRegex(ValueError, "完整提示词计划"):
            state.initialize(self.state_path, self.request_path, self.plan_path, partial=True)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_overseas_alias_and_legacy_design_preserve_completed_image(self):
        identifier = self.init()[0]["id"]
        self.complete_source(identifier)
        self.event(identifier, "delivered")
        old = read_json(self.state_path)
        item = old["assets"][identifier]
        legacy_hash = object_hash([item["prompt"], item["kind"], item["variant"], item["min_short_side"],
                                   item["reference_ids"], item["reference_paths"]])
        self.assertEqual(legacy_hash, item["design_hash"])
        old.pop("region_mode")  # Simulate a prior overseas state with no region metadata.
        atomic_json(self.state_path, old)
        self.init(region_mode="海外")
        actual = read_json(self.state_path)["assets"][identifier]
        self.assertEqual(actual["accepted_attempt_id"], item["accepted_attempt_id"])
        self.assertEqual(self.actions()[identifier], "complete")

    def test_region_change_cannot_replace_inflight_design(self):
        identifier = self.init()[0]["id"]
        self.event(identifier, "generating", handle="old-region", attempt_id="old-attempt")
        self.prepare(region_mode="domestic")
        with self.assertRaisesRegex(ValueError, "仍在生成"):
            state.initialize(self.state_path, self.request_path, self.plan_path)
        self.event(identifier, "generated", attempt_id="old-attempt", source_path=str(self.picture()))
        state.initialize(self.state_path, self.request_path, self.plan_path)
        self.assertNotIn("source", read_json(self.state_path)["assets"][identifier])
        self.assertEqual(self.actions()[identifier], "generate")


if __name__ == "__main__":
    unittest.main()
