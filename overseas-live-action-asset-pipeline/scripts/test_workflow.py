"""Behavior regressions for attempt provenance, batch validation and final output."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import asset_state as state
import finish_asset
from image_check import inspect_image
import prepare
import test_pipeline as fixtures
from pipeline_common import atomic_json, read_json
from script_index import build_index, IndexErrors
from workbook_data import build_payload


class WorkflowTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    asset = fixtures.PipelineTests.asset
    prepare = fixtures.PipelineTests.prepare
    init = fixtures.PipelineTests.init
    event = fixtures.PipelineTests.event
    actions = fixtures.PipelineTests.actions
    picture = fixtures.PipelineTests.picture

    def one(self, **changes):
        return self.init(selected_assets={"人物": [{"name": "艾玛", "count": 1}]}, **changes)[0]["id"]

    def receive(self, identifier, attempt, **times):
        self.event(identifier, "generating", attempt_id=attempt, handle="tool-"+attempt,
                   **({"at": times["submitted"]} if "submitted" in times else {}))
        return self.event(identifier, "generated", attempt_id=attempt, source_path=str(self.picture(attempt+".png")),
                          **({"at": times["returned"]} if "returned" in times else {}))

    def test_preflight_blocks_submission_without_blocking_other_designs(self):
        identifier = self.one()
        plan = read_json(self.plan_path)
        plan["assets"][0]["prompt"] = "A changed composition"
        plan["assets"][0].pop("preflight")
        atomic_json(self.plan_path, plan)
        state.initialize(self.state_path, self.request_path, self.plan_path)
        self.assertEqual(self.actions()[identifier], "preflight")
        with self.assertRaises(ValueError):
            self.event(identifier, "generating", handle="premature")
        self.event(identifier, "preflight", checks=fixtures.PREFLIGHT)
        self.assertEqual(self.actions()[identifier], "generate")

    def test_duplicate_failure_once_and_different_quality_codes_pause(self):
        identifier = self.one()
        self.receive(identifier, "attempt-1")
        self.event(identifier, "checked", attempt_id="attempt-1", approved=False, code="extra_prop", note="Extra object")
        self.event(identifier, "failed", attempt_id="attempt-1", stage="quality", code="cropped", message="Same image also cropped")
        item = read_json(self.state_path)["assets"][identifier]
        self.assertEqual(item["failure"]["count"], 1)
        plan = read_json(self.plan_path)
        plan["assets"][0]["prompt"] = "Repaired full prompt with complete margins"
        atomic_json(self.plan_path, plan)
        state.initialize(self.state_path, self.request_path, self.plan_path)
        self.receive(identifier, "attempt-2")
        self.event(identifier, "checked", attempt_id="attempt-2", approved=False, code="cropped", note="Still cropped")
        for n in range(3, 7):
            self.assertEqual(self.actions()[identifier], "retry_generation")
            attempt = "attempt-" + str(n)
            self.receive(identifier, attempt)
            self.event(identifier, "checked", attempt_id=attempt, approved=False,
                       code="defect-" + str(n), note="Visible quality defect")
        self.assertEqual(self.actions()[identifier], "paused")
        item = read_json(self.state_path)["assets"][identifier]
        self.assertEqual(len(item["attempts"]), 6)
        self.assertNotEqual(item["attempts"][0]["prompt"], item["attempts"][1]["prompt"])
        self.event(identifier, "retry", note="Composition reduced and margins corrected")
        self.assertEqual(self.actions()[identifier], "retry_generation")

    def test_duplicate_events_and_start_do_not_create_extra_attempts(self):
        identifier = self.one()
        start = dict(attempt_id="a1", handle="tool-a1")
        self.event(identifier, "generating", **start)
        revision = read_json(self.state_path)["revision"]
        self.assertTrue(self.event(identifier, "generating", **start)["duplicate"])
        self.assertEqual(read_json(self.state_path)["revision"], revision)
        result = dict(attempt_id="a1", source_path=str(self.picture()))
        self.event(identifier, "generated", **result)
        self.assertTrue(self.event(identifier, "generated", **result)["duplicate"])
        self.event(identifier, "checked", approved=False, code="cropped", note="cropped")
        self.event(identifier, "generating", attempt_id="a2", handle="tool-a2")
        with self.assertRaisesRegex(ValueError, "attempt_id"):
            self.event(identifier, "generated", **result)
        self.assertEqual(self.actions()[identifier], "wait")

    def test_event_id_cannot_be_reused_for_different_content(self):
        identifier = self.one()
        event = dict(event_id="unique", attempt_id="a1", handle="tool-a1")
        self.event(identifier, "generating", **event)
        with self.assertRaises(ValueError):
            self.event(identifier, "generating", **{**event, "handle": "different"})

    def test_attempt_timing_has_distinct_real_stages(self):
        identifier = self.one()
        self.receive(identifier,"timed",submitted="2020-01-01T00:00:00+00:00",returned="2020-01-01T00:00:10+00:00")
        self.event(identifier,"checked",approved=True,single_person=True,note="Observed",
                   at="2020-01-01T00:00:13+00:00")
        timing = state.metrics(self.state_path)["timings"][0]
        self.assertEqual(timing["generation_seconds"],10)
        self.assertEqual(timing["review_seconds"],3)
        self.assertIsNone(timing["archive_seconds"])
        with self.assertRaises(ValueError):
            self.event(identifier,"delivered",at="2019-01-01T00:00:00+00:00")

    def test_finish_saves_one_and_does_not_claim_display(self):
        identifier = self.one(output_root=str(self.folder/"archive"))
        self.receive(identifier,"save-one")
        result = finish_asset.finish(self.state_path, identifier, "save-one",
                   {"approved":True,"single_person":True,"note":"Observed actual source"})
        self.assertEqual(result["status"],"saved")
        self.assertEqual(self.actions()[identifier],"deliver")
        self.assertTrue(Path(result["target_path"]).is_file())
        repeated = finish_asset.finish(self.state_path, identifier,"save-one",displayed=True)
        self.assertEqual(repeated["status"],"reused")
        self.assertEqual(self.actions()[identifier],"complete")
        self.assertEqual(len(list((self.folder/"archive").rglob('*.png'))),1)

    def test_archive_failure_resumes_save_without_generation(self):
        identifier = self.one(output_root=str(self.folder/"archive"))
        self.receive(identifier,"save-failure")
        with patch('finish_asset.save_one',side_effect=PermissionError('test denial')):
            with self.assertRaises(PermissionError):
                finish_asset.finish(self.state_path,identifier,"save-failure",
                   {"approved":True,"single_person":True,"note":"Observed"})
        self.assertEqual(self.actions()[identifier],"save")
        result = finish_asset.finish(self.state_path,identifier,"save-failure")
        self.assertEqual(result["status"],"saved")
        self.assertEqual(len(read_json(self.state_path)["assets"][identifier]["attempts"]),1)

    def test_direct_delivery_never_creates_archive(self):
        identifier = self.one()
        self.receive(identifier,"direct")
        result = finish_asset.finish(self.state_path,identifier,"direct",
                   {"approved":True,"single_person":True,"note":"Observed"},displayed=True)
        self.assertEqual(result["status"],"not_archived")
        self.assertEqual(self.actions()[identifier],"complete")

    def test_prop_supports_both_orientations_but_not_other_ratios(self):
        for size, ratio in [((576,1024),"9:16"),((1024,576),"16:9"),((1672,941),"16:9")]:
            result = inspect_image(self.picture('prop.png',size),"道具","prop")
            self.assertEqual(result['aspect_ratio'],ratio)
        for size in ((1024,1024),(800,600),(900,500),(512,288)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                inspect_image(self.picture('bad-prop.png',size),"道具","prop")
        with self.assertRaises(ValueError):
            inspect_image(self.picture('portrait-mismatch.png',(1024,576)),"人物","original")
        with self.assertRaises(ValueError):
            inspect_image(self.picture('scene-mismatch.png',(576,1024)),"场景","scene")

    def test_landscape_prop_passes_review_and_archive(self):
        identifier = self.init(selected_assets={"道具":["银色钥匙"]},
                               output_root=str(self.folder/'archive'))[0]['id']
        plan = read_json(self.plan_path)
        plan['assets'][0]['prompt'] = '银色钥匙横向展示，16:9横版，完整全貌，浅灰纯色背景。'
        plan['assets'][0]['preflight']['framing'] = '钥匙横向展示更清楚，选择16:9横版，四周完整留白。'
        atomic_json(self.plan_path,plan)
        state.initialize(self.state_path,self.request_path,self.plan_path)
        self.event(identifier,'generating',attempt_id='landscape-prop',handle='test-request')
        self.event(identifier,'generated',attempt_id='landscape-prop',
                   source_path=str(self.picture('wide-prop.png',(1024,576))))
        result = finish_asset.finish(self.state_path,identifier,'landscape-prop',
                    {'approved':True,'note':'Synthetic verification: 16:9 matches selected layout'},displayed=True)
        self.assertEqual(result['status'],'saved')
        self.assertTrue(Path(result['target_path']).is_file())
        self.assertEqual(self.actions()[identifier],'complete')
        self.assertEqual(read_json(self.state_path)['assets'][identifier]['review']['aspect_ratio'],'16:9')

    def test_batch_index_errors_and_quote_line_suggestions(self):
        ann = deepcopy(self.annotations)
        ann["assets"][0]["occurrences"][0]["evidence"][0]["line"] = 5
        ann["assets"][1]["aliases"] = []
        with self.assertRaises(IndexErrors) as caught:
            build_index(self.raw,ann)
        self.assertGreaterEqual(len(caught.exception.errors),2)
        self.assertIn("[6]",str(caught.exception))
        self.assertIn("emma-blue",str(caught.exception))
        self.assertEqual(ann["assets"][0]["occurrences"][0]["evidence"][0]["line"],5)

    def test_failed_index_batch_leaves_existing_request_and_index_unchanged(self):
        self.prepare()
        prior = self.request_path.read_bytes(),self.index_path.read_bytes()
        ann = deepcopy(self.annotations)
        ann["assets"][0]["occurrences"][0]["evidence"][0]["quote"] = "wrong"
        ann["assets"][1]["aliases"] = []
        annotation_path = self.folder/'ann.json'
        atomic_json(annotation_path,ann)
        with self.assertRaises(IndexErrors):
            prepare.index(self.request_path,annotation_path,self.folder)
        self.assertEqual(prior,(self.request_path.read_bytes(),self.index_path.read_bytes()))

    def test_workbook_uses_accepted_prompt_and_rejects_drift(self):
        identifier = self.one(generate_workbook=True)
        self.receive(identifier,"final")
        finish_asset.finish(self.state_path,identifier,"final",
                   {"approved":True,"single_person":True,"note":"Observed"},displayed=True)
        payload = build_payload(self.state_path)
        selected = read_json(self.state_path)["assets"][identifier]
        self.assertEqual(payload["sheets"][0]["rows"][0][4],selected["attempts"][0]["prompt"])
        self.assertEqual(payload["unresolved_images"],[])
        corrupted = read_json(self.state_path)
        corrupted["assets"][identifier]["prompt"] = "unsynchronized table replacement"
        atomic_json(self.state_path,corrupted)
        with self.assertRaisesRegex(ValueError,"不一致"):
            build_payload(self.state_path)

    def test_text_workbook_has_exact_categories_and_real_blank_time(self):
        self.annotations["assets"][3]["variant"].pop("time")
        self.init(output_mode="workbook",selected_assets={"场景":["医院"]})
        payload = build_payload(self.state_path)
        self.assertEqual([s["name"] for s in payload["sheets"]],["场景资产"])
        self.assertIsNone(payload["sheets"][0]["rows"][0][2])
        self.assertEqual(len(payload["sheets"][0]["rows"][0]),5)

    def test_legacy_success_preserved_but_requires_prompt_verification(self):
        identifier = self.one(generate_workbook=True)
        self.receive(identifier,"legacy")
        finish_asset.finish(self.state_path,identifier,"legacy",
                   {"approved":True,"single_person":True,"note":"Observed"},displayed=True)
        saved = read_json(self.state_path)
        item = saved["assets"][identifier]
        for key in ('attempts','current_attempt_id','accepted_attempt_id'):
            item.pop(key,None)
        saved['schema_version']=1
        atomic_json(self.state_path,saved)
        self.assertEqual(self.actions()[identifier],"complete")
        self.assertEqual(build_payload(self.state_path)['legacy_prompt_verification_required'],[identifier])
        self.event(identifier,'verify_legacy_prompt',prompt='Verified complete final prompt',note='Matched actual tool log and image')
        self.assertEqual(build_payload(self.state_path)['legacy_prompt_verification_required'],[])
        self.assertEqual(state.metrics(self.state_path)['recorded_attempts'],0)


if __name__ == '__main__':
    unittest.main()
