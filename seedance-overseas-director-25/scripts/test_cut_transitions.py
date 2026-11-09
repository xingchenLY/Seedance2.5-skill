from copy import deepcopy
import json
from pathlib import Path
import unittest

from cut_transitions import jump_cut_prose
from delivery_checks import CONDITIONAL_CUTS, validate_delivery, validate_visual_plan
from render_prompt import render_plan
from test_delivery_checks import review
from validate_plan import validate


def fixture(name='10-jump-cut'):
    return json.loads((Path(__file__).resolve().parent.parent / 'assets/evaluation' /
                       (name + '.json')).read_text(encoding='utf-8'))


class CutTransitionTests(unittest.TestCase):
    def assert_issue(self, authored, message):
        with self.assertRaises(ValueError) as caught:
            render_plan(authored)
        self.assertIn(message, str(caught.exception))

    def test_scoped_jump_roundtrip_preserves_input_and_review_gate(self):
        authored = fixture()
        original = deepcopy(authored)
        plan, text = render_plan(authored)
        self.assertEqual(authored, original)
        self.assertNotIn('delivery_review', plan)
        self.assertEqual(plan['segments'][0]['duration'], 3)
        self.assertTrue(validate_delivery(plan, text))
        review(plan, text)
        self.assertEqual(validate(plan) + validate_delivery(plan, text), [])

    def test_unmarked_same_camera_jump_still_fails(self):
        authored = fixture()
        authored['segments'][0]['shots'][1].pop('jump_cut_before')
        self.assert_issue(authored, 'same scale and camera')

    def test_jump_exception_covers_four_conditional_pairs_only_when_recorded(self):
        for left, right in CONDITIONAL_CUTS:
            authored = fixture()
            first, second = authored['segments'][0]['shots']
            first.update(start_scale=left, end_scale=left)
            second.update(start_scale=right, end_scale=right)
            with self.subTest(pair=(left, right)):
                plan, text = render_plan(authored)
                self.assertEqual(validate_delivery(plan, text, require_review=False), [])
                second.pop('jump_cut_before')
                self.assert_issue(authored, 'forbidden playback cut')

    def test_malformed_and_empty_records_fail(self):
        base = fixture()['segments'][0]['shots'][1]['jump_cut_before']
        invalid = [None, False, {}, [], {**base, 'extra': True}]
        for field in ('basis', 'omitted', 'cue'):
            invalid.extend([{**base, field: ''}, {**base, field: []},
                            {**base, field: 'invalid\nfield'}])
        for value in (True, -1, float('inf'), float('nan'), '0'):
            invalid.append({**base, 'from_shot_start': value})
        for record in invalid:
            with self.subTest(record=record):
                authored = fixture()
                authored['segments'][0]['shots'][1]['jump_cut_before'] = record
                self.assert_issue(authored, 'jump_cut_before')

    def test_first_shot_cannot_declare_jump_from_nowhere(self):
        authored = fixture()
        first, second = authored['segments'][0]['shots']
        first['jump_cut_before'] = deepcopy(second['jump_cut_before'])
        self.assert_issue(authored, 'must follow a shot')

    def test_exception_must_name_actual_predecessor(self):
        authored = fixture()
        authored['segments'][0]['shots'][1]['jump_cut_before']['from_shot_start'] = 0.5
        self.assert_issue(authored, 'actual adjacent playback predecessor')

    def test_endpoint_views_are_required_even_for_other_scale_pairs(self):
        for index in (0, 1):
            authored = fixture()
            authored['segments'][0]['shots'][1].update(start_scale='手部特写', end_scale='手部特写')
            authored['segments'][0]['shots'][index].pop('view')
            with self.subTest(index=index):
                self.assert_issue(authored, 'jump cut requires valid camera endpoint views')

    def test_cross_scene_is_not_a_same_scene_jump_exception(self):
        authored = fixture()
        authored['segments'][0]['shots'][1]['view']['space'] = '另一个房间'
        self.assert_issue(authored, 'limited to the same scene space')

    def test_uses_previous_end_view_not_start(self):
        authored = fixture()
        first = authored['segments'][0]['shots'][0]
        first['end_view'] = deepcopy(first['view'])
        first['end_view']['space'] = '走廊'
        self.assert_issue(authored, 'limited to the same scene space')

    def test_cross_prompt_jump_remains_invalid(self):
        plan, _ = render_plan(fixture())
        first_segment = plan['segments'][0]
        second_segment = deepcopy(first_segment)
        second_segment['id'] = 'S2'
        second_segment['shots'] = [second_segment['shots'][1]]
        second_segment['shots'][0].update(start=0, end=1.5)
        first_segment['shots'] = first_segment['shots'][:1]
        first_segment['duration'] = second_segment['duration'] = 1.5
        plan['segments'].append(second_segment)
        errors = []
        validate_visual_plan(plan, errors, required=True)
        self.assertTrue(any('same episode and prompt segment' in e for e in errors), errors)
        self.assertTrue(any('same scale across prompt boundary' in e for e in errors), errors)

    def test_playback_order_cannot_reuse_a_stale_exception(self):
        plan, _ = render_plan(fixture())
        plan['playback'] = [dict(segment_id='S1', start=1.5, end=3),
                            dict(segment_id='S1', start=0, end=1.5)]
        errors = []
        validate_visual_plan(plan, errors, required=True)
        self.assertTrue(any('must follow a shot' in e for e in errors), errors)

    def test_exception_does_not_spread_to_next_cut(self):
        authored = fixture()
        second = authored['segments'][0]['shots'][1]
        third = deepcopy(second)
        third.update(start=3, end=4.5, frame_id='分镜3')
        third.pop('jump_cut_before')
        authored['segments'][0]['shots'].append(third)
        self.assert_issue(authored, 'same scale and camera')

    def test_jump_marker_does_not_bypass_action_state_continuity(self):
        authored = fixture()
        authored['action_tracks'][0]['beats'][1].pop('break_before')
        self.assert_issue(authored, 'action state jump')

    def test_jump_marker_does_not_bypass_original_speech(self):
        authored = fixture()
        authored['sources'] = [dict(id='L1', episode='current', speaker='Mara', kind='OS',
                                    language='en', text='Another page, then the last.')]
        authored['segments'][0]['speech'] = [dict(source_id='L1', speaker='Mara', kind='OS',
            start=0.1, end=2.9, estimated_seconds=2.8, text='Another page, then the last.',
            performance='低声，语速均匀')]
        plan, text = render_plan(authored)
        self.assertEqual(text.count('“Another page, then the last.”'), 1)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])
        authored['segments'][0]['speech'][0]['text'] = 'Another page.'
        self.assert_issue(authored, 'source text changed')

    def test_jump_cue_cannot_be_removed_after_rehash(self):
        plan, text = render_plan(fixture())
        cue = jump_cut_prose(plan['segments'][0]['shots'][1])
        text = text.replace(cue, '')
        review(plan, text)
        self.assertTrue(any('jump cut cue is absent' in e for e in validate_delivery(plan, text)))

    def test_jump_cue_cannot_be_borrowed_from_another_internal_shot(self):
        authored = fixture()
        first, second = authored['segments'][0]['shots']
        second['frame_id'] = first['frame_id']
        plan, text = render_plan(authored)
        cue = jump_cut_prose(plan['segments'][0]['shots'][1])
        text = text.replace(cue, '').replace(first['camera_text'], cue + first['camera_text'], 1)
        review(plan, text)
        self.assertTrue(any('jump cut cue is absent' in e for e in validate_delivery(plan, text)))

    def test_jump_cue_in_visual_instead_of_camera_is_rejected(self):
        plan, text = render_plan(fixture())
        cue = jump_cut_prose(plan['segments'][0]['shots'][1])
        text = text.replace(cue, '').replace('画面内容：', '画面内容：' + cue, 1)
        review(plan, text)
        self.assertTrue(any('jump cut cue is absent' in e for e in validate_delivery(plan, text)))

    def test_changed_jump_cue_needs_final_text_update(self):
        plan, text = render_plan(fixture())
        plan['segments'][0]['shots'][1]['jump_cut_before']['cue'] = '稍后重新看向末页'
        review(plan, text)
        self.assertTrue(any('jump cut cue is absent' in e for e in validate_delivery(plan, text)))

    def test_necessary_closeup_chain_with_real_reverse_camera_passes(self):
        plan, text = render_plan(fixture('11-closeup-chain'))
        review(plan, text)
        self.assertEqual(validate(plan) + validate_delivery(plan, text), [])
        self.assertEqual(text.count('“You were right.”'), 1)

    def test_closeup_chain_cannot_use_renumbering_as_camera_change(self):
        authored = fixture('11-closeup-chain')
        first, second = authored['segments'][0]['shots']
        second['view'].update(position=first['view']['position'], height=first['view']['height'])
        self.assert_issue(authored, 'same scale and camera')

    def test_closeup_chain_is_not_a_cross_prompt_same_scale_waiver(self):
        authored = fixture('11-closeup-chain')
        first_segment = authored['segments'][0]
        second_segment = deepcopy(first_segment)
        second_segment['id'] = 'S2'
        second_segment['shots'] = [second_segment['shots'][1]]
        second_segment['shots'][0].update(start=0, end=1.5)
        second_segment['speech'] = []
        first_segment['shots'] = first_segment['shots'][:1]
        # Both remain on scene even when each segment shows only one person.
        first_segment['cast'] = ['Mara', 'Elias']
        second_segment['cast'] = ['Mara', 'Elias']
        authored['segments'].append(second_segment)
        self.assert_issue(authored, 'same scale across prompt boundary')


if __name__ == '__main__':
    unittest.main()
