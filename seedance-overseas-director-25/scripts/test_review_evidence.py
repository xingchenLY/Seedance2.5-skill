from copy import deepcopy
import unittest

from delivery_checks import validate_delivery
from render_prompt import render_plan
from test_delivery_checks import review
from test_render_prompt import authored_sample
from validate_plan import validate, validate_timing_preflight


class ReviewEvidenceTests(unittest.TestCase):
    def reviewed(self):
        plan, text = render_plan(authored_sample())
        review(plan, text)
        return plan, text

    def issue(self, plan, text, message):
        errors = validate_delivery(plan, text)
        self.assertTrue(any(message in e for e in errors), errors)

    def test_short_complete_episode_renders_without_padding(self):
        authored = authored_sample()
        authored['episodes'][0]['complete'] = True
        plan, text = render_plan(authored)
        review(plan, text)
        self.assertEqual(validate(plan) + validate_delivery(plan, text), [])
        self.assertEqual(plan['segments'][0]['duration'], 4)

    def test_user_can_explicitly_remove_custom_episode_minimum(self):
        authored = authored_sample()
        authored['episodes'][0]['complete'] = True
        authored['limits'] = {'episode_seconds': 0}
        authored['limits_basis'] = 'User explicitly removed the previously requested minimum.'
        self.assertEqual(validate(render_plan(authored)[0]), [])

    def test_each_generic_review_category_is_rejected(self):
        for key in ('source_coverage', 'timing', 'visual_continuity', 'conflicts'):
            for value in ('已检查。', '全部通过', 'PASS', '无冲突'):
                with self.subTest(key=key, value=value):
                    plan, text = self.reviewed()
                    plan['delivery_review'][key] = value
                    self.issue(plan, text, 'concrete ' + key)

    def test_missing_lighting_receipts_fail_final_but_not_render(self):
        plan, text = self.reviewed()
        del plan['delivery_review']['lighting']
        self.issue(plan, text, 'segment-scoped lighting evidence')
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_scope_cannot_omit_repeat_or_invent_segments(self):
        for mode in ('empty', 'repeat', 'unknown'):
            with self.subTest(mode=mode):
                plan, text = self.reviewed()
                rows = plan['delivery_review']['lighting']
                if mode == 'empty':
                    rows.clear()
                elif mode == 'repeat':
                    rows.append(deepcopy(rows[0]))
                else:
                    rows[0]['segment_id'] = 'missing'
                self.issue(plan, text, 'delivery_review.lighting')

    def test_all_actual_shots_must_be_covered_in_order(self):
        for coverage in ([0], [2, 0], [0, 0, 2], [False, 2], [0, 1]):
            with self.subTest(coverage=coverage):
                plan, text = self.reviewed()
                plan['delivery_review']['lighting'][0]['checked_shots'] = coverage
                self.issue(plan, text, 'checked_shots')

    def test_anchor_must_match_this_segment_final_lighting(self):
        plan, text = self.reviewed()
        plan['delivery_review']['lighting'][0]['anchor'] = '不存在的南窗成为唯一光源。'
        self.issue(plan, text, 'anchor differs')

    def test_lighting_basis_and_continuity_cannot_be_boilerplate(self):
        for key in ('anchor', 'basis', 'continuity'):
            plan, text = self.reviewed()
            plan['delivery_review']['lighting'][0][key] = '已检查'
            self.issue(plan, text, 'concrete ' + key)

    def test_dynamic_evidence_binds_to_correct_internal_shot(self):
        authored = authored_sample()
        authored['segments'][0]['shots'][1]['frame_id'] = '分镜1'
        marker = 'Elias 侧脸短暂被经过的车灯照亮，随后恢复原窗光。'
        authored['segments'][0]['shots'][1]['visual_text'] += marker
        plan, text = render_plan(authored)
        review(plan, text)
        change = {'shot_start': 2, 'evidence': marker, 'basis': '合成输入明确窗外有车辆驶过。'}
        plan['delivery_review']['lighting'][0]['changes'] = [change]
        self.assertEqual(validate_delivery(plan, text), [])
        change['shot_start'] = 0
        self.issue(plan, text, 'absent from its actual shot')

    def test_unbound_change_quote_fails(self):
        plan, text = self.reviewed()
        plan['delivery_review']['lighting'][0]['changes'] = [
            {'shot_start': 0, 'evidence': '并未写入画面的开灯过程。', 'basis': '合成检查未知引用。'}]
        self.issue(plan, text, 'absent from its actual shot')

    def test_change_needs_real_shot_and_basis(self):
        for value in (-1, True, float('nan'), 0.5):
            plan, text = self.reviewed()
            plan['delivery_review']['lighting'][0]['changes'] = [
                {'shot_start': value, 'evidence': '窗口', 'basis': '已检查'}]
            self.issue(plan, text, 'unique actual shot_start')
            self.issue(plan, text, 'concrete basis')

    def test_changed_prompt_still_invalidates_review(self):
        plan, text = self.reviewed()
        self.issue(plan, text + '\n', 'changed after review')

    def test_30_1_with_removable_tail_is_feasible(self):
        result = validate_timing_preflight({'segments': [{'id': 'S1', 'pre_round_duration': 30.1,
            'timing_feasibility': {'minimum_seconds': 29.7, 'basis': '原估值含0.4秒无任务尾停。'}}]})
        self.assertEqual(result, [])

    def test_irreducible_30_1_requires_regrouping(self):
        result = validate_timing_preflight({'segments': [{'id': 'S1', 'pre_round_duration': 30.1,
            'timing_feasibility': {'minimum_seconds': 30.1, 'basis': '实测连续原声30.1秒。'}}]})
        self.assertTrue(any('timing infeasible' in e for e in result), result)


if __name__ == '__main__':
    unittest.main()
