"""Behavioral regressions for real cuts, short narration and bound continuation."""
from copy import deepcopy
import unittest

from camera_views import physical_key, camera_view_prose, check_view_binding
from continuation_boundary import previous_tails
from cut_boundary_checks import boundary_context
from delivery_checks import validate_visual_plan, validate_delivery
from narration_editing import check_narrated_duration
from render_prompt import render_plan, unique_parts
from review_report import collect_review_report
from shot_sequence_review import review_shot_sequence
from test_camera_views import scene
from test_modes import mode_sample
from test_delivery_checks import review


def tail_from(segment, number=1):
    shot = deepcopy(segment['shots'][-1])
    from shot_pacing import fmt
    interval = f'{fmt(shot["start"])}–{fmt(shot["end"])} 秒 '
    scale = shot['start_scale']
    if scale != shot['end_scale']:
        scale += '连续变为' + shot['end_scale']
    return dict(segment_id=segment['id'], segment_number=number, shot=shot,
                final_camera=interval + scale + '，' + camera_view_prose(shot) + shot['camera_text'],
                final_visual=interval + shot['visual_text'])


def continued():
    previous = mode_sample('真人剧')
    tail = tail_from(previous['segments'][0])
    data = mode_sample('真人剧')
    data['segments'][0]['id'] = 'S2'
    data['episodes'][0].update(segment_start=2, previous_tail=tail)
    first = data['segments'][0]['shots'][0]
    first.update(start_scale='近景', end_scale='近景')
    return data


class ShotSequenceTests(unittest.TestCase):
    def test_position_particle_and_direction_synonyms_are_one_camera(self):
        a = dict(space='门厅', position='伊森东侧肩后', height='佐伊眼高')
        b = dict(space='门厅', position='伊森的东边肩后', height='佐伊眼睛高度')
        self.assertEqual(physical_key(a), physical_key(b))
        b['position'] = '伊森西侧肩后'
        self.assertNotEqual(physical_key(a), physical_key(b))

    def test_same_person_crop_cut_rejected_beyond_old_four_pairs(self):
        for pair in [('中景', '近景'), ('近景', '面部特写'), ('全景', '中景')]:
            data = scene()
            a, b = data['segments'][0]['shots']
            a.update(start_scale=pair[0], end_scale=pair[0])
            b.update(start_scale=pair[1], end_scale=pair[1], view=deepcopy(a['view']))
            errors = []
            validate_visual_plan(data, errors, required=True)
            self.assertTrue(any('forbidden playback cut' in e for e in errors), errors)

    def test_true_camera_change_does_not_get_a_blanket_crop_ban(self):
        data = scene()
        a, b = data['segments'][0]['shots']
        a.update(start_scale='中景', end_scale='中景')
        b.update(start_scale='近景', end_scale='近景')
        errors = []
        validate_visual_plan(data, errors, required=True)
        self.assertFalse(any('forbidden playback cut' in e for e in errors), errors)

    def test_narration_limit_is_strict_and_covers_the_whole_shot(self):
        for duration, fail in [(2.999, False), (3, True), (3.001, True), (8, True)]:
            segment = dict(id='S', speech=[dict(kind='narration', start=0, end=.1)])
            shot = dict(start=0, end=duration)
            errors = []
            check_narrated_duration(segment, shot, errors)
            self.assertEqual(bool(errors), fail)

    def test_no_false_narration_overlap_at_exact_cut_and_no_dialogue_cap(self):
        for kind, start, end in [('narration', 4, 5), ('narration', 0, 1), ('dialogue', 1, 4), ('inner', 1, 4)]:
            errors = []
            check_narrated_duration(dict(id='S', speech=[dict(kind=kind, start=start, end=end)]),
                                    dict(start=1, end=4), errors)
            self.assertEqual(errors, [])

    def test_narration_limit_is_translation_invariant(self):
        for mode in ('推文', '3D漫推文'):
            data = mode_sample(mode)
            data['segments'][0]['shots'][0]['end'] = 3
            data['segments'][0]['shots'][1]['start'] = 3
            with self.assertRaisesRegex(ValueError, 'strictly < 3'):
                render_plan(data)

    def test_returning_setup_is_reviewed_not_automatically_forbidden(self):
        data = scene(); segment = data['segments'][0]
        third = deepcopy(segment['shots'][0]); third.update(start=4, end=6, visual_text='Mara听见否认后收回伸出的手。')
        segment['shots'].append(third)
        segment['duration'] = 6
        before = deepcopy(data)
        findings = review_shot_sequence(data)
        returns = [f for f in findings if f['kind'] == 'returning_view']
        self.assertEqual(len(returns), 1)
        self.assertEqual([s['start'] for s in returns[0]['shots']], [0, 4])
        self.assertEqual(data, before)
        report = collect_review_report(data)
        item = next(i for i in report['items'] if i['code'] == 'shot_sequence' and i['finding']['kind'] == 'returning_view')
        self.assertEqual(len(item['locations']), 2)

    def test_renamed_similar_setup_gets_joint_review(self):
        data = scene(); a, b = data['segments'][0]['shots']
        b['view'] = deepcopy(a['view'])
        b['view']['position'] += '略向旁边移半步'
        b['view']['height'] += '略抬高'
        self.assertTrue(any(f['kind'] == 'similar_cut' for f in review_shot_sequence(data)))

    def test_complete_continuation_render_excludes_prior_context_from_output(self):
        data = continued()
        before = deepcopy(data)
        derived, text = render_plan(data)
        self.assertEqual(data, before)
        self.assertEqual(derived['segments'][0]['duration'], 4)
        self.assertEqual(text.count('预估时长'), 1)
        self.assertIn('第 2 段｜', text)
        self.assertNotIn('previous_tail', text)
        self.assertEqual(validate_delivery(derived, text, require_review=False), [])

    def test_two_person_dialogue_review_excludes_foreground_shoulder(self):
        data = scene(); segment = data['segments'][0]
        shot = segment['shots'][0]; segment['shots'] = [shot]; shot['end'] = 4
        self.assertFalse(any(f['kind'] == 'shared_dialogue' for f in review_shot_sequence(data)))
        shot['view']['foreground'] = []
        self.assertTrue(any(f['kind'] == 'shared_dialogue' for f in review_shot_sequence(data)))

    def test_missing_continuation_cannot_be_marked_passed(self):
        data = continued(); del data['episodes'][0]['previous_tail']
        errors = []
        validate_visual_plan(data, errors, required=True)
        self.assertEqual(sum('saved previous actual shot' in e for e in errors), 1, errors)

    def test_snapshot_is_bound_and_does_not_mutate_plan(self):
        data = continued(); before = deepcopy(data); errors = []
        self.assertTrue(previous_tails(data, errors, required=True))
        self.assertEqual(errors, []); self.assertEqual(data, before)
        data['episodes'][0]['previous_tail']['shot']['view']['position'] = '另一侧'
        errors = []; previous_tails(data, errors, required=True)
        self.assertTrue(any('differs' in e for e in errors), errors)

    def test_stale_predecessor_number_is_rejected(self):
        for value in (3, True, '1', None):
            data = continued(); data['episodes'][0]['previous_tail']['segment_number'] = value
            errors = []; previous_tails(data, errors, required=True)
            self.assertTrue(any('stale' in e for e in errors), errors)

    def test_similar_cut_review_also_covers_saved_previous_tail(self):
        data = continued()
        tail = data['episodes'][0]['previous_tail']
        first = data['segments'][0]['shots'][0]
        first['view'] = deepcopy(tail['shot']['view'])
        first['view']['position'] += '略向东半步'
        findings = review_shot_sequence(data)
        candidates = [f for f in findings if f.get('previous_tail')]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['kind'], 'similar_cut')
        self.assertEqual(candidates[0]['shots'][0]['segment_id'], 'S2')
        self.assertEqual(candidates[0]['previous_tail']['segment_id'], 'S1')

    def test_continuation_same_scale_is_checked_before_rendering(self):
        data = continued()
        prior = data['episodes'][0]['previous_tail']['shot']
        first = data['segments'][0]['shots'][0]
        first['start_scale'] = prior['end_scale']
        errors = []; validate_visual_plan(data, errors, required=True)
        self.assertTrue(any('same scale across prompt boundary' in e for e in errors), errors)

    def test_same_subject_continuation_still_requires_visible_change_evidence(self):
        data = continued()
        first = data['segments'][0]['shots'][0]
        first['view'].update(subject='Elias', foreground=['Mara'])
        first.update(start_subject='Elias', end_subject='Elias')
        errors = []; boundary_context(data, errors, required=True)
        self.assertTrue(any('visible composition change evidence' in e for e in errors), errors)

    def test_compact_moving_camera_keeps_all_endpoint_changes_bound(self):
        shot = deepcopy(scene()['segments'][0]['shots'][0])
        shot['end_view'] = deepcopy(shot['view'])
        shot['end_view'].update(position='长桌东侧中央', background='门与北窗交界')
        compact = camera_view_prose(shot)
        expanded = camera_view_prose(shot, style='expanded')
        self.assertLess(len(compact), len(expanded))
        errors = []; check_view_binding(shot, [compact], 'S', errors)
        self.assertEqual(errors, [])
        errors = []; check_view_binding(shot, [compact.replace('门与北窗交界', '')], 'S', errors)
        self.assertTrue(errors)

    def test_exact_fragment_dedup_preserves_evidence_and_distinct_actions(self):
        self.assertEqual(unique_parts('甲放下手机。乙肩背在画左。', '乙肩背在画左。', '甲拿回手机。'),
                         '甲放下手机。乙肩背在画左。甲拿回手机。')
        self.assertEqual(unique_parts('甲放下手机。', '甲再次放下手机。'), '甲放下手机。甲再次放下手机。')


if __name__ == '__main__':
    unittest.main()
