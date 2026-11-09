"""Synthetic record tests; these receipts must never be used as real audits."""
from copy import deepcopy
import unittest

from delivery_checks import validate_delivery
from render_prompt import render_plan
from test_delivery_checks import review
from test_modes import mode_sample


def sample():
    plan, text = render_plan(mode_sample('真人剧'))
    review(plan, text)
    return plan, text


def add_issue(plan, after, kind='improvement'):
    row = dict(id='E1', kind=kind, status='applied',
        rule_or_goal='合成测试：让具体画面证据绑定到修改后的真实位置。',
        before='合成旧稿中没有出现该具体场内关系。',
        change='测试新增当前具体关系描述，原发声保持。', after=after,
        verification='此记录只测试程序证据绑定，不声称真实内容已经独立审阅。')
    plan['delivery_review']['editorial_review']['issues'] = [row]
    return row


class EditorialReviewTests(unittest.TestCase):
    def issue(self, plan, text, fragment):
        errors = validate_delivery(plan, text)
        self.assertTrue(any(fragment in e for e in errors), errors)

    def test_first_delivery_requires_separate_final_text_review(self):
        for mode in ('真人剧', '3D漫真人剧', '推文', '3D漫推文'):
            plan, text = render_plan(mode_sample(mode))
            review(plan, text)
            self.assertEqual(validate_delivery(plan, text), [])
            del plan['delivery_review']['editorial_review']
            self.issue(plan, text, 'separate final-text audit')

    def test_actual_method_and_fallback_are_explicit(self):
        plan, text = sample()
        record = plan['delivery_review']['editorial_review']
        del record['fallback_basis']
        self.issue(plan, text, 'actual reason')
        record['method'] = 'independent_agent'
        record['reviewer_ref'] = '合成代理编号，仅供记录接口测试'
        self.assertEqual(validate_delivery(plan, text), [])
        record['method'] = 'pretend_second_person'
        self.issue(plan, text, 'actual review method')

    def test_final_review_cannot_skip_segments_or_current_candidates(self):
        for field in ('reviewed_segments', 'finding_keys'):
            plan, text = sample()
            self.assertTrue(plan['delivery_review']['editorial_review'][field])
            plan['delivery_review']['editorial_review'][field] = []
            self.issue(plan, text, 'editorial_review')

    def test_final_version_needs_new_review_after_design_or_text_change(self):
        for key in ('plan_sha256', 'prompt_sha256'):
            plan, text = sample()
            plan['delivery_review']['editorial_review'][key] = 'old-version'
            self.issue(plan, text, 'current design and final text')

    def test_errors_and_improvements_both_must_be_implemented(self):
        for kind in ('error', 'improvement'):
            for status in ('pending', 'retained', 'deferred'):
                plan, text = sample()
                segment = plan['segments'][0]
                row = add_issue(plan, [dict(segment_id=segment['id'], shot_start=0,
                    text=segment['shots'][0]['visual_text'])], kind)
                row['status'] = status
                self.issue(plan, text, 'must be applied')
                row['status'] = 'applied'
                self.assertEqual(validate_delivery(plan, text), [])

    def test_unchanged_excerpt_is_not_proof_of_applied_change(self):
        plan, text = sample()
        segment = plan['segments'][0]
        excerpt = segment['shots'][0]['visual_text']
        row = add_issue(plan, [dict(segment_id=segment['id'], shot_start=0, text=excerpt)])
        row['before'] = excerpt
        self.issue(plan, text, 'unchanged excerpt')

    def test_shot_evidence_cannot_point_to_other_shot_or_nonexistent_text(self):
        for start, excerpt in ((2, 'Mara 看向 Elias，前景保留他的肩背。'),
                               (0, '稿内根本没有这条新动作。')):
            plan, text = sample()
            add_issue(plan, [dict(segment_id=plan['segments'][0]['id'], shot_start=start, text=excerpt)])
            self.issue(plan, text, 'absent from its final shot')

    def test_header_and_voice_changes_bind_without_inventing_shots(self):
        for prefix in ('出镜人物：', '（Mara）台词参考'):
            plan, text = sample()
            excerpt = next(line for line in text.splitlines() if line.startswith(prefix))
            after = dict(segment_id=plan['segments'][0]['id'], scope='segment', text=excerpt)
            add_issue(plan, [after])
            self.assertEqual(validate_delivery(plan, text), [])
            after['segment_id'] = 'another-segment'
            self.issue(plan, text, 'in this segment')

    def test_segment_evidence_needs_field_context_not_an_isolated_name(self):
        plan, text = sample()
        add_issue(plan, [dict(segment_id=plan['segments'][0]['id'], scope='segment', text='Mara')])
        self.issue(plan, text, 'with its label')

    def test_same_shoulder_cannot_be_retained_even_with_complete_receipts(self):
        data = mode_sample('真人剧')
        second = data['segments'][0]['shots'][1]
        second['view']['position'] = second['view']['position'].replace('左肩后', '右肩后')
        plan, text = render_plan(data)
        review(plan, text)
        self.issue(plan, text, 'shoulder-axis inconsistency cannot be retained')

    def test_validation_does_not_manufacture_or_modify_editorial_evidence(self):
        plan, text = sample()
        del plan['delivery_review']['editorial_review']
        original = deepcopy(plan)
        validate_delivery(plan, text)
        self.assertEqual(plan, original)


if __name__ == '__main__':
    unittest.main()
