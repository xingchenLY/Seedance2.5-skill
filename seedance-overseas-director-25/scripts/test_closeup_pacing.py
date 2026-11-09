from copy import deepcopy
import unittest

from closeup_pacing import CLOSEUPS, check_closeups, closeup_prose
from delivery_checks import validate_delivery
from dialogue_editing import check_dialogue_editing
from render_prompt import render_plan
from shot_pacing import check_shot_pacing
from test_modes import mode_sample


def span(start, end, extended=False):
    item = dict(start=start, end=end, task='读清试图压住的笑意消失',
                finish_evidence=f'第{end}秒，Mara 嘴角放下，目光停在 Elias 脸上。')
    if extended:
        item['necessity'] = dict(reason='performance_turn',
            basis='前半先压住笑意，随后嘴角失守，末端才收回回应；全过程需要2.7秒。',
            cut_at=start+1.5, next_view='Elias 近景，读清对方等待回应的表情。',
            loss_if_cut='此时切走会漏掉笑意从勉强维持到消失的转折，肩背无法显示嘴角变化。')
    return item


def shot(end=3, first='面部特写', last=None):
    return dict(start=0, end=end, start_scale=first, end_scale=last or first,
                static_spans=[])


def extension_sample():
    data = mode_sample('真人剧')
    a, b = data['segments'][0]['shots']
    a.update(end=3, start_scale='面部特写', end_scale='面部特写',
             closeup_spans=[span(0, 3, True)])
    a['visual_text'] += a['closeup_spans'][0]['finish_evidence']
    b['start'] = 3
    return data


class CloseupPacingTests(unittest.TestCase):
    def errors(self, item):
        errors = []
        check_closeups(item, 'test', errors)
        return errors

    def test_all_four_types_have_same_two_second_boundary(self):
        for scale in CLOSEUPS:
            for duration in (1, 1.6, 2, 2.1, 4, 6):
                with self.subTest(scale=scale, duration=duration):
                    self.assertEqual(bool(self.errors(shot(duration, scale))), duration > 2)

    def test_near_and_medium_near_are_not_closeups(self):
        for scale in ('近景', '中近景', '中景'):
            self.assertEqual(self.errors(shot(5, scale)), [])

    def test_legacy_timing_only_records_do_not_acquire_framing(self):
        self.assertEqual(self.errors(dict(start=0, end=4)), [])

    def test_four_modes_reject_unnecessary_long_closeup(self):
        for mode in ('真人剧', '3D漫真人剧', '推文', '3D漫推文'):
            data = mode_sample(mode)
            a, b = data['segments'][0]['shots']
            a.update(end=3, start_scale='面部特写', end_scale='面部特写')
            b['start'] = 3
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'closeup exceeds 2 seconds'):
                render_plan(data)

    def test_internal_cut_keeps_one_voice_and_original_duration_in_all_modes(self):
        for mode in ('真人剧', '3D漫真人剧', '推文', '3D漫推文'):
            data = mode_sample(mode)
            segment = data['segments'][0]
            a, b = segment['shots']
            a.update(start_scale='面部特写', end_scale='面部特写')
            b.update(start_scale='近景', end_scale='近景', frame_id=a['frame_id'])
            segment['speech'][0]['end'] = 3.2
            before = deepcopy(data)
            plan, text = render_plan(data)
            self.assertEqual(data, before)
            self.assertEqual(validate_delivery(plan, text, require_review=False), [])
            self.assertEqual(plan['segments'][0]['duration'], 4)
            self.assertEqual(len(plan['segments'][0]['speech']), 1)
            self.assertEqual(text.count('“Put it down.”'), 1)
            self.assertIn('内部硬切', text)

    def test_mixed_shot_uses_actual_closeup_interval_not_whole_shot(self):
        item = shot(5, '中近景', '面部特写')
        item['closeup_spans'] = [span(3.5, 5)]
        self.assertEqual(self.errors(item), [])
        item['closeup_spans'][0]['start'] = 2.9
        self.assertTrue(self.errors(item))

    def test_exit_from_closeup_and_middle_closeup_are_supported(self):
        for first, last, start, end in [('面部特写', '中近景', 0, 1.5),
                                       ('近景', '近景', 1.5, 3)]:
            item = shot(5, first, last)
            item['closeup_spans'] = [span(start, end)]
            self.assertEqual(self.errors(item), [])

    def test_real_departure_and_return_need_separated_intervals(self):
        item = shot(5)
        item['closeup_spans'] = [span(0, 1.5), span(3.5, 5)]
        self.assertEqual(self.errors(item), [])
        item['closeup_spans'] = [span(0, 2), span(2, 5, True)]
        self.assertTrue(any('merge continuous' in e for e in self.errors(item)))

    def test_missing_mixed_span_and_false_partial_fixed_span_rejected(self):
        self.assertTrue(self.errors(shot(5, '近景', '面部特写')))
        for spans in ([], [span(1, 3)], [span(0, 2)]):
            item = shot(3)
            item['closeup_spans'] = spans
            self.assertTrue(self.errors(item))

    def test_whole_short_mixed_shot_needs_no_extra_record(self):
        for first, last in [('近景', '面部特写'), ('道具特写', '中景')]:
            self.assertEqual(self.errors(shot(2, first, last)), [])

    def test_silent_vo_and_os_do_not_waive_closeup_duration(self):
        for kind in (None, 'VO', 'OS'):
            data = extension_sample()
            del data['segments'][0]['shots'][0]['closeup_spans']
            if kind is None:
                data['sources'] = []
                data['segments'][0]['speech'] = []
            else:
                data['sources'][0]['kind'] = data['segments'][0]['speech'][0]['kind'] = kind
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'closeup exceeds 2 seconds'):
                render_plan(data)

    def test_necessity_is_conditional_and_compares_a_real_early_cut(self):
        item = shot(3)
        item['closeup_spans'] = [span(0, 3, True)]
        self.assertEqual(self.errors(item), [])
        for cut in (0, 2.1, 3, True, float('inf'), None):
            bad = deepcopy(item)
            bad['closeup_spans'][0]['necessity']['cut_at'] = cut
            self.assertTrue(self.errors(bad))
        item['end'] = item['closeup_spans'][0]['end'] = 2
        self.assertTrue(any('only beyond' in e for e in self.errors(item)))

    def test_generic_audio_excuses_do_not_grant_exception(self):
        for field in ('basis', 'next_view', 'loss_if_cut'):
            for excuse in ('台词还没说完', '保持张力', '完整表演', '情绪强烈', '摄影机仍在移动', ''):
                item = shot(3)
                item['closeup_spans'] = [span(0, 3, True)]
                item['closeup_spans'][0]['necessity'][field] = excuse
                self.assertTrue(self.errors(item))

    def test_explicit_user_exception_needs_actual_user_basis(self):
        item = shot(3)
        item['closeup_spans'] = [span(0, 3, True)]
        record = item['closeup_spans'][0]['necessity']
        record['reason'] = 'user_requested'
        self.assertTrue(self.errors(item))
        record['user_basis'] = '用户明确指定这个3秒面部特写不中断。'
        self.assertEqual(self.errors(item), [])

    def test_bad_records_fail_without_crashing(self):
        for spans in (None, {}, [None], [{}], [dict(start=0, end=3)],
                      [span(float('nan'), 3)], [span(0, float('inf'))],
                      [span(True, 3)], [span(-1, 3)], [span(0, 3.01)],
                      [span(2, 1)], [span(0, 4)]):
            item = shot(3)
            item['closeup_spans'] = spans
            with self.subTest(spans=spans):
                self.assertTrue(self.errors(item))

    def test_short_closeup_phase_is_not_an_independent_subsecond_shot(self):
        item = shot(3, '近景', '面部特写')
        item['closeup_spans'] = [span(2.5, 3)]
        errors = []
        check_shot_pacing(item, 'test', errors, required=True)
        self.assertEqual(errors, [])
        errors = []
        check_shot_pacing(shot(0.5), 'test', errors, required=True)
        self.assertTrue(any('shorter than 1 second' in e for e in errors))

    def test_extension_does_not_waive_static_display_limit(self):
        item = shot(3, '道具特写')
        item['closeup_spans'] = [span(0, 3, True)]
        item['closeup_spans'][0]['necessity']['reason'] = 'reading'
        item['static_spans'] = [dict(start=0, end=3, text='原文字保持静止')]
        errors = []
        check_shot_pacing(item, 'test', errors, required=True)
        self.assertTrue(any('static display exceeds' in e for e in errors))

    def test_extension_does_not_waive_long_dialogue_check(self):
        item = shot(7)
        item['closeup_spans'] = [span(0, 7, True)]
        self.assertEqual(self.errors(item), [])
        errors = []
        check_dialogue_editing({'segments': [dict(id='S1', shots=[item],
            speech=[dict(start=0, end=7, kind='dialogue')])]}, errors)
        self.assertTrue(any('longer than six seconds' in e for e in errors))

    def test_rendered_exception_binds_interval_and_endpoint_without_outputting_reason(self):
        plan, text = render_plan(extension_sample())
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])
        item = plan['segments'][0]['shots'][0]
        self.assertIn(closeup_prose(item), text)
        self.assertNotIn(item['closeup_spans'][0]['necessity']['loss_if_cut'], text)
        self.assertEqual(text.count(item['closeup_spans'][0]['finish_evidence']), 1)
        for token, expected in [(closeup_prose(item), 'closeup intervals are absent'),
                                (item['closeup_spans'][0]['finish_evidence'], 'finish_evidence is absent')]:
            changed = text.replace(token, '')
            self.assertTrue(any(expected in e for e in validate_delivery(plan, changed, require_review=False)))

    def test_final_evidence_in_other_shot_cannot_satisfy_binding(self):
        plan, text = render_plan(extension_sample())
        endpoint = plan['segments'][0]['shots'][0]['closeup_spans'][0]['finish_evidence']
        changed = text.replace(endpoint, '').replace('画面内容：3–4 秒 ', '画面内容：3–4 秒 '+endpoint)
        self.assertTrue(any('finish_evidence is absent' in e
                            for e in validate_delivery(plan, changed, require_review=False)))

    def test_final_interval_cannot_silently_change(self):
        plan, text = render_plan(extension_sample())
        item = plan['segments'][0]['shots'][0]
        item['closeup_spans'][0]['end'] = 2.5
        self.assertTrue(validate_delivery(plan, text, require_review=False))


if __name__ == '__main__':
    unittest.main()
