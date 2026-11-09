from copy import deepcopy
import json
from pathlib import Path
import unittest

from delivery_checks import validate_delivery
from render_prompt import render_plan
from shot_pacing import check_shot_pacing, static_span_prose
from test_delivery_checks import review
from test_validate_plan import sample
from validate_plan import validate


def fixture(name):
    return json.loads((Path(__file__).resolve().parent.parent/'assets/evaluation'/name).read_text(encoding='utf-8'))


class ShotPacingTests(unittest.TestCase):
    def findings(self, spans, start=0, end=4):
        errors = []
        check_shot_pacing(dict(start=start, end=end, static_spans=spans), 'test', errors, required=True)
        return errors

    def assert_finding(self, errors, text):
        self.assertTrue(any(text in e for e in errors), errors)

    def test_minimum_includes_dialogue_vo_and_os_coverage(self):
        for kind in ('dialogue', 'VO', 'OS'):
            for cut in (0.9, 1, 1.1):
                with self.subTest(kind=kind, cut=cut):
                    plan = sample()
                    plan['sources'][0]['kind'] = kind
                    segment = plan['segments'][0]
                    segment['speech'][0]['kind'] = kind
                    segment['shots'] = [dict(start=0, end=cut), dict(start=cut, end=4)]
                    errors = validate(plan)
                    if cut < 1:
                        self.assert_finding(errors, 'actual shot is shorter than 1 second')
                    else:
                        self.assertEqual(errors, [])

    def test_speech_itself_can_be_shorter_than_one_second(self):
        plan = sample()
        plan['sources'][0]['text'] = 'Wait.'
        segment = plan['segments'][0]
        segment.update(duration=1, english_words=1, shots=[dict(start=0, end=1)])
        segment['speech'][0].update(text='Wait.', start=0.2, end=0.6, estimated_seconds=0.4)
        self.assertEqual(validate(plan), [])

    def test_parent_frame_does_not_hide_subsecond_internal_cut(self):
        authored = fixture('12-long-dialogue.json')
        authored['segments'][0]['shots'][0]['end'] = 0.9
        authored['segments'][0]['shots'][1]['start'] = 0.9
        with self.assertRaisesRegex(ValueError, 'actual shot is shorter than 1 second'):
            render_plan(authored)

    def test_static_whole_shot_two_second_boundary(self):
        for duration in (1, 1.9, 2, 2.1):
            with self.subTest(duration=duration):
                authored = fixture('13-static-display.json')
                shot = authored['segments'][0]['shots'][0]
                authored['segments'][0].pop('pre_round_duration', None)
                shot['end'] = shot['static_spans'][0]['end'] = duration
                if duration in (1.9,):
                    self.assertEqual(self.findings(shot['static_spans'], end=duration), [])
                    with self.assertRaisesRegex(ValueError, 'final segment duration must be an integer'):
                        render_plan(authored)
                elif duration > 2:
                    with self.assertRaisesRegex(ValueError, 'static display exceeds 2 seconds'):
                        render_plan(authored)
                else:
                    plan, text = render_plan(authored)
                    review(plan, text)
                    self.assertEqual(validate(plan) + validate_delivery(plan, text), [])

    def test_dynamic_shot_can_exceed_two_seconds(self):
        authored = fixture('12-long-dialogue.json')
        original = deepcopy(authored)
        plan, text = render_plan(authored)
        self.assertEqual(authored, original)
        self.assertEqual([s['end'] - s['start'] for s in plan['segments'][0]['shots']], [3, 2, 3])
        self.assertEqual(len(plan['segments'][0]['speech']), 1)
        voice = plan['segments'][0]['speech'][0]
        self.assertEqual((voice['start'], voice['end'], voice['kind']), (0.2, 7.8, 'dialogue'))
        self.assertEqual(text.count('“' + authored['sources'][0]['text'] + '”'), 1)
        self.assertNotIn('delivery_review', plan)
        self.assertTrue(validate_delivery(plan, text))
        review(plan, text)
        self.assertEqual(validate(plan) + validate_delivery(plan, text), [])

    def test_offscreen_voice_does_not_exempt_static_hold(self):
        plan = sample()
        plan['segments'][0]['shots'] = [dict(start=0, end=4, static_spans=[
            dict(start=0, end=3, text='物件停在原位，画面没有变化')])]
        self.assert_finding(validate(plan), 'static display exceeds 2 seconds')

    def test_static_tail_within_dynamic_shot_is_checked(self):
        self.assert_finding(self.findings([dict(start=1.9, end=4, text='动作结果保持不变')]),
                            'static display exceeds 2 seconds')

    def test_short_hold_is_not_an_independent_shot(self):
        self.assertEqual(self.findings([dict(start=3.7, end=4, text='结果保持原状态')]), [])

    def test_separated_static_holds_do_not_add_to_one_limit(self):
        self.assertEqual(self.findings([dict(start=0, end=1.5, text='第一处状态'),
                                       dict(start=2, end=4, text='真实变化后的状态')]), [])

    def test_touching_overlapping_or_reordered_holds_rejected(self):
        for start in (1, 1.5, 0):
            with self.subTest(start=start):
                self.assert_finding(self.findings([dict(start=0, end=1.5, text='物件原位'),
                                                  dict(start=start, end=3, text='物件仍原位')]),
                                    'merge uninterrupted holds')

    def test_bad_shape_and_text_rejected(self):
        for spans in (None, {}, [None], [dict(start=0, end=1)],
                      [dict(start=0, end=1, text='')], [dict(start=0, end=1, text='a\nb')]):
            with self.subTest(spans=spans):
                self.assertTrue(self.findings(spans))

    def test_invalid_times_and_bounds_rejected(self):
        for start, end in ((True, 1), (0, float('inf')), (float('nan'), 1),
                           ('0', 1), (-0.1, 1), (3, 4.1), (1, 1), (2, 1), (0, 1.01)):
            with self.subTest(start=start, end=end):
                self.assertTrue(self.findings([dict(start=start, end=end, text='原状态')]))

    def test_final_metadata_cannot_be_silently_omitted(self):
        authored = fixture('13-static-display.json')
        del authored['segments'][0]['shots'][0]['static_spans']
        with self.assertRaisesRegex(ValueError, 'static_spans is required'):
            render_plan(authored)

    def test_static_prose_removed_after_rehash_rejected(self):
        plan, text = render_plan(fixture('13-static-display.json'))
        changed = text.replace(static_span_prose(plan['segments'][0]['shots'][0]), '')
        review(plan, changed)
        self.assert_finding(validate_delivery(plan, changed), 'static display text/times are absent')

    def test_static_prose_in_wrong_shot_or_field_rejected(self):
        authored = fixture('12-long-dialogue.json')
        # A short held result after the reaction has completed, still under Mara's original voice.
        shot = authored['segments'][0]['shots'][1]
        shot['static_spans'] = [dict(start=4.7, end=5, text='Elias 保持落肩后的状态')]
        plan, text = render_plan(authored)
        prose = static_span_prose(shot)
        for target in ('画面内容：0–3 秒 ', '运镜：0–3 秒 ', '整体风格：'):
            with self.subTest(target=target):
                self.assertIn(target, text)
                moved = text.replace(prose, '').replace(target, target + prose, 1)
                review(plan, moved)
                self.assert_finding(validate_delivery(plan, moved), 'static display text/times are absent')

    def test_metadata_changed_without_final_text_rejected(self):
        plan, text = render_plan(fixture('13-static-display.json'))
        plan['segments'][0]['shots'][0]['static_spans'][0]['end'] = 0.9
        review(plan, text)
        self.assert_finding(validate_delivery(plan, text), 'static display text/times are absent')


if __name__ == '__main__':
    unittest.main()
