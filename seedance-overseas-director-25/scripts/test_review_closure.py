"""Final gates must expose missing work and stale evidence, without editing art."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from appearance_review import findings
from delivery_checks import validate_delivery
from render_prompt import render_plan
from review_closure import check_source_audit, evidence_matches, plan_digest
from review_evidence import concrete_note
from review_report import collect_review_report
from test_delivery_checks import review
from test_modes import mode_sample
from validate_plan import validate


def reviewed(mode='真人剧'):
    plan, text = render_plan(mode_sample(mode))
    review(plan, text)
    return plan, text


class ReviewClosureTests(unittest.TestCase):
    def issue(self, plan, text, message):
        errors = validate(plan) + validate_delivery(plan, text)
        self.assertTrue(any(message in error for error in errors), errors)

    def test_all_modes_require_current_candidate_decisions(self):
        for mode in ('真人剧', '3D漫真人剧', '推文', '3D漫推文'):
            plan, text = reviewed(mode)
            self.assertEqual(validate(plan) + validate_delivery(plan, text), [])
            del plan['delivery_review']['findings']
            self.issue(plan, text, 'delivery_review.findings')

    def test_unprocessed_candidate_is_not_cleared_by_generic_notes(self):
        plan, text = reviewed()
        self.assertTrue(plan['delivery_review']['findings'])
        plan['delivery_review']['findings'] = []
        self.issue(plan, text, 'unresolved REVIEW')

    def test_invalid_decision_or_empty_rationale_fails(self):
        for field, value in (('decision', 'fixed'), ('decision', 'pending'), ('basis', '本段复核完成，没有异常。')):
            plan, text = reviewed()
            plan['delivery_review']['findings'][0][field] = value
            self.issue(plan, text, 'unresolved REVIEW')

    def test_candidate_cannot_use_stale_or_duplicate_key(self):
        for kind in ('stale', 'duplicate'):
            plan, text = reviewed()
            rows = plan['delivery_review']['findings']
            if kind == 'stale':
                rows[0]['key'] = 'old-key'
            else:
                rows.append(deepcopy(rows[0]))
            self.issue(plan, text, 'stale, unknown or duplicate')

    def test_evidence_must_cover_correct_actual_shot(self):
        for kind in ('text', 'location', 'missing'):
            plan, text = reviewed()
            row = plan['delivery_review']['findings'][0]
            if kind == 'missing': row['evidence'] = []
            elif kind == 'text': row['evidence'][0]['text'] = '本镜没有写过的表演依据。'
            else: row['evidence'][0]['shot_start'] = 1.1
            self.issue(plan, text, 'REVIEW evidence')

    def test_cross_shot_candidate_needs_both_endpoints(self):
        data = mode_sample('真人剧')
        data['segments'][0]['shots'][1]['view']['position'] = data['segments'][0]['shots'][1]['view']['position'].replace('左肩后', '右肩后')
        plan, text = render_plan(data)
        review(plan, text)
        cross = next(i for i in collect_review_report(plan)['items'] if i['finding'].get('kind') == 'same_shoulder_reverse')
        row = next(r for r in plan['delivery_review']['findings'] if r['key'] == cross['key'])
        row['evidence'].pop()
        self.issue(plan, text, 'every affected shot')

    def test_repair_rebuilds_candidates_without_mutating_source_voice(self):
        data = mode_sample('真人剧')
        original = deepcopy(data)
        second = data['segments'][0]['shots'][1]
        second['view']['position'] = second['view']['position'].replace('左肩后', '右肩后')
        bad, text = render_plan(data)
        review(bad, text)
        bad['delivery_review']['findings'] = []
        self.issue(bad, text, 'unresolved REVIEW')
        # Actual framing correction, not a forged retain decision.
        second['view']['position'] = original['segments'][0]['shots'][1]['view']['position']
        fixed, final = render_plan(data)
        review(fixed, final)
        self.assertFalse(any(i['code'] == 'shoulder_axis' for i in collect_review_report(fixed)['items']))
        self.assertEqual(validate(fixed) + validate_delivery(fixed, final), [])
        self.assertEqual(fixed['segments'][0]['speech'], original['segments'][0]['speech'])
        self.assertEqual(fixed['segments'][0]['duration'], original['segments'][0]['duration'])

    def test_plan_only_change_invalidates_review_even_when_text_hash_unchanged(self):
        plan, text = reviewed()
        plan['sources'][0]['source_ref'] = '另一个来源版本'
        self.issue(plan, text, 'plan changed')

    def test_review_only_changes_do_not_change_plan_digest(self):
        plan, text = reviewed()
        digest = plan_digest(plan)
        plan['delivery_review']['conflicts'] = '同段声音类别与无配乐条件一致，未改变原词。'
        self.assertEqual(plan_digest(plan), digest)
        self.assertEqual(validate_delivery(plan, text), [])

    def test_missing_source_audit_and_segment_are_rejected(self):
        for value in (None, []):
            plan, text = reviewed()
            plan['delivery_review']['source_audit'] = value
            self.issue(plan, text, 'source_audit')

    def test_source_audit_cannot_duplicate_or_invent_segment(self):
        for kind in ('duplicate', 'unknown'):
            plan, text = reviewed()
            rows = plan['delivery_review']['source_audit']
            if kind == 'duplicate': rows.append(deepcopy(rows[0]))
            else: rows[0]['segment_id'] = 'missing'
            self.issue(plan, text, 'unknown or duplicate segment')

    def test_source_based_bystander_and_prop_cannot_disappear_with_plan(self):
        for field, value, message in (('characters', ['Mara', 'Elias', 'Observer'], 'characters differ'),
                                      ('props', '手机', 'props differ')):
            plan, text = reviewed()
            plan['delivery_review']['source_audit'][0][field] = value
            self.issue(plan, text, message)

    def test_key_information_binds_source_quote_and_final_location(self):
        plan, text = reviewed()
        row = plan['delivery_review']['source_audit'][0]
        row['source_excerpt'] = '合成原文：两人面对面，Mara 说话。'
        shot = plan['segments'][0]['shots'][0]
        row['key_information'] = [dict(source_quote='Mara 说话', shot_start=shot['start'], evidence=shot['visual_text'])]
        self.assertEqual(validate_delivery(plan, text), [])
        row['key_information'][0]['source_quote'] = '手机屏幕亮起'
        self.issue(plan, text, 'source_quote must occur')
        row['key_information'][0]['source_quote'] = 'Mara 说话'
        row['key_information'][0]['shot_start'] = 1.1
        self.issue(plan, text, 'source key information is absent')

    def test_generic_signoff_variants_are_rejected_without_length_threshold(self):
        for phrase in ('本段复核完成，没有异常。', '已经逐项检查，未发现任何问题', '本次检查全部通过'):
            self.assertFalse(concrete_note(phrase), phrase)
        self.assertTrue(concrete_note('L1 后停0.3秒接话。'))

    def test_original_result_words_are_data_not_reviewer_signoffs(self):
        for literal in ('PASS', '没有异常', '检查通过'):
            plan, text = reviewed()
            row = plan['delivery_review']['source_audit'][0]
            row['source_excerpt'] = literal
            row['key_information'] = [dict(source_quote=literal, shot_start=0, evidence=literal)]
            sid = row['segment_id']
            intervals = {(sid, 0): [f'屏幕显示“{literal}”。']}
            errors = []
            check_source_audit(plan, intervals, [row], errors)
            self.assertEqual(errors, [])
            self.assertTrue(evidence_matches(literal, intervals, (sid, 0)))
            # The same words remain insufficient as the reviewer's reasoning.
            row['coverage_basis'] = literal
            errors = []
            check_source_audit(plan, intervals, [row], errors)
            self.assertTrue(any('coverage_basis' in error for error in errors))

    def test_pallor_paraphrase_is_located_but_spoken_literal_is_not_rewritten(self):
        self.assertTrue(list(findings('她的脸一下失去了血色。')))
        self.assertTrue(list(findings('他的面孔毫无血色。')))
        self.assertFalse(list(findings('不要让她的脸失去血色。')))
        self.assertFalse(list(findings('纸条写着“她的脸失去了血色”。')))
        self.assertFalse(list(findings('她的眼眶轻微发红。')))

    def test_cli_never_prints_final_pass_with_unresolved_candidates(self):
        plan, text = reviewed()
        plan['delivery_review']['findings'] = []
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'plan.json').write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            (folder/'final.txt').write_text(text, encoding='utf-8')
            args = [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).with_name('validate_plan.py')),
                    str(folder/'plan.json'), '--final-prompt', str(folder/'final.txt')]
            result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertNotIn('PASS:', result.stdout)
            self.assertIn('unresolved REVIEW', result.stdout)
            self.assertIn('key=', result.stdout)
            review(plan, text)
            (folder/'plan.json').write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS:', result.stdout)
            self.assertNotIn('REVIEW:', result.stdout)
            self.assertIn('no audio/video verification', result.stdout)

    def test_validation_does_not_rewrite_or_auto_complete_reviews(self):
        plan, text = reviewed()
        plan['delivery_review']['findings'] = []
        before = deepcopy(plan)
        validate_delivery(plan, text)
        self.assertEqual(plan, before)


if __name__ == '__main__':
    unittest.main()
