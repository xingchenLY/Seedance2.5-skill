from copy import deepcopy
import json
from pathlib import Path
import unittest

from action_continuity import action_context, action_prose
from delivery_checks import validate_delivery
from render_prompt import render_plan
from test_delivery_checks import review
from validate_plan import validate


def fixture(name='07-dialogue-action'):
    root = Path(__file__).resolve().parent.parent
    return json.loads((root/'assets/evaluation'/f'{name}.json').read_text(encoding='utf-8'))


def findings(plan):
    errors = []
    action_context(plan, errors, required=True)
    return errors


class ActionContinuityTests(unittest.TestCase):
    def assert_issue(self, plan, fragment):
        errors = findings(plan)
        self.assertTrue(any(fragment in e for e in errors), errors)

    def test_action_chain_roundtrip_keeps_timing_speech_and_template(self):
        authored = fixture()
        original = deepcopy(authored)
        plan, text = render_plan(authored)
        self.assertEqual(authored, original)
        self.assertEqual(plan['segments'][0]['duration'], 4)
        self.assertEqual(text.count('“Take the key. Keep it safe.”'), 1)
        self.assertEqual(text.count('画面内容：'), 1)
        self.assertNotIn('action_tracks', text)
        for beat in authored['action_tracks'][0]['beats']:
            self.assertEqual(text.count(beat['action_text']), 1)
        review(plan, text)
        self.assertEqual(validate(plan) + validate_delivery(plan, text), [])

    def test_compact_distinct_actions_keep_release_and_turn_in_same_shot(self):
        plan, text = render_plan(fixture('09-linked-actions'))
        self.assertEqual(plan['segments'][0]['duration'], 1)
        self.assertEqual(len(plan['segments'][0]['shots']), 1)
        self.assertIn('松开杯身并收回右手，随即转肩', text)
        self.assertNotIn('特写', text)

    def test_missing_final_action_declaration_cannot_be_auto_filled(self):
        plan = fixture()
        del plan['action_tracks']
        self.assertEqual(findings({**plan, 'action_tracks': []}), [])
        with self.assertRaisesRegex(ValueError, 'action_tracks'):
            render_plan(plan)

    def test_no_action_case_needs_no_invented_bridge(self):
        plan, text = render_plan(fixture('01-dialogue'))
        self.assertEqual(plan['action_tracks'], [])
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])

    def test_dropped_last_handoff_beat_fails_source_endpoint(self):
        plan = fixture()
        plan['action_tracks'][0]['beats'].pop()
        self.assert_issue(plan, 'does not reach')
        with self.assertRaisesRegex(ValueError, 'does not reach'):
            render_plan(plan)

    def test_jump_directly_to_already_received_object_fails(self):
        plan = fixture()
        second = plan['action_tracks'][0]['beats'][1]
        second['before'] = deepcopy(second['after'])
        self.assert_issue(plan, 'action state jump')

    def test_restart_from_initial_pose_fails(self):
        plan = fixture()
        beats = plan['action_tracks'][0]['beats']
        beats[1]['before'] = deepcopy(beats[0]['before'])
        self.assert_issue(plan, 'action state jump')

    def test_unexplained_hand_change_after_handoff_fails(self):
        plan = fixture()
        track = plan['action_tracks'][0]
        received = deepcopy(track['end_state'])
        wrong = {**received, 'key.owner': 'Elias右手'}
        track['beats'].append(dict(segment_id='S1', shot_start=2.5,
                                   before=wrong, after=received, action_text='Elias 承接持物状态。'))
        self.assert_issue(plan, 'action state jump')

    def test_missing_state_key_or_action_bridge_fails(self):
        for field in ('before', 'after', 'action_text'):
            plan = fixture()
            del plan['action_tracks'][0]['beats'][1][field]
            self.assertTrue(findings(plan))

    def test_state_key_cannot_disappear_mid_chain(self):
        plan = fixture()
        beat = plan['action_tracks'][0]['beats'][1]
        del beat['before']['Elias.left_hand']
        del beat['after']['Elias.left_hand']
        self.assert_issue(plan, 'state keys changed')

    def test_missing_or_invalid_source_endpoint_fails(self):
        for value in (None, {}, 'done', {'key.owner': ''}):
            plan = fixture()
            plan['action_tracks'][0]['end_state'] = value
            self.assert_issue(plan, 'end_state is required')

    def test_actual_shot_binding_not_just_frame_name(self):
        plan = fixture()
        plan['action_tracks'][0]['beats'][1]['shot_start'] = 1.1
        self.assert_issue(plan, 'actual playback shot start')

    def test_invalid_shapes_return_findings(self):
        for value in (None, {}, [None], [{}]):
            plan = fixture()
            plan['action_tracks'] = value
            self.assertTrue(findings(plan))

    def test_invalid_beat_timing_return_findings(self):
        for value in (True, float('nan'), [], '1'):
            plan = fixture()
            plan['action_tracks'][0]['beats'][1]['shot_start'] = value
            self.assertTrue(findings(plan))

    def test_first_beat_cannot_claim_resume_exception(self):
        plan = fixture()
        plan['action_tracks'][0]['beats'][0]['break_before'] = {}
        self.assert_issue(plan, 'first action beat')

    def test_adjacent_cut_cannot_claim_offscreen_progress(self):
        plan = fixture()
        beat = plan['action_tracks'][0]['beats'][1]
        beat['before'] = deepcopy(beat['after'])
        beat['break_before'] = dict(kind='offscreen_progress', basis='切到另一个镜头。',
                                    resume_text='切回时交接已经完成。')
        self.assert_issue(plan, 'actual intervening playback time')

    def test_stale_or_invalid_exception_fails(self):
        base = dict(kind='source_time_jump', basis='原文明示跳时。', resume_text='跳时后取当前姿态。')
        for value in (base, None, {'kind': []}, {**base, 'basis': ''}, {**base, 'resume_text': ''}):
            plan = fixture()
            plan['action_tracks'][0]['beats'][1]['break_before'] = value
            self.assertTrue(findings(plan))

    def test_source_time_jump_can_resume_with_bound_text(self):
        plan = fixture()
        beat = plan['action_tracks'][0]['beats'][1]
        beat['before'] = deepcopy(beat['after'])
        beat['break_before'] = dict(kind='source_time_jump', basis='测试原文明确省略交接经过后跳时。',
                                    resume_text='原文跳时后，钥匙已在 Elias 左手，Mara 右手已经松开。')
        beat['action_text'] = 'Elias 左手保持持钥匙，Mara 右手停在交接处。'
        errors = []
        context = action_context(plan, errors, required=True)
        self.assertEqual(errors, [])
        segment = plan['segments'][0]
        self.assertIn(beat['break_before']['resume_text'], action_prose(segment, segment['shots'][1], context))

    def test_offscreen_progress_uses_elapsed_playback_time(self):
        plan = fixture('04-emotion')
        shots = plan['segments'][0]['shots']
        reaction = deepcopy(shots[0])
        shots[0]['end'] = 1
        reaction.update(start=1, end=2)
        shots.insert(1, reaction)
        track = plan['action_tracks'][0]
        track['beats'][0]['after'] = {'Mara.posture': '正在小幅前倾'}
        beat = track['beats'][1]
        beat['break_before'] = dict(kind='offscreen_progress',
            basis='前倾已在首镜发起，切到听者一秒后回到 Mara，只有非关键姿态变化自然推进。',
            resume_text='切回时 Mara 已从此前正在前倾的阶段落到略前倾姿态，肩背张力仍在。')
        self.assertEqual(findings(plan), [])

    def test_reversed_or_duplicate_shot_beats_fail(self):
        plan = fixture()
        plan['action_tracks'][0]['beats'].reverse()
        self.assert_issue(plan, 'reverse playback order')
        plan = fixture()
        track = plan['action_tracks'][0]
        track['beats'].insert(1, deepcopy(track['beats'][0]))
        self.assert_issue(plan, 'overlap or reverse')

    def test_related_actions_cannot_reset_state_in_another_track(self):
        plan = fixture()
        copy = deepcopy(plan['action_tracks'][0])
        copy['id'] = 'reset-by-renaming'
        plan['action_tracks'].append(copy)
        self.assert_issue(plan, 'already belongs to another action track')

    def test_flashback_time_layers_keep_separate_state(self):
        plan = fixture('05-flashback')
        past = plan['action_tracks'][0]
        present = deepcopy(past)
        present.update(id='present-cup', time_layer='present-hall')
        present['beats'][0]['shot_start'] = 0
        present['end_state'] = {'cup.owner': 'Elias手边桌面', 'Mara.left_hand': '空手'}
        present['beats'][0]['before'] = deepcopy(present['end_state'])
        present['beats'][0]['after'] = deepcopy(present['end_state'])
        present['beats'][0]['action_text'] = '当前时点旧杯仍在 Elias 手边桌面，Mara 左手空着。'
        plan['action_tracks'].insert(0, present)
        self.assertEqual(findings(plan), [])

    def test_cross_segment_state_cannot_reset(self):
        plan = fixture()
        first = plan['segments'][0]
        second = deepcopy(first)
        second['id'] = 'S2'
        second['shots'] = second['shots'][1:]
        for shot in second['shots']:
            shot['start'] -= 1
            shot['end'] -= 1
        first['shots'] = first['shots'][:1]
        first['duration'], second['duration'] = 1, 3
        plan['segments'].append(second)
        beat = plan['action_tracks'][0]['beats'][1]
        beat.update(segment_id='S2', shot_start=0)
        self.assertEqual(findings(plan), [])
        beat['before']['Mara.right_hand'] = '腹前握钥匙'
        self.assert_issue(plan, 'action state jump')

    def test_playback_order_not_generated_segment_order(self):
        plan = fixture()
        segment = plan['segments'][0]
        segment['duration'] = 4
        plan['playback'] = [dict(segment_id='S1', start=1, end=4),
                            dict(segment_id='S1', start=0, end=1)]
        self.assert_issue(plan, 'reverse playback order')

    def test_deleted_bridge_fails_after_text_rehash(self):
        plan, text = render_plan(fixture())
        bridge = plan['action_tracks'][0]['beats'][1]['action_text']
        broken = text.replace(bridge, '')
        review(plan, broken)
        self.assertTrue(any('action bridge is absent' in e for e in validate_delivery(plan, broken)))

    def test_bridge_moved_to_header_camera_or_wrong_internal_shot_fails(self):
        plan, text = render_plan(fixture())
        bridge = plan['action_tracks'][0]['beats'][1]['action_text']
        for destination in ('整体风格：', '运镜：', '画面内容：0–1 秒 '):
            with self.subTest(destination=destination):
                broken = text.replace(bridge, '').replace(destination, destination + bridge, 1)
                review(plan, broken)
                self.assertTrue(any('action bridge is absent' in e for e in validate_delivery(plan, broken)))

    def test_final_check_independently_validates_state_chain(self):
        plan, text = render_plan(fixture())
        beats = plan['action_tracks'][0]['beats']
        beats[1]['before'] = deepcopy(beats[0]['before'])
        review(plan, text)
        self.assertTrue(any('action state jump' in e for e in validate_delivery(plan, text)))


if __name__ == '__main__':
    unittest.main()
