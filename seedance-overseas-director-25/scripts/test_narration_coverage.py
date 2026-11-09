"""Narrated facts survive richer visual coverage and shorter actual shots.

Synthetic examples verify record/text behavior, not generated video quality.
"""
from copy import deepcopy
import unittest

from narration_coverage import check_narration_coverage, long_narrated_shot
from render_prompt import render_plan
from delivery_checks import validate_delivery
from test_modes import mode_sample
from test_narration_editing import long_plan

CLAUSES = ['I kept my hands at my sides.', 'He held out the flowers and waited.',
           'I turned my eyes toward the phone in his other hand.']
VISUALS = ['Mara双手留在身侧，嘴唇自然闭合，面向Elias。',
           'Elias右手将花束递到两人之间，左手仍持手机，目光等着Mara的回应。',
           'Mara视线从花束移向Elias左手的手机，双手仍未伸出。']


def rich_plan(mode='3D漫推文'):
    data = mode_sample(mode)
    text = ' '.join(CLAUSES)
    data['sources'][0]['text'] = data['narration_sources'][0]['original_text'] = text
    segment = data['segments'][0]
    segment['speech'][0].update(text=text, start=0, end=10, estimated_seconds=10)
    segment['duration'] = 10
    shots = segment['shots']
    shots.append(deepcopy(shots[0]))
    for i, (shot, start, end, scale) in enumerate(zip(shots, (0, 3, 6), (3, 6, 10), ('中景', '中近景', '近景'))):
        shot.update(start=start, end=end, frame_id=f'分镜{i+1}', start_scale=scale, end_scale=scale,
                    visual_text=VISUALS[i])
    shots[0]['camera_text'] += '画框保留Mara双手与身侧的相对位置。'
    shots[1]['camera_text'] += '花束及Elias双手持物均可见。'
    shots[2]['relation']['placements'][0]['placement'] = '在画面右后方，面向Elias，视线转向他左手的手机'
    segment['narration_coverage'] = [
        dict(source_id=data['sources'][0]['id'], text=clause, task=task, focus=focus,
             views=[dict(shot_start=start, evidence=visual)])
        for clause, task, focus, start, visual in zip(CLAUSES, ('reaction', 'action', 'reaction'),
            ('Mara没有接花的手位', 'Elias递花及手机归属', 'Mara注意对象转向手机'), (0, 3, 6), VISUALS)]
    return data


def coverage_errors(data):
    errors = []
    check_narration_coverage(data, errors)
    return errors


class NarrationCoverageTests(unittest.TestCase):
    def assert_finding(self, errors, phrase):
        self.assertTrue(any(phrase in e for e in errors), errors)

    def test_rich_visual_tasks_preserve_one_continuous_voice_in_both_modes(self):
        for mode in ('3D漫推文', '推文'):
            data = rich_plan(mode)
            before = deepcopy(data['segments'][0]['speech'])
            plan, text = render_plan(data)
            with self.subTest(mode=mode):
                self.assertEqual(plan['segments'][0]['speech'], before)
                self.assertEqual(text.count('旁白参考'), 1)
                self.assertIn('“' + ' '.join(CLAUSES) + '”', text)
                self.assertEqual(plan['segments'][0]['duration'], 10)
                self.assertTrue(all(v in text for v in VISUALS))
                self.assertNotIn('narration_coverage', text)
                self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_long_narration_must_account_for_full_source_even_with_short_shots(self):
        data = rich_plan()
        del data['segments'][0]['narration_coverage']
        with self.assertRaisesRegex(ValueError, 'source-to-picture task list'):
            render_plan(data)

    def test_omitted_duplicated_reordered_or_rewritten_clause_is_rejected(self):
        for operation in ('omit', 'duplicate', 'reorder', 'rewrite'):
            data = rich_plan(); units = data['segments'][0]['narration_coverage']
            if operation == 'omit': units.pop(1)
            elif operation == 'duplicate': units.append(deepcopy(units[0]))
            elif operation == 'reorder': units.reverse()
            else: units[0]['text'] = 'I accepted the flowers.'
            with self.subTest(operation=operation):
                self.assert_finding(coverage_errors(data), 'cover the full narrated text once')

    def test_unknown_source_or_shot_and_empty_placements_are_rejected(self):
        for field, value, message in [('source_id', 'future', 'source spoken in this segment'),
                                      ('views', [], 'actual visual placement'),
                                      ('views', [dict(shot_start=2, evidence=VISUALS[0])], 'one actual shot')]:
            data = rich_plan(); data['segments'][0]['narration_coverage'][0][field] = value
            with self.subTest(field=field): self.assert_finding(coverage_errors(data), message)

    def test_repeated_portrait_cannot_be_claimed_as_several_distinct_tasks(self):
        data = rich_plan(); units = data['segments'][0]['narration_coverage']
        units[1]['views'][0]['evidence'] = units[0]['views'][0]['evidence']
        self.assert_finding(coverage_errors(data), 'repeated visual evidence')

    def test_view_removal_relocation_or_negation_fails_final_binding(self):
        plan, text = render_plan(rich_plan())
        evidence = VISUALS[1]
        for changed in (text.replace(evidence, ''),
                        text.replace(evidence, '').replace(VISUALS[0], VISUALS[0] + evidence),
                        text.replace(evidence, '不要呈现：' + evidence)):
            self.assert_finding(validate_delivery(plan, changed, require_review=False), 'narration coverage evidence is absent')

    def test_final_validator_independently_rejects_missing_coverage(self):
        plan, text = render_plan(rich_plan())
        del plan['segments'][0]['narration_coverage']
        self.assert_finding(validate_delivery(plan, text, require_review=False), 'source-to-picture task list')

    def test_one_source_unit_can_have_several_views_without_repeating_spoken_text(self):
        data = rich_plan(); segment = data['segments'][0]
        units = segment['narration_coverage']
        segment['narration_coverage'] = [dict(source_id=units[0]['source_id'], text=' '.join(CLAUSES),
            task='relationship', focus='同一递花过程及双方反应', views=[u['views'][0] for u in units])]
        plan, text = render_plan(data)
        self.assertEqual(text.count('“' + ' '.join(CLAUSES) + '”'), 1)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_multiple_voice_sources_all_need_picture_coverage(self):
        data = rich_plan(); segment = data['segments'][0]
        voice = segment['speech'][0]
        voice.update(text=CLAUSES[0], end=3, estimated_seconds=3)
        data['sources'][0]['text'] = data['narration_sources'][0]['original_text'] = CLAUSES[0]
        source = deepcopy(data['sources'][0]); source.update(id='L2', text=' '.join(CLAUSES[1:]))
        data['sources'].append(source)
        inventory = deepcopy(data['narration_sources'][0]); inventory.update(id='N2', source_id='L2', original_text=source['text'])
        data['narration_sources'].append(inventory)
        second = deepcopy(voice); second.update(source_id='L2', text=source['text'], start=3, end=10, estimated_seconds=7)
        segment['speech'].append(second)
        for unit in segment['narration_coverage'][1:]: unit['source_id'] = 'L2'
        plan, text = render_plan(data)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])
        segment['narration_coverage'] = segment['narration_coverage'][:1]
        self.assert_finding(coverage_errors(data), 'L2: visual task texts')

    def test_whole_shot_duration_triggers_review_when_narration_is_only_four_seconds(self):
        data = long_plan()
        segment = data['segments'][0]
        segment['speech'][0].update(end=4, estimated_seconds=4)
        del segment['shots'][0]['narration_visual']
        self.assertTrue(long_narrated_shot(segment, segment['shots'][0]))
        with self.assertRaisesRegex(ValueError, 'narration_visual'):
            render_plan(data)

    def test_partial_narration_long_shot_needs_coverage_as_well(self):
        data = long_plan(); segment = data['segments'][0]
        segment['duration'] = 8
        segment['speech'][0].update(end=4, estimated_seconds=4)
        del segment['narration_coverage']
        self.assert_finding(coverage_errors(data), 'source-to-picture task list')

    def test_long_take_requires_actual_alternative_cut_not_just_two_changes(self):
        data = long_plan()
        del data['segments'][0]['shots'][0]['narration_visual']['long_take']['cut_review']
        with self.assertRaisesRegex(ValueError, 'cut_review'):
            render_plan(data)
        data = long_plan()
        hold = data['segments'][0]['shots'][0]['narration_visual']['long_take']
        for field, value in [('at', 9), ('at', float('nan')), ('next_view', ''), ('loss_if_cut', '保持张力')]:
            changed = deepcopy(data)
            changed['segments'][0]['shots'][0]['narration_visual']['long_take']['cut_review'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'cut point|cut_review'):
                render_plan(changed)

    def test_declared_user_request_needs_actual_basis(self):
        data = long_plan()
        data['segments'][0]['shots'][0]['narration_visual']['long_take']['reason'] = 'user_requested'
        with self.assertRaisesRegex(ValueError, 'user_basis'):
            render_plan(data)

    def test_legitimate_long_take_still_preserves_original_audio(self):
        data = long_plan()
        plan, text = render_plan(data)
        self.assertEqual(plan['segments'][0]['speech'], data['segments'][0]['speech'])
        self.assertEqual(len(plan['segments'][0]['shots']), 1)
        self.assertNotIn('loss_if_cut', text)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_final_long_shot_check_does_not_depend_on_prior_plan_pass(self):
        plan, text = render_plan(long_plan())
        plan['segments'][0]['shots'][0]['narration_visual']['long_take']['cut_review']['at'] = -1
        self.assert_finding(validate_delivery(plan, text, require_review=False), 'alternative cut point')

    def test_six_second_whole_shot_is_not_an_automatic_error(self):
        data = long_plan(); segment = data['segments'][0]
        segment['shots'][0]['end'] = 6
        segment['speech'][0].update(end=6, estimated_seconds=6)
        del segment['shots'][0]['narration_visual']
        plan, text = render_plan(data)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_non_narration_modes_do_not_gain_coverage_records(self):
        for mode in ('真人剧', '3D漫真人剧'):
            data = mode_sample(mode)
            self.assertEqual(coverage_errors(data), [])
            plan, text = render_plan(data)
            self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_malformed_coverage_fails_with_findings(self):
        for entry in (None, [], {}, '', [None], [dict(source_id=[], text='x')]):
            data = rich_plan(); data['segments'][0]['narration_coverage'] = entry
            with self.subTest(entry=entry): self.assertTrue(coverage_errors(data))
        for value in (True, float('nan'), '0', []):
            data = rich_plan(); data['segments'][0]['narration_coverage'][0]['views'][0]['shot_start'] = value
            with self.subTest(value=value): self.assertTrue(coverage_errors(data))


if __name__ == '__main__':
    unittest.main()
