"""Narrative audio stays whole while real shots and justified long takes differ.

All examples are synthetic; passing tests do not certify generated video or
replace source-based judgment of a dramatic information change.
"""
from copy import deepcopy
import unittest

from delivery_checks import validate_delivery
from narration_editing import DEFAULT_CUT
from render_prompt import render_plan
from test_delivery_checks import review
from test_modes import mode_sample

SPOKEN = ('I watched him hold out the flowers while the people around us waited '
          'for me to smile and pretend that everything between us was fine.')


def base_plan(mode='3D漫推文'):
    data = mode_sample(mode)
    data['sources'][0]['text'] = SPOKEN
    data['narration_sources'][0]['original_text'] = SPOKEN
    data['segments'][0]['speech'][0].update(text=SPOKEN, start=0, end=8, estimated_seconds=8)
    # This legacy mechanics fixture does not certify source-to-picture semantic completeness.
    data['segments'][0]['narration_coverage'] = [dict(source_id=data['sources'][0]['id'], text=SPOKEN,
        task='relationship', focus='合成测试中的双方关系',
        views=[dict(shot_start=0, evidence=data['segments'][0]['shots'][0]['visual_text'])])]
    return data


def long_plan(mode='3D漫推文'):
    data = base_plan(mode)
    segment = data['segments'][0]
    segment['shots'] = segment['shots'][:1]
    shot = segment['shots'][0]
    shot['end'] = 8
    shot['narration_visual'] = dict(mode='progression', basis='合成测试中的原有递花过程及双方未改变的站位',
        long_take=dict(reason='continuous_action', why_uncut='持续看清花束由伸出到停在双方之间的过程与未接花结果',
                       finish_evidence='Elias将花束停在两人之间，Mara双手留在身侧',
                       cut_review=dict(at=4, next_view='切到花束与双方手位',
                           loss_if_cut='本合成案例要求观众持续核对双方手与花束的接触，切开会遮掉关键交接判断')), beats=[
            dict(start=0, end=4, change='action_progress', new_information='花束从Elias腹前伸到两人之间',
                 visual_evidence='花束从Elias腹前伸到两人之间，Mara目光随花束移动'),
            dict(start=4, end=8, change='relationship_change',
                 new_information='Elias将花束停在两人之间，Mara双手留在身侧',
                 visual_evidence='Elias将花束停在两人之间，Mara双手留在身侧；她把目光从花束转回他的脸')])
    return data


def multi_plan(mode='3D漫推文', same_frame=True):
    data = base_plan(mode)
    shots = data['segments'][0]['shots']
    shots.append(deepcopy(shots[0]))
    for i, (shot, start, end, scale) in enumerate(zip(shots, (0, 2.7, 5.3), (2.7, 5.3, 8), ('近景', '中近景', '近景'))):
        shot.update(start=start, end=end, frame_id='分镜1' if same_frame else f'分镜{i+1}',
                    start_scale=scale, end_scale=scale)
    return data


class NarrationEditingTests(unittest.TestCase):
    def assert_finding(self, errors, needle):
        self.assertTrue(any(needle in error for error in errors), errors)

    def test_old_two_description_long_take_no_longer_passes(self):
        for mode in ('推文', '3D漫推文'):
            data = long_plan(mode)
            entry = data['segments'][0]['shots'][0]['narration_visual']
            del entry['long_take']
            for beat in entry['beats']:
                del beat['change']
                del beat['new_information']
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'long_take|new_information'):
                render_plan(data)

    def test_reported_expression_only_case_fails_even_with_complete_metadata(self):
        for mode in ('推文', '3D漫推文'):
            data = long_plan(mode)
            entry = data['segments'][0]['shots'][0]['narration_visual']
            phrases = ['Mara抬眼看向Elias，眉心收紧', 'Mara继续望着Elias，眉间保持紧绷']
            entry['long_take'].update(reason='performance_arc', finish_evidence=phrases[-1])
            for beat, phrase in zip(entry['beats'], phrases):
                beat.update(change='performance_turn', new_information=phrase, visual_evidence=phrase)
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'expression/idle-motion-only'):
                render_plan(data)

    def test_3d_idle_animation_does_not_establish_a_story_stage(self):
        data = long_plan()
        entry = data['segments'][0]['shots'][0]['narration_visual']
        for beat, phrase in zip(entry['beats'], ['Mara眨眼，耳朵轻摆', 'Elias尾巴晃动，毛发飘动']):
            beat.update(new_information=phrase, visual_evidence=phrase)
        entry['long_take']['finish_evidence'] = entry['beats'][-1]['visual_evidence']
        with self.assertRaisesRegex(ValueError, 'expression/idle-motion-only'):
            render_plan(data)

    def test_concrete_long_take_remains_allowed_without_forced_cuts(self):
        for mode in ('推文', '3D漫推文'):
            data = long_plan(mode)
            before = deepcopy(data)
            with self.assertRaisesRegex(ValueError, 'strictly < 3'):
                render_plan(data)
            self.assertEqual(data, before)

    def test_visible_performance_turn_is_not_banned_as_a_facial_expression(self):
        from mode_rules import check_narrative_visual
        data = long_plan()
        shot = data['segments'][0]['shots'][0]
        entry = shot['narration_visual']
        phrases = ['Mara试图掩饰的笑容消失，在Elias追问时避开他的视线', 'Mara把已经伸向花束的手收回身侧，抬眼直视Elias']
        entry['long_take'].update(reason='performance_arc', why_uncut='连续看清从勉强迎合到收回伸手的态度转折', finish_evidence=phrases[-1])
        for beat, phrase in zip(entry['beats'], phrases):
            beat.update(change='performance_turn', new_information=phrase, visual_evidence=phrase)
        errors = []
        check_narrative_visual(shot, True, errors, 'test')
        self.assertEqual(errors, [])
        with self.assertRaisesRegex(ValueError, 'strictly < 3'):
            render_plan(data)

    def test_audio_continuity_and_generic_tension_are_not_long_take_reasons(self):
        for why in ('旁白还没讲完', '为了保持旁白连续，所以必须一镜到底', '保持张力', '动作连贯'):
            data = long_plan()
            data['segments'][0]['shots'][0]['narration_visual']['long_take']['why_uncut'] = why
            with self.subTest(why=why), self.assertRaisesRegex(ValueError, 'concrete value of not cutting'):
                render_plan(data)

    def test_evidence_belongs_to_its_own_stage_and_final_endpoint(self):
        data = long_plan()
        entry = data['segments'][0]['shots'][0]['narration_visual']
        entry['beats'][0]['new_information'] = entry['beats'][1]['new_information']
        with self.assertRaisesRegex(ValueError, 'this beat visual_evidence'):
            render_plan(data)
        data = long_plan()
        entry = data['segments'][0]['shots'][0]['narration_visual']
        entry['long_take']['finish_evidence'] = entry['beats'][0]['new_information']
        with self.assertRaisesRegex(ValueError, 'final beat'):
            render_plan(data)

    def test_repeated_held_information_cannot_be_relabelled_as_new(self):
        data = long_plan()
        entry = data['segments'][0]['shots'][0]['narration_visual']
        for beat, phrase in zip(entry['beats'], ['Mara右手握住门把', 'Mara仍右手握住门把']):
            beat.update(new_information=phrase, visual_evidence=phrase)
        entry['long_take']['finish_evidence'] = entry['beats'][-1]['visual_evidence']
        with self.assertRaisesRegex(ValueError, 'repeated held information'):
            render_plan(data)

    def test_malformed_long_take_records_report_errors(self):
        for value in (None, [], {}, 'keep it'):
            data = long_plan()
            data['segments'][0]['shots'][0]['narration_visual']['long_take'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'long_take'):
                render_plan(data)
        for field, value in [('reason', []), ('why_uncut', ''), ('finish_evidence', 4)]:
            data = long_plan()
            data['segments'][0]['shots'][0]['narration_visual']['long_take'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                render_plan(data)

    def test_same_continuous_audio_covers_three_actual_shots_in_one_or_three_frames(self):
        for mode in ('推文', '3D漫推文'):
            for same_frame in (True, False):
                data = multi_plan(mode, same_frame)
                original_voice = deepcopy(data['segments'][0]['speech'])
                plan, text = render_plan(data)
                self.assertEqual(plan['segments'][0]['speech'], original_voice)
                self.assertEqual(len(plan['segments'][0]['shots']), 3)
                self.assertEqual(text.count('旁白参考'), 1)
                self.assertEqual(text.count('“' + SPOKEN + '”'), 1)
                self.assertEqual(text.count(DEFAULT_CUT), 2 if same_frame else 0)
                self.assertEqual(plan['segments'][0]['duration'], 8)
                review(plan, text)
                self.assertEqual(validate_delivery(plan, text), [])

    def test_authored_dissolve_is_preserved_instead_of_replaced_by_hard_cut(self):
        data = multi_plan()
        data['segments'][0]['shots'][1]['camera_text'] = '内部叠化到本镜，保留既定关系位。'
        plan, text = render_plan(data)
        self.assertEqual(text.count(DEFAULT_CUT), 1)
        self.assertIn('内部叠化到本镜', text)
        changed = text.replace('内部叠化到本镜', '沿用当前构图')
        review(plan, changed)
        self.assert_finding(validate_delivery(plan, changed), 'authored internal transition is missing')

    def test_negative_cut_instruction_does_not_get_overridden_with_a_hard_cut(self):
        for text in ('不要硬切，接前镜连续。', '不要硬切或叠化，接前镜连续。'):
            data = multi_plan()
            data['segments'][0]['shots'][1]['camera_text'] = text
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'authored internal transition is missing'):
                render_plan(data)

    def test_internal_cut_removed_relocated_or_negated_fails_final_even_after_new_hash(self):
        plan, text = render_plan(multi_plan())
        for replacement in ('', '保持原镜头继续。', '不要' + DEFAULT_CUT):
            changed = text.replace(DEFAULT_CUT, replacement, 1)
            review(plan, changed)
            self.assert_finding(validate_delivery(plan, changed), 'explicit internal cut is missing')
        changed = text.replace(DEFAULT_CUT, '', 1).replace('运镜：0–2.7 秒 ', '运镜：0–2.7 秒 ' + DEFAULT_CUT)
        review(plan, changed)
        self.assert_finding(validate_delivery(plan, changed), 'explicit internal cut is missing')

    def test_plan_and_final_cannot_claim_whole_segment_is_one_take_with_three_shots(self):
        data = multi_plan()
        data['segments'][0]['presentation']['整体风格'] += '整段一镜到底。'
        with self.assertRaisesRegex(ValueError, 'whole-segment no-cut'):
            render_plan(data)
        plan, text = render_plan(multi_plan())
        changed = text.replace('运镜：0–2.7 秒 ', '运镜：0–2.7 秒 本段全程不切镜。', 1)
        review(plan, changed)
        self.assert_finding(validate_delivery(plan, changed), 'whole-segment no-cut')

    def test_final_validator_also_checks_changed_metadata_and_visible_progression(self):
        plan, text = render_plan(multi_plan())
        long_shot = deepcopy(long_plan()['segments'][0]['shots'][0])
        plan['segments'][0]['shots'] = [long_shot]
        long_shot['narration_visual']['long_take']['why_uncut'] = '保持旁白连续'
        errors = validate_delivery(plan, text, require_review=False)
        self.assert_finding(errors, 'strictly < 3')
        self.assert_finding(errors, 'concrete value of not cutting')

    def test_explicit_user_one_take_request_still_needs_visible_progression(self):
        data = long_plan()
        hold = data['segments'][0]['shots'][0]['narration_visual']['long_take']
        hold.update(reason='user_requested', why_uncut='旧案例要求连续观察递花')
        hold['cut_review']['user_basis'] = '旧指令要求长镜；不能覆盖当前小于3秒要求'
        with self.assertRaisesRegex(ValueError, 'strictly < 3'):
            render_plan(data)

    def test_threshold_is_a_review_trigger_not_a_forced_cut_or_duration_limit(self):
        data = long_plan()
        segment = data['segments'][0]
        segment['shots'][0]['end'] = 6
        segment['speech'][0].update(end=6, estimated_seconds=6)
        del segment['shots'][0]['narration_visual']
        with self.assertRaisesRegex(ValueError, 'strictly < 3'):
            render_plan(data)

    def test_drama_modes_do_not_gain_narration_only_metadata_requirements(self):
        for mode in ('真人剧', '3D漫真人剧'):
            data = mode_sample(mode)
            data['segments'][0]['shots'][1]['frame_id'] = '分镜1'
            plan, text = render_plan(data)
            self.assertIn(DEFAULT_CUT, text)
            review(plan, text)
            self.assertEqual(validate_delivery(plan, text), [])


if __name__ == '__main__':
    unittest.main()
