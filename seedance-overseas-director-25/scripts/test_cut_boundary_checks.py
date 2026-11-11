"""Regression: 22-2 ends on Zoe; 22-3 reopens on the same over-shoulder view.

Camera descriptions reproduce the reported boundary; speech is a short synthetic
fixture, not a rewrite of the episode. No generated-video claims are made.
"""
from copy import deepcopy
import json
import unittest

from camera_views import endpoint_view, validate_views
from cut_boundary_checks import boundary_context
from delivery_checks import validate_delivery, validate_visual_plan
from mode_rules import MODES
from render_prompt import render_plan
from test_delivery_checks import review
from test_modes import mode_sample


def boundary_sample(mode='推文'):
    plan = json.loads(json.dumps(mode_sample(mode), ensure_ascii=False)
                      .replace('Mara', '佐伊').replace('Elias', '伊森'))
    first = plan['segments'][0]
    first['shots'] = first['shots'][:1]
    first['duration'] = 2
    shot = first['shots'][0]
    shot.update(start_scale='近景', end_scale='近景', start_camera='south-offset', end_camera='south-offset')
    shot['view'].update(space='新公司办公楼门外与台阶', position='南端伊森东侧肩后向东错开半步处',
                        direction='西北方佐伊', height='佐伊眼高', subject='佐伊',
                        foreground=['伊森'], background='台阶边缘')
    shot['camera_text'] = '固定取景，主体与必要关系位置清楚。'
    second = deepcopy(first)
    second['id'] = 'S2'
    second['shots'][0].update(start_scale='中近景', end_scale='中近景',
                               start_camera='south', end_camera='south')
    second['shots'][0]['view'].update(position='南端伊森东侧肩后', background='北侧入口台阶')
    second['speech'][0]['source_id'] = 'L2'
    second['speech'][0]['text'] = 'Please stop now.'
    source = deepcopy(plan['sources'][0])
    source.update(id='L2', text='Please stop now.')
    plan['sources'].append(source)
    if 'narration_sources' in plan:
        narration = deepcopy(plan['narration_sources'][0])
        narration.update(id='N2', source_id='L2', original_text='Please stop now.', source_ref='测试源文第2句')
        plan['narration_sources'].append(narration)
    plan['segments'].append(second)
    if mode in ('推文', '3D漫推文'):
        # Keep the camera-boundary test in the valid narrative segment window.
        # Synthetic speech is lengthened, rather than adding silent padding.
        for segment, source, narration, spoken in zip(plan['segments'], plan['sources'],
                plan['narration_sources'], (
                    'Put it down and leave it exactly where it is right now, please.',
                    'Please stop filming me and put that phone down on the table.')):
            segment['duration'] = segment['shots'][0]['end'] = 4
            segment['speech'][0].update(text=spoken, start=0, end=4, estimated_seconds=4)
            source['text'] = narration['original_text'] = spoken
    return plan


def visible_change(plan):
    before = plan['segments'][0]['shots'][-1]
    after = plan['segments'][1]['shots'][0]
    before_text = '切出时伊森肩背占画左前景，佐伊面部呈近正面，双方之间的空间被肩背遮住。'
    after_text = '切入时伊森肩背仅留画左下角，佐伊呈三分之二侧面，双方之间的空隙清楚可见。'
    before['camera_text'] += before_text
    after['view'].update(position='前场东侧靠北的斜侧机位', direction='西侧佐伊与伊森之间')
    after['camera_text'] += after_text
    after['cut_change_before'] = dict(from_segment_id='S1', from_shot_start=0, aspect='occlusion',
                                      before_evidence=before_text, after_evidence=after_text,
                                      basis='以两人间保留的距离呈现佐伊要求伊森停止的原有对峙。')
    return plan


class CutBoundaryTests(unittest.TestCase):
    def findings(self, plan):
        errors = []
        validate_visual_plan(plan, errors, required=True)
        return errors

    def assert_finding(self, errors, needle):
        self.assertTrue(any(needle in error for error in errors), errors)

    def test_reported_half_step_close_to_medium_close_fails_all_four_modes(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                data = boundary_sample(mode)
                self.assert_finding(self.findings(data), 'visible composition change evidence')
                with self.assertRaisesRegex(ValueError, 'visible composition change evidence'):
                    render_plan(data)

    def test_scale_label_and_new_camera_id_alone_never_authorize_boundary(self):
        data = boundary_sample()
        after = data['segments'][1]['shots'][0]
        after.update(start_scale='中景', end_scale='中景', start_camera='totally-new', end_camera='totally-new')
        self.assert_finding(self.findings(data), 'visible composition change evidence')

    def test_directional_synonym_does_not_authorize_conditional_cut(self):
        data = boundary_sample()
        data['segments'][0]['shots'][0]['view']['position'] = '长桌南侧，伊森左肩后'
        data['segments'][1]['shots'][0]['view']['position'] = '长桌南边，伊森左肩后'
        self.assert_finding(self.findings(data), 'forbidden playback cut')

    def test_story_backed_visible_change_renders_all_four_modes(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                plan, text = render_plan(visible_change(boundary_sample(mode)))
                review(plan, text)
                self.assertEqual(validate_delivery(plan, text), [])
                self.assertNotIn('cut_change_before', text)

    def test_record_does_not_waive_same_camera_or_same_scale_rules(self):
        data = visible_change(boundary_sample())
        first, second = [s['shots'][0] for s in data['segments']]
        second['view'] = deepcopy(first['view'])
        self.assert_finding(self.findings(data), 'forbidden playback cut')
        second.update(start_scale='近景', end_scale='近景')
        self.assert_finding(self.findings(data), 'same scale across prompt boundary')

    def test_record_must_target_actual_predecessor(self):
        for field, value in [('from_segment_id', 'elsewhere'), ('from_shot_start', 7), ('from_shot_start', True)]:
            with self.subTest(field=field, value=value):
                data = visible_change(boundary_sample())
                data['segments'][1]['shots'][0]['cut_change_before'][field] = value
                self.assert_finding(self.findings(data), 'predecessor differs')

    def test_generic_or_identical_evidence_is_rejected(self):
        for before, after in [('近景，固定取景。', '中近景，机位不同，错开半步。'),
                              ('伊森肩背占画左前景，佐伊在画右后方。', '伊森肩背占画左前景，佐伊在画右后方。')]:
            data = visible_change(boundary_sample())
            record = data['segments'][1]['shots'][0]['cut_change_before']
            record.update(before_evidence=before, after_evidence=after)
            for s, text in zip(data['segments'], [before, after]):
                s['shots'][0]['camera_text'] += text
            self.assert_finding(self.findings(data), 'distinct visible endpoint compositions')

    def test_malformed_record_is_an_error_not_a_crash(self):
        for value in [None, [], {}, dict(from_segment_id='S1')]:
            data = boundary_sample()
            data['segments'][1]['shots'][0]['cut_change_before'] = value
            self.assert_finding(self.findings(data), 'expected from_segment_id')
        for field, value in [('aspect', []), ('before_evidence', 5), ('basis', '')]:
            data = visible_change(boundary_sample())
            data['segments'][1]['shots'][0]['cut_change_before'][field] = value
            self.assertTrue(self.findings(data))

    def test_evidence_must_exist_in_each_authored_shot(self):
        for index in (0, 1):
            data = visible_change(boundary_sample())
            data['segments'][index]['shots'][0]['camera_text'] = '固定取景。'
            self.assert_finding(self.findings(data), 'absent from the authored endpoint shot')

    def test_final_evidence_missing_or_moved_to_other_shot_fails_even_with_new_hash(self):
        for side in ('before', 'after'):
            for relocate in (False, True):
                with self.subTest(side=side, relocate=relocate):
                    plan, text = render_plan(visible_change(boundary_sample()))
                    evidence = plan['segments'][1]['shots'][0]['cut_change_before'][side + '_evidence']
                    text = text.replace(evidence, '')
                    if relocate:
                        other = 'after_evidence' if side == 'before' else 'before_evidence'
                        anchor = plan['segments'][1]['shots'][0]['cut_change_before'][other]
                        text = text.replace(anchor, anchor + evidence)
                    review(plan, text)
                    self.assert_finding(validate_delivery(plan, text), 'correct final shot interval')

    def test_actual_motion_endpoint_controls_boundary_not_start(self):
        data = boundary_sample()
        first, second = [s['shots'][0] for s in data['segments']]
        first['view']['position'] = '门外南侧'
        first['end_view'] = deepcopy(second['view'])
        first['camera_text'] = '摄影机从门外南侧横移到东侧肩后并落稳。'
        self.assert_finding(self.findings(data), 'forbidden playback cut')
        self.assert_finding(self.findings(data), 'visible composition change evidence')

    def test_explicit_motion_with_unchanged_ids_requires_end_view(self):
        data = boundary_sample()
        shot = data['segments'][0]['shots'][0]
        for camera in ('摄影机从门外南侧横移到东侧肩后。', '摄影机转向伊森。'):
            with self.subTest(camera=camera):
                shot['camera_text'] = camera
                self.assertIsNone(endpoint_view(shot, 'end'))
                self.assert_finding(self.findings(data), 'explicit camera motion requires end_view')

    def test_fixed_camera_actor_motion_and_zoom_do_not_require_end_view(self):
        shot = boundary_sample()['segments'][0]['shots'][0]
        for camera in ('摄影机固定，佐伊从北侧走近手机。', '固定机位连续变焦拉远，保留原有朝向与关系位。'):
            shot['camera_text'] = camera
            errors = []
            validate_views(shot, 'test', errors)
            self.assertEqual(errors, [])
            self.assertEqual(endpoint_view(shot, 'end'), shot['view'])

    def test_motion_added_only_to_final_text_requires_endpoint_after_hash_refresh(self):
        plan, text = render_plan(visible_change(boundary_sample()))
        text = text.replace('固定取景，主体与必要关系位置清楚。', '摄影机从门外南侧横移到东侧肩后。', 1)
        review(plan, text)
        self.assert_finding(validate_delivery(plan, text), 'final camera motion requires end_view')

    def test_genuine_reverse_shot_does_not_need_same_subject_record(self):
        data = boundary_sample()
        after = data['segments'][1]['shots'][0]
        after['view'].update(subject='伊森', foreground=['佐伊'], position='北端佐伊东侧肩后', direction='西南方伊森')
        self.assertEqual(self.findings(data), [])

    def test_new_scene_or_episode_does_not_need_same_subject_record(self):
        data = boundary_sample()
        data['segments'][1]['shots'][0]['view']['space'] = '另一处室内'
        self.assertEqual(self.findings(data), [])
        data = boundary_sample()
        data['segments'][1]['episode'] = 'next'
        self.assertEqual(self.findings(data), [])

    def test_noncharacter_insert_does_not_bypass_evidence(self):
        data = boundary_sample()
        for segment in data['segments']:
            segment['shots'][0]['view']['subject'] = None
        self.assert_finding(self.findings(data), 'visible composition change evidence')

    def test_playback_order_controls_predecessor_and_later_reverse_does_not_fix_opening(self):
        data = visible_change(boundary_sample())
        data['playback'] = [dict(segment_id='S2', start=0, end=4), dict(segment_id='S1', start=0, end=4)]
        self.assert_finding(self.findings(data), 'record requires an adjacent cross-prompt cut')
        data = boundary_sample()
        after = data['segments'][1]
        reaction = deepcopy(after['shots'][0])
        reaction.update(start=4, end=6, frame_id='分镜2', start_camera='north', end_camera='north')
        reaction['view'].update(subject='伊森', foreground=['佐伊'], position='北端佐伊东侧肩后')
        after['shots'].append(reaction)
        after['duration'] = 6
        self.assert_finding(self.findings(data), 'visible composition change evidence')

    def test_record_cannot_refer_to_unplayed_shot(self):
        data = visible_change(boundary_sample())
        data['playback'] = [dict(segment_id='S1', start=0, end=4)]
        errors = []
        boundary_context(data, errors, required=True)
        self.assert_finding(errors, 'outside playback')


if __name__ == '__main__':
    unittest.main()
