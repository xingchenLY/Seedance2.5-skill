"""Offline fast-start regressions: scope, recovery, and no premature generation."""
from copy import deepcopy
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from PIL import Image

PREFLIGHT = dict(body_basis="Synthetic body verified", continuity="Same identity and state",
                 framing="Correct aspect and full margins", exclusions="No extra subjects")
import asset_state as state
import prepare
from input_check import load_and_validate
from pipeline_common import atomic_json, read_json, script_hash
from save_one_asset import save_one
from script_index import build_index, episode_headers


class FastStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="asset-fast-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request_path = self.root / "request.json"
        self.annotations_path = self.root / "annotations.json"
        self.plan_path = self.root / "plan.json"
        self.state_path = self.root / "state.json"
        self.raw = {"asset_workflow": "codex", "region_mode": "overseas", "script_text": "BIOGRAPHY\nDo not use this profile.\n01\nINT. ROOM\nEmma wears red.\nLiam wears black.\n02\nEXT. GARDEN\nEmma walks away.",
                    "episodes": [1], "output_root": str(self.root / "archive"), "output_mode": "assets",
                    "generate_turnarounds": True}
        self.ann = {"source_sha256": script_hash(self.raw), "confirmed_numbered_episodes": True,
                    "excluded_ranges": [{"start_line": 1, "end_line": 2}],
                    "scenes": [{"id": "s1", "start_line": 4, "end_line": 6, "episode": 1}],
                    "assets": [{"id": key, "category": "人物", "name": name, "variant": {"outfit": outfit},
                                "occurrences": [{"scene_id": "s1", "evidence": [{"line": line, "quote": quote}]}]}
                               for key, name, outfit, line, quote in
                               [("emma", "Emma", "red", 5, "Emma wears red."),
                                ("liam", "Liam", "black", 6, "Liam wears black.")]]}
        atomic_json(self.request_path, self.raw)
        atomic_json(self.annotations_path, self.ann)

    def index(self):
        prepare.index(self.request_path, self.annotations_path, self.root)
        return load_and_validate(self.request_path, require_content=True)["resolved_assets"]

    def init_partial(self):
        items = self.index()
        first = {"id": items[0]["id"], "preflight": PREFLIGHT, "prompt": "Emma final portrait prompt"}
        atomic_json(self.plan_path, {"assets": [first]})
        state.initialize(self.state_path, self.request_path, self.plan_path, partial=True)
        return items

    def event(self, identifier, event, **fields):
        return state.record_event(self.state_path, {"id": identifier, "event": event, **fields})

    def actions(self):
        return {x["id"]: x["action"] for x in state.snapshot(self.state_path)["assets"]}

    def image(self):
        path = self.root / "portrait.png"
        Image.new("RGB", (576, 1024), "gray").save(path)
        return path

    def test_partial_keeps_scope_and_blocks_undesignated_items(self):
        items = self.init_partial()
        self.assertEqual(len(items), 4)  # two originals plus requested default turnarounds
        actions = self.actions()
        self.assertEqual(actions[items[0]["id"]], "generate")
        self.assertEqual(list(actions.values()).count("design"), 3)
        with self.assertRaises(ValueError):
            self.event(items[2]["id"], "generating", handle="not-designed")
        with self.assertRaises(ValueError):
            state.initialize(self.state_path, self.request_path, self.plan_path)
        self.assertEqual(len(read_json(self.state_path)["assets"]), 4)

    def test_partial_append_preserves_inflight_and_rejects_changed_design(self):
        items = self.init_partial()
        first, second = items[0]["id"], items[2]["id"]
        self.event(first, "generating", handle="real-request-handle")
        atomic_json(self.plan_path, {"assets": [{"id": second, "preflight": PREFLIGHT, "prompt": "Liam final portrait prompt"}]})
        state.initialize(self.state_path, self.request_path, self.plan_path, partial=True)
        self.assertEqual(self.actions()[first], "wait")
        self.assertEqual(self.actions()[second], "generate")
        self.assertEqual(read_json(self.state_path)["assets"][first]["generation"]["handle"], "real-request-handle")
        atomic_json(self.plan_path, {"assets": [{"id": first, "preflight": PREFLIGHT, "prompt": "changed while running"}]})
        with self.assertRaises(ValueError):
            state.initialize(self.state_path, self.request_path, self.plan_path, partial=True)

    def test_partial_reference_cycle_and_unknown_id_rejected(self):
        items = self.init_partial()
        first, second = items[0]["id"], items[2]["id"]
        for entries in ([{"id": "outside-scope", "preflight": PREFLIGHT, "prompt": "extra"}],
                        [{"id": first, "preflight": PREFLIGHT, "prompt": "A", "reference_ids": [second]},
                         {"id": second, "preflight": PREFLIGHT, "prompt": "B", "reference_ids": [first]}]):
            atomic_json(self.plan_path, {"assets": entries})
            with self.assertRaises(ValueError):
                state.initialize(self.state_path, self.request_path, self.plan_path, partial=True)

    def test_complete_parent_survives_partial_update_and_dispatch_uses_saved_copy(self):
        items = self.init_partial()
        first, turn = items[0]["id"], items[1]["id"]
        source = self.image()
        self.event(first, "generating", handle="handle")
        self.event(first, "generated", source_path=str(source))
        self.event(first, "checked", approved=True, single_person=True, note="Synthetic test check")
        saved = save_one(self.request_path, source, "人物", "Emma-red", first)
        self.event(first, "saved", target_path=saved["target_path"])
        self.event(first, "delivered")
        atomic_json(self.plan_path, {"assets": [{"id": turn, "preflight": PREFLIGHT, "prompt": "Final turnaround prompt"}]})
        state.initialize(self.state_path, self.request_path, self.plan_path, partial=True)
        self.assertEqual(self.actions()[first], "complete")
        dispatch = state.snapshot(self.state_path, actions=["generate"], limit=1, include_prompt=True)
        self.assertEqual(dispatch["counts"], {"complete": 1, "generate": 1, "design": 2})
        self.assertEqual(dispatch["assets"][0]["id"], turn)
        self.assertEqual(dispatch["assets"][0]["reference_paths"], [saved["target_path"]])
        self.assertNotEqual(saved["target_path"], str(source))

    def test_batch_failure_keeps_committed_prefix_and_recovers(self):
        identifier = self.init_partial()[0]["id"]
        self.event(identifier, "generating", handle="handle")
        events = [{"id": identifier, "event": "generated", "source_path": str(self.image())},
                  {"id": identifier, "event": "checked", "approved": True, "note": "missing person count"},
                  {"id": identifier, "event": "delivered"}]
        result = state.record_events(self.state_path, events)
        self.assertFalse(result["ok"])
        self.assertEqual((result["committed"], result["failed_index"]), (1, 1))
        self.assertEqual(self.actions()[identifier], "inspect")
        result = state.record_events(self.state_path, [
            {**events[1], "single_person": True}, events[2]])
        self.assertTrue(result["ok"])
        self.assertEqual(self.actions()[identifier], "save")

    def test_text_only_requires_full_plan(self):
        self.raw["output_mode"] = "prompts"
        atomic_json(self.request_path, self.raw)
        self.index()
        atomic_json(self.plan_path, {"assets": []})
        with self.assertRaises(ValueError):
            state.initialize(self.state_path, self.request_path, self.plan_path, partial=True)

    def test_numbered_headings_require_body_confirmation(self):
        lines = self.raw["script_text"].splitlines()
        self.assertEqual(episode_headers(lines), [])
        self.assertEqual(episode_headers(lines, confirmed_numbered=True), [(3, 1), (7, 2)])
        unconfirmed = {k: v for k, v in self.ann.items() if k != "confirmed_numbered_episodes"}
        with self.assertRaises(ValueError):
            build_index(self.raw, unconfirmed)
        with self.assertRaises(ValueError):
            episode_headers(["01", "Story", "03"], confirmed_numbered=True)
        self.assertEqual(build_index(self.raw, self.ann)["episodes"], [1, 2])

    def test_extract_selected_episode_cache_and_changed_source(self):
        first = prepare.extract(self.request_path, self.root)
        self.assertFalse(first["ready_for_generation"])
        self.assertTrue(first["selection_provisional"])
        reading = Path(first["reading_path"]).read_text(encoding="utf-8")
        self.assertIn("0005 Emma wears red.", reading)
        self.assertNotIn("BIOGRAPHY", reading)
        self.assertNotIn("GARDEN", reading)
        self.assertFalse((self.root / "archive").exists())
        self.assertTrue(prepare.extract(self.request_path, self.root)["reused_extraction"])
        self.raw["script_text"] = self.raw["script_text"].replace("Emma wears red.", "Emma wears green.")
        atomic_json(self.request_path, self.raw)
        result = prepare.extract(self.request_path, self.root)
        self.assertFalse(result["reused_extraction"])
        self.assertIn("Emma wears green.", Path(result["reading_path"]).read_text(encoding="utf-8"))

    def test_failed_scope_resolution_does_not_replace_request_or_index(self):
        self.index()
        original_request = self.request_path.read_bytes()
        index_path = self.root / "content-index.json"
        original_index = index_path.read_bytes()
        ann = deepcopy(self.ann)
        ann["assets"][0]["occurrences"][0]["evidence"][0]["quote"] = "wrong quote"
        atomic_json(self.annotations_path, ann)
        with self.assertRaisesRegex(ValueError, "emma.*s1.*5"):
            prepare.index(self.request_path, self.annotations_path, self.root)
        self.assertEqual(self.request_path.read_bytes(), original_request)
        self.assertEqual(index_path.read_bytes(), original_index)

    def test_cli_short_event_and_compact_dispatch(self):
        identifier = self.init_partial()[0]["id"]
        script = str(Path(__file__).with_name("asset_state.py"))
        event = {"id": identifier, "event": "generating", "handle": "cli-handle"}
        result = subprocess.run([sys.executable, "-B", script, "record", str(self.state_path), "--json", json.dumps(event)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["next_action"], "wait")
        result = subprocess.run([sys.executable, "-B", script, "next", str(self.state_path),
                                 "--actions", "wait", "--limit", "1", "--include-prompt"],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["assets"][0]["handle"], "cli-handle")


if __name__ == "__main__":
    unittest.main()
