"""Long dialogue holds, source-required facial reactions and final evidence binding."""
from copy import deepcopy
import unittest

from test_dialogue_reactions import long_dialogue, long_take_progression, errors, WORDS
from render_prompt import render_plan
from delivery_checks import validate_delivery
from mode_rules import MODES
from dialogue_reactions import reaction_prose


def speaker_hold(mode='真人剧', detailed=True):
    data = long_dialogue(mode)
    seg = data['segments'][0]
    shot = seg['shots'][0]
    seg['shots'] = [shot]
    shot['end'] = 12
    evidence = 'Mara 从向对方要求回应转为暴露受伤，最后重新直立并盯住 Elias'
    shot['visual_text'] = evidence
    seg['speech'][0]['dialogue_review'] = dict(mode='hold_speaker', listeners=['Elias'],
        basis='合成原词中 Mara 的指责转入信任破裂，暂不揭示 Elias 的判断。',
        hold=dict(reason='speaker_performance', why='连续看清 Mara 强硬指责中暴露受伤再恢复边界。',
                  segment_id='S1', shot_start=0, evidence=evidence))
    if detailed:
        shot['dialogue_visual'] = long_take_progression()
    return data


class DialogueEditingTests(unittest.TestCase):
    def assert_finding(self, found, phrase):
        self.assertTrue(any(phrase in f for f in found), found)

    def test_legacy_single_reason_cannot_pass_a_long_speaker_shot_all_modes(self):
        for mode in MODES:
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'dialogue_visual'):
                render_plan(speaker_hold(mode, detailed=False))

    def test_three_actual_shots_preserve_one_continuous_utterance_all_modes(self):
        for mode in MODES:
            data = long_dialogue(mode)
            original = deepcopy(data['segments'][0]['speech'])
            plan, text = render_plan(data)
            with self.subTest(mode=mode):
                self.assertEqual(plan['segments'][0]['speech'], original)
                self.assertEqual(text.count('“' + WORDS + '”'), 1)
                self.assertEqual(text.count('内部硬切至本镜'), 2)
                self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_concrete_deliberate_long_take_is_still_possible_all_modes(self):
        for mode in MODES:
            plan, text = render_plan(speaker_hold(mode))
            with self.subTest(mode=mode):
                self.assertEqual(len(plan['segments'][0]['shots']), 1)
                self.assertEqual(text.count('“' + WORDS + '”'), 1)
                self.assertNotIn('内部硬切', text)
                self.assertNotIn('dialogue_visual', text)
                self.assertNotIn('narration_visual', plan['segments'][0]['shots'][0])
                self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_reception_coverage_and_no_listener_do_not_exempt_actual_long_shots(self):
        for variant in ('covered', 'no_listener'):
            data = speaker_hold(detailed=False)
            voice = data['segments'][0]['speech'][0]
            if variant == 'no_listener':
                voice['dialogue_review'] = dict(mode=variant, listeners=[], basis='合成独处自语情境。')
            else:
                voice['dialogue_review'] = deepcopy(long_dialogue()['segments'][0]['speech'][0]['dialogue_review'])
                node = voice['dialogue_review']['reactions'][0]
                node.update(shot_start=0, channel='body', visibility_evidence='Elias 肩背在左前景清楚可见',
                            evidence='Elias 前景双肩从耸起状态落低')
                data['segments'][0]['shots'][0]['camera_text'] += node['visibility_evidence']
            with self.subTest(variant=variant):
                self.assert_finding(errors(data), 'dialogue_visual')

    def test_whole_shot_duration_counts_even_when_dialogue_is_brief(self):
        data = speaker_hold(detailed=False)
        data['segments'][0]['speech'][0].update(start=4, end=6, estimated_seconds=2)
        self.assert_finding(errors(data), 'longer than six seconds')

    def test_six_second_shot_needs_no_long_take_metadata(self):
        data = speaker_hold(detailed=False)
        seg = data['segments'][0]
        seg['duration'] = seg['shots'][0]['end'] = 6
        seg['speech'][0].update(end=6, estimated_seconds=6)
        seg['speech'][0].pop('dialogue_review')
        self.assertEqual(errors(data), [])

    def test_cut_comparison_must_be_inside_shot_and_concrete(self):
        for field, value, message in [('at', 12, 'inside'), ('at', float('nan'), 'inside'),
                                      ('next_view', '', 'next_view'), ('loss_if_cut', '保持完整表演', 'loss_if_cut')]:
            data = speaker_hold()
            data['segments'][0]['shots'][0]['dialogue_visual']['long_take']['cut_review'][field] = value
            with self.subTest(field=field, value=value):
                self.assert_finding(errors(data), message)

    def test_user_requested_hold_needs_actual_request_record(self):
        data = speaker_hold()
        hold = data['segments'][0]['shots'][0]['dialogue_visual']['long_take']
        hold['reason'] = 'user_requested'
        self.assert_finding(errors(data), 'user_basis')
        hold['cut_review']['user_basis'] = '合成用户请求：本句连续拍摄 Mara 的表演。'
        self.assertEqual(errors(data), [])

    def test_timed_stages_cannot_have_gaps_or_repeat_information(self):
        data = speaker_hold()
        beats = data['segments'][0]['shots'][0]['dialogue_visual']['beats']
        beats[1]['start'] = 4.5
        self.assert_finding(errors(data), 'continuously cover')
        beats[1]['start'] = 4
        beats[1]['new_information'] = beats[1]['visual_evidence'] = beats[0]['visual_evidence']
        self.assert_finding(errors(data), 'repeated')

    def test_cosmetic_progression_and_early_finish_cannot_justify_hold(self):
        data = speaker_hold()
        entry = data['segments'][0]['shots'][0]['dialogue_visual']
        entry['beats'][0].update(new_information='Mara 继续皱眉', visual_evidence='Mara 继续皱眉')
        self.assert_finding(errors(data), 'expression/idle-motion-only')
        entry['long_take']['finish_evidence'] = entry['beats'][0]['visual_evidence']
        self.assert_finding(errors(data), 'final beat')

    def test_final_removed_negated_or_retimed_stage_fails(self):
        plan, text = render_plan(speaker_hold())
        stage = plan['segments'][0]['shots'][0]['dialogue_visual']['beats'][1]
        cue = reaction_prose(dict(start=stage['start'], end=stage['end'], evidence=stage['visual_evidence']))
        for broken in (text.replace(cue, ''), text.replace(cue, '不要呈现：' + cue),
                       text.replace(cue, cue.replace('第4秒至第8秒', '第1秒至第3秒'))):
            self.assert_finding(validate_delivery(plan, broken, require_review=False), 'absent')

    def test_required_face_cannot_be_relabelled_as_body(self):
        data = long_dialogue()
        data['sources'][0]['reaction_channels'] = {'Elias': 'face'}
        self.assertEqual(errors(data), [])
        node = data['segments'][0]['speech'][0]['dialogue_review']['reactions'][0]
        node['channel'] = 'body'
        self.assert_finding(errors(data), 'reaction_channels')
        data['sources'][0]['reaction_channels'] = {'Elias': 'body'}
        self.assertEqual(errors(data), [])

    def test_channel_requirement_alone_triggers_review_of_short_critical_line(self):
        data = long_dialogue(reviewed=False)
        data['segments'][0]['speech'][0].update(end=5, estimated_seconds=5)
        data['sources'][0]['reaction_channels'] = {'Elias': 'face'}
        self.assert_finding(errors(data), 'needs a dialogue_review')

    def test_same_frame_body_change_is_allowed_when_source_requires_body(self):
        data = long_dialogue()
        data['sources'][0]['reaction_channels'] = {'Elias': 'body'}
        node = data['segments'][0]['speech'][0]['dialogue_review']['reactions'][0]
        node.update(shot_start=0, start=3.8, end=4, channel='body',
                    visibility_evidence='Elias 肩背在左前景清楚可见', evidence='Elias 前景双肩从耸起状态落低')
        data['segments'][0]['shots'][0]['camera_text'] += node['visibility_evidence']
        plan, text = render_plan(data)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_non_dialogue_is_not_forced_into_dialogue_editing(self):
        for kind in ('narration', 'inner', 'OS', 'VO'):
            data = speaker_hold(detailed=False)
            voice = data['segments'][0]['speech'][0]
            voice['kind'] = kind
            voice.pop('dialogue_review')
            with self.subTest(kind=kind): self.assertEqual(errors(data), [])

    def test_malformed_new_records_report_findings_without_crashing(self):
        for value in ([], None, 3, {}, {'mode': 'progression', 'beats': [None, {}]}):
            data = speaker_hold()
            data['segments'][0]['shots'][0]['dialogue_visual'] = value
            with self.subTest(value=value): self.assertTrue(errors(data))
        for value in ([], {'Elias': []}, {'Elias': 'anything'}, {'': 'face'}):
            data = long_dialogue()
            data['sources'][0]['reaction_channels'] = value
            with self.subTest(value=value): self.assert_finding(errors(data), 'reaction_channels')


if __name__ == '__main__':
    unittest.main()
