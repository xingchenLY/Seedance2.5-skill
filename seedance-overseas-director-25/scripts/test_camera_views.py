from copy import deepcopy
import json
from pathlib import Path
import unittest

from camera_views import camera_view_prose
from delivery_checks import CONDITIONAL_CUTS, validate_delivery, validate_visual_plan
from render_prompt import render_plan
from test_delivery_checks import review


def scene():
    path = Path(__file__).resolve().parent.parent / 'assets/evaluation/04-emotion.json'
    plan = json.loads(path.read_text(encoding='utf-8'))
    first, second = plan['segments'][0]['shots']
    first.update(start_scale='近景', end_scale='近景')
    second.update(start_scale='中近景', end_scale='中近景')
    return plan


class CameraViewTests(unittest.TestCase):
    def assert_render_error(self, plan, message):
        with self.assertRaises(ValueError) as caught:
            render_plan(plan)
        self.assertIn(message, str(caught.exception))

    def test_renumbering_cannot_authorize_any_of_four_pairs(self):
        for left, right in CONDITIONAL_CUTS:
            with self.subTest(pair=(left, right)):
                plan = scene()
                first, second = plan['segments'][0]['shots']
                first.update(start_scale=left, end_scale=left)
                second.update(start_scale=right, end_scale=right)
                second['view'] = deepcopy(first['view'])
                self.assertNotEqual(first['end_camera'], second['start_camera'])
                self.assert_render_error(plan, 'forbidden playback cut')

    def test_missing_views_cannot_fall_back_to_different_ids(self):
        for omitted in [(0,), (1,), (0, 1)]:
            plan = scene()
            for i in omitted:
                plan['segments'][0]['shots'][i].pop('view')
            with self.subTest(omitted=omitted):
                self.assert_render_error(plan, 'camera endpoint views required')

    def test_changing_subject_fields_without_view_is_rejected(self):
        plan = scene()
        plan['segments'][0]['shots'][1].update(start_subject='Elias', end_subject='Elias')
        self.assert_render_error(plan, 'primary character differs from camera view')

    def test_foreground_shoulder_cannot_also_be_primary(self):
        plan = scene()
        plan['segments'][0]['shots'][1]['view']['subject'] = 'Elias'
        self.assert_render_error(plan, 'relationship-only foreground character')

    def test_background_direction_and_punctuation_do_not_establish_camera_move(self):
        for change in ['background', 'direction', 'punctuation']:
            plan = scene()
            first, second = plan['segments'][0]['shots']
            second['view'] = deepcopy(first['view'])
            if change == 'punctuation':
                second['view']['position'] += '。 '
            else:
                second['view'][change] = '另一背景参照' if change == 'background' else '稍向右转但仍主拍 Mara'
            with self.subTest(change=change):
                self.assert_render_error(plan, 'forbidden playback cut')

    def test_real_position_or_height_change_is_allowed(self):
        for field in ['position', 'height']:
            plan = scene()
            first, second = plan['segments'][0]['shots']
            second['view'] = deepcopy(first['view'])
            second['view'][field] = '长桌西侧斜前方' if field == 'position' else 'Mara 胸口高度'
            with self.subTest(field=field):
                derived, text = render_plan(plan)
                self.assertEqual(validate_delivery(derived, text, require_review=False), [])

    def test_same_camera_real_subject_change_is_allowed(self):
        plan = scene()
        first, second = plan['segments'][0]['shots']
        second.update(start_camera=first['end_camera'], end_camera=first['end_camera'])
        second['view'] = deepcopy(first['view'])
        second['view'].update(subject='Elias', foreground=['Mara'], direction='Elias', background='Elias 身后南墙')
        second['camera_text'] = '固定位置转向 Elias 后切入听者近景，轴线同侧。'
        second['visual_text'] = 'Mara 的肩背在前景，Elias 在后方听着 Mara 的质问，保持嘴部闭合与原紧绷状态。'
        derived, text = render_plan(plan)
        self.assertEqual(validate_delivery(derived, text, require_review=False), [])

    def test_same_scale_and_same_physical_camera_still_fail_after_renumbering(self):
        plan = scene()
        first, second = plan['segments'][0]['shots']
        second.update(start_scale='近景', end_scale='近景')
        second['view'] = deepcopy(first['view'])
        second['view'].update(subject='Elias', foreground=['Mara'], direction='Elias')
        self.assert_render_error(plan, 'same scale and camera')

    def test_continuous_scale_change_within_one_shot_is_allowed(self):
        plan = scene()
        segment = plan['segments'][0]
        first = segment['shots'][0]
        first.update(end=4, end_scale='中近景', camera_text='保持观察方向，连续变焦拉远到中近景，结束时落稳。')
        segment['shots'] = [first]
        beats = plan['action_tracks'][0]['beats']
        beats[0]['action_text'] += beats[1]['action_text']
        beats[0]['after'] = beats[1]['after']
        plan['action_tracks'][0]['beats'] = beats[:1]
        derived, text = render_plan(plan)
        self.assertEqual(validate_delivery(derived, text, require_review=False), [])

    def test_cut_compares_motion_end_not_start(self):
        plan = scene()
        first, second = plan['segments'][0]['shots']
        first['end_camera'] = 'motion-end'
        first['end_view'] = deepcopy(second['view'])
        first['camera_text'] = '摄影机从南端沿桌侧移动到西侧斜前方，保持 Mara 为主拍，落稳后切出。'
        self.assert_render_error(plan, 'forbidden playback cut')

    def test_motion_end_needs_explicit_record(self):
        plan = scene()
        plan['segments'][0]['shots'][0]['end_camera'] = 'moved-camera'
        self.assert_render_error(plan, 'end: camera view is required')

    def test_valid_motion_endpoint_binds_both_states_to_text(self):
        plan = scene()
        first = plan['segments'][0]['shots'][0]
        first['end_camera'] = 'south-west-end'
        first['end_view'] = deepcopy(first['view'])
        first['end_view']['position'] = '长桌南侧偏西'
        first['camera_text'] = '从起始位置沿桌侧向西移动，保持 Mara 为主拍，落稳后切出。'
        derived, text = render_plan(plan)
        self.assertIn('起始时，', text)
        self.assertIn('结束时，', text)
        self.assertEqual(validate_delivery(derived, text, require_review=False), [])

    def test_camera_prose_cannot_be_removed_after_rehash(self):
        plan, text = render_plan(scene())
        evidence = camera_view_prose(plan['segments'][0]['shots'][0])
        text = text.replace(evidence, '')
        review(plan, text)
        self.assertTrue(any('camera view evidence is absent' in e for e in validate_delivery(plan, text)))

    def test_internal_camera_evidence_cannot_be_borrowed_from_another_shot(self):
        authored = scene()
        authored['segments'][0]['shots'][1]['frame_id'] = '分镜1'
        plan, text = render_plan(authored)
        first, second = plan['segments'][0]['shots']
        text = text.replace(camera_view_prose(first), camera_view_prose(second))
        review(plan, text)
        self.assertTrue(any('分镜1/0–2: camera view evidence is absent' in e for e in validate_delivery(plan, text)))

    def test_camera_metadata_change_needs_final_text_update(self):
        plan, text = render_plan(scene())
        plan['segments'][0]['shots'][0]['view']['position'] = '新位置'
        review(plan, text)
        self.assertTrue(any('camera view evidence is absent' in e for e in validate_delivery(plan, text)))

    def test_cross_segment_ids_do_not_reset_physical_camera(self):
        authored = scene()
        first_segment = authored['segments'][0]
        second_segment = deepcopy(first_segment)
        second_segment['id'] = 'S2'
        second_segment['shots'] = [second_segment['shots'][1]]
        second_segment['shots'][0].update(start=0, end=2, frame_id='分镜1')
        second_segment['shots'][0]['view'] = deepcopy(first_segment['shots'][0]['view'])
        second_segment['speech'] = [second_segment['speech'][1]]
        second_segment['speech'][0].update(start=0.1, end=1.7)
        first_segment['shots'] = [first_segment['shots'][0]]
        first_segment['speech'] = [first_segment['speech'][0]]
        authored['segments'].append(second_segment)
        authored['action_tracks'][0]['beats'][1].update(segment_id='S2', shot_start=0)
        self.assert_render_error(authored, 'forbidden playback cut')

    def test_playback_uses_actual_endpoints_even_when_reordered(self):
        plan, _ = render_plan(scene())
        other = deepcopy(plan['segments'][0])
        other['id'] = 'S2'
        plan['segments'].append(other)
        plan['playback'] = [dict(segment_id='S2', start=2, end=4), dict(segment_id='S1', start=0, end=2)]
        plan['segments'][0]['shots'][0]['view'] = deepcopy(other['shots'][1]['view'])
        errors = []
        validate_visual_plan(plan, errors, required=True)
        self.assertTrue(any('forbidden playback cut' in e for e in errors), errors)

    def test_invalid_view_shapes_are_errors(self):
        for change in [None, [], {}, {'foreground': ['Elias', 'Elias']}, {'subject': 'Nobody'},
                       {'position': ''}, {'height': '眼高\n注入'}, {'foreground': [['Elias']]}]:
            plan = scene()
            shot = plan['segments'][0]['shots'][0]
            if isinstance(change, dict) and change:
                shot['view'].update(change)
            else:
                shot['view'] = change
            with self.subTest(change=change), self.assertRaises(ValueError):
                render_plan(plan)


if __name__ == '__main__':
    unittest.main()
