"""Offline output-selection regressions; never invoke a generation service."""
from itertools import combinations
import unittest
import asset_state as state
from input_check import RequestError, validate_request
from pipeline_common import atomic_json, read_json
from save_one_asset import save_one
from receive_asset import receive
import test_pipeline as fixtures
from workbook_data import build_payload


class OutputSelectionTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    asset = fixtures.PipelineTests.asset
    prepare = fixtures.PipelineTests.prepare
    init = fixtures.PipelineTests.init
    event = fixtures.PipelineTests.event
    actions = fixtures.PipelineTests.actions
    picture = fixtures.PipelineTests.picture
    complete_source = fixtures.PipelineTests.complete_source

    def test_default_full_table_without_directory_or_images(self):
        self.raw.pop("output_mode")
        items = self.init(selected_assets=None)
        self.assertEqual(len(items), 5)
        self.assertEqual(set(self.actions().values()), {"text_only"})
        payload = build_payload(self.state_path)
        self.assertEqual([len(s["rows"]) for s in payload["sheets"]], [3, 1, 1])
        self.assertEqual(payload["unresolved_images"], [])
        self.assertFalse((self.folder / "人物").exists())
        with self.assertRaises(ValueError):
            self.event(items[0]["id"], "generating", handle="not-authorized")
        with self.assertRaises(ValueError):
            save_one(self.request_path, self.picture(), "人物", "艾玛", items[0]["id"])

    def test_all_sixteen_image_combinations_preserve_full_table(self):
        types = ("人物", "三视图", "场景", "道具")
        self.raw.pop("output_mode")
        for count in range(5):
            for chosen in combinations(types, count):
                with self.subTest(chosen=chosen):
                    items = self.init(selected_assets=None, image_types=list(chosen),
                                      output_root=str(self.folder / "delivery"))
                    self.assertEqual(sum(x["kind"] != "turnaround" for x in items), 5)
                    self.assertEqual(sum(x["kind"] == "turnaround" for x in items),
                                     3 if "三视图" in chosen else 0)
                    for x in items:
                        expected = ("三视图" if x["kind"] == "turnaround" else x["category"]) in chosen
                        self.assertEqual(x["generate_image"], expected)
                        if not expected:
                            self.assertEqual(self.actions()[x["id"]], "text_only")
                    payload = build_payload(self.state_path)
                    self.assertEqual([len(s["rows"]) for s in payload["sheets"]], [3, 1, 1])
                    self.assertTrue(all(x["action"] != "text_only" for x in payload["unresolved_images"]))

    def test_disabled_category_cannot_submit_or_save_during_other_images(self):
        items = self.init(selected_assets=None, image_types=["场景"],
                          output_root=str(self.folder / "delivery"))
        role = next(x for x in items if x["category"] == "人物")
        scene = next(x for x in items if x["category"] == "场景")
        with self.assertRaises(ValueError):
            self.event(role["id"], "generating", handle="disabled")
        with self.assertRaises(ValueError):
            save_one(self.request_path, self.picture(), "人物", "艾玛", role["id"])
        self.event(scene["id"], "generating", handle="chosen")
        self.assertEqual(self.actions()[scene["id"]], "wait")

    def test_conflicting_or_invalid_selectors_are_rejected(self):
        cases = [
            {"image_types": ["三视图"], "generate_turnarounds": False},
            {"image_types": [], "generate_turnarounds": True},
            {"image_types": ["人物"], "turnaround_characters": ["艾玛"]},
            {"image_types": ["场景"]},  # selected_assets contains only a character
            {"image_types": ["未知"]},
            {"image_types": True},
            {"image_types": ["人物"], "output_mode": "workbook"},
            {"image_types": ["人物"], "output_mode": "prompts"},
            {"image_types": [], "output_mode": "images"},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(RequestError):
                validate_request({**self.raw, **changes})

    def test_explicit_images_defaults_to_bases_without_turnarounds(self):
        request = validate_request({**self.raw, "selected_assets": None,
                                    "output_mode": "images", "output_root": str(self.folder)})
        self.assertEqual(request["image_types"], ["人物", "场景", "道具"])
        self.assertFalse(request["generate_turnarounds"])
        self.assertFalse(request["generate_workbook"])

    def test_legacy_turnaround_request_alone_does_not_enable_originals(self):
        self.raw.pop("output_mode")
        request = validate_request({**self.raw, "generate_turnarounds": True})
        self.assertEqual(request["image_types"], ["三视图"])
        self.assertTrue(request["generate_workbook"])

    def turn_only(self, **changes):
        items = self.init(selected_assets={"人物": [{"name": "艾玛", "count": 1}]},
                          image_types=["三视图"], **changes)
        return items[0]["id"], items[1]["id"]

    def test_turnaround_alone_waits_for_existing_original(self):
        parent, turn = self.turn_only()
        self.assertEqual(self.actions(), {parent: "text_only", turn: "wait_for_reference"})
        with self.assertRaises(ValueError):
            self.event(parent, "generating", handle="must-not-generate")
        with self.assertRaises(ValueError):
            self.event(turn, "generating", handle="missing-reference")

    def test_checked_external_original_unlocks_only_turnaround_and_tracks_hash(self):
        parent, turn = self.turn_only(output_root=str(self.folder / "out"))
        source = self.picture()
        plan = read_json(self.plan_path)
        plan["assets"][0]["reference_image"] = {
            "path": str(source), "single_person": True, "note": "Synthetic single-character fixture checked"}
        atomic_json(self.plan_path, plan)
        state.initialize(self.state_path, self.request_path, self.plan_path)
        self.assertEqual(self.actions(), {parent: "text_only", turn: "generate"})
        dispatch = state.snapshot(self.state_path, actions=["generate"], include_prompt=True)
        self.assertEqual(dispatch["assets"][0]["reference_paths"], [str(source)])
        self.event(turn, "generating", handle="turn-only")
        recorded = read_json(self.state_path)
        self.assertNotIn("attempts", {k: v for k, v in recorded["assets"][parent].items() if v})
        self.assertEqual(recorded["assets"][turn]["attempts"][0]["references"][0]["path"], str(source))
        self.event(turn, "generated", source_path=str(self.picture("turn.png", (1024, 576))))
        self.picture(color="blue")  # reference changed on disk; stale checks do not unlock it
        self.assertEqual(self.actions()[turn], "wait_for_reference")
        state.initialize(self.state_path, self.request_path, self.plan_path)
        updated = read_json(self.state_path)["assets"][turn]
        self.assertNotIn("source", updated)
        self.assertEqual(self.actions()[turn], "preflight")

    def test_existing_original_requires_actual_single_character_check_and_geometry(self):
        self.turn_only()
        for value in ({"path": str(self.picture()), "single_person": False, "note": "Two subjects"},
                      {"path": str(self.picture()), "single_person": True, "note": ""},
                      {"path": str(self.picture("wide.png", (1024, 576))),
                       "single_person": True, "note": "Wrong geometry"}):
            plan = read_json(self.plan_path)
            plan["assets"][0]["reference_image"] = value
            atomic_json(self.plan_path, plan)
            with self.subTest(value=value), self.assertRaises(ValueError):
                state.initialize(self.state_path, self.request_path, self.plan_path)

    def test_adding_turnaround_reuses_approved_original(self):
        parent = self.init(selected_assets={"人物": [{"name": "艾玛", "count": 1}]})[0]["id"]
        self.complete_source(parent)
        self.event(parent, "delivered")
        previous = read_json(self.state_path)["assets"][parent]["current_attempt_id"]
        items = self.init(image_types=["三视图"])
        self.assertEqual(self.actions()[parent], "text_only")
        self.assertEqual(self.actions()[items[1]["id"]], "generate")
        self.assertEqual(read_json(self.state_path)["assets"][parent]["current_attempt_id"], previous)

    def test_disabling_images_preserves_inflight_reception(self):
        identifier = self.init()[0]["id"]
        self.event(identifier, "generating", handle="already-running")
        self.init(image_types=[])
        self.assertEqual(self.actions()[identifier], "wait")
        self.event(identifier, "generated", source_path=str(self.picture()))
        self.assertEqual(self.actions()[identifier], "text_only")
        with self.assertRaises(ValueError):
            self.event(identifier, "generating", handle="disabled-again")

    def test_disabled_inflight_result_does_not_create_pending_copy(self):
        items = self.init(selected_assets=None, image_types=["人物", "场景"],
                          output_root=str(self.folder / "out"))
        parent = next(x for x in items if x["category"] == "人物")["id"]
        self.event(parent, "generating", attempt_id="old-photo", handle="already-running")
        self.init(image_types=["场景"])
        result = receive(self.state_path, parent, "old-photo", self.picture())
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "received_not_archived")
        self.assertEqual(self.actions()[parent], "text_only")
        self.assertFalse((self.folder / "out").exists())


if __name__ == "__main__":
    unittest.main()
