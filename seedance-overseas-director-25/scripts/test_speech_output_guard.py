"""Text-level replay regressions; these do not certify generated audio."""
from copy import deepcopy
import unittest

from delivery_checks import validate_delivery
from dialogue_reactions import reaction_prose
from mode_rules import MODES
from render_prompt import render_plan
from speech_continuity import speech_context, speech_carry_prose
from speech_output_guard import (CROSS_SHOT_CUE, complete_source_quote,
                                 check_quoted_speech_copies, check_restart_prose,
                                 check_voice_execution)
from test_dialogue_reactions import long_dialogue, WORDS, REACTION
from test_speech_continuity import long_voice


def quote_findings(value, source='No.'):
    plan = dict(sources=[dict(id='L1', text=source)])
    segment = dict(speech=[dict(source_id='L1')])
    errors = []
    check_quoted_speech_copies(plan, segment, [value], 'test', errors)
    return errors


def restart_findings(value, *, new_turn=False, ended=False, prior=False):
    voice = dict(speaker='Mara', start=0, end=2 if ended else 12)
    segment = dict(id='S1', speech=[voice])
    if new_turn:
        segment['speech'].append(dict(speaker='Mara', start=6, end=7))
    context = {}
    if prior:
        voice.update(start=4)
        context[('S1', 0)] = dict(previous={'voice': {}}, gap=1)
    errors = []
    check_restart_prose(segment, dict(start=4, end=7), context, [value], 'test', errors)
    return errors


class SpeechOutputGuardTests(unittest.TestCase):
    def test_cross_frame_voice_occurs_once_and_real_cuts_survive_all_modes(self):
        for mode in MODES:
            data = long_dialogue(mode)
            for i, shot in enumerate(data['segments'][0]['shots']):
                shot['frame_id'] = f'分镜{i+1}'
            data['segments'][0].pop('frame_notes', None)
            original = deepcopy(data)
            plan, text = render_plan(data)
            with self.subTest(mode=mode):
                self.assertEqual(data, original)
                self.assertEqual(plan['segments'][0]['speech'], original['segments'][0]['speech'])
                self.assertEqual(len(plan['segments'][0]['shots']), 3)
                self.assertEqual(text.count('“' + WORDS + '”'), 1)
                self.assertEqual(text.count('You used my name without asking me.'), 1)
                self.assertEqual(text.count(CROSS_SHOT_CUE), 1)
                self.assertIn('分镜3（7–12 秒）', text)
                self.assertIn(REACTION, text)
                self.assertIn('不因切镜重新起句或重念已完成部分', text)
                self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_internal_cuts_require_single_execution_even_with_one_formal_frame(self):
        plan, text = render_plan(long_dialogue())
        self.assertEqual(text.count('内部硬切至本镜'), 2)
        broken = text.replace('；' + CROSS_SHOT_CUE, '')
        self.assertTrue(any('single-execution' in e for e in
                            validate_delivery(plan, broken, require_review=False)))

    def test_one_actual_shot_needs_no_cross_shot_direction(self):
        voice = dict(start=0, end=1)
        errors = []
        check_voice_execution(dict(shots=[dict(start=0, end=2)]), voice, '清楚', 'test', errors)
        self.assertEqual(errors, [])

    def test_whole_sentence_copy_is_rejected_in_each_nonvoice_location(self):
        plan, text = render_plan(long_dialogue())
        duplicate = '已听到「You used my name without asking me.」。'
        for marker in ('对话氛围：', '运镜：', '画面内容：', '延续质问力度'):
            broken = text.replace(marker, marker + duplicate, 1)
            with self.subTest(marker=marker):
                self.assertTrue(any('complete source speech' in e for e in
                                    validate_delivery(plan, broken, require_review=False)))

    def test_authored_reaction_evidence_cannot_reintroduce_the_whole_quote(self):
        data = long_dialogue()
        node = data['segments'][0]['speech'][0]['dialogue_review']['reactions'][0]
        node['evidence'] += '，Mara刚说过「' + node['trigger']['quote'] + '」'
        with self.assertRaisesRegex(ValueError, 'complete source speech'):
            render_plan(data)

    def test_final_restart_instruction_is_checked_in_its_actual_interval(self):
        plan, text = render_plan(long_dialogue())
        node = plan['segments'][0]['speech'][0]['dialogue_review']['reactions'][0]
        original = reaction_prose(node)
        broken = text.replace(original, original + 'Mara在切镜后从头读整句。')
        self.assertTrue(any('restart instruction' in e for e in
                            validate_delivery(plan, broken, require_review=False)))

    def test_final_necessary_screen_text_can_equal_spoken_sentence(self):
        plan, text = render_plan(long_dialogue())
        amended = text.replace('画面内容：0–4 秒 ',
            '画面内容：0–4 秒 手机屏幕清楚显示“You used my name without asking me.”。', 1)
        self.assertEqual(validate_delivery(plan, amended, require_review=False), [])

    def test_sentence_matching_preserves_accents_and_detects_bounded_sentences(self):
        for first, second in (
            ('No elegí a Ana.', 'Elegí a Lucía.'),
            ('Não escolhi Ana.', 'Escolhi Lúcia.'),
            ('You lied.', 'I trusted you.'),
        ):
            source = first + ' ' + second
            with self.subTest(source=source):
                self.assertTrue(complete_source_quote(first, source))
                self.assertTrue(complete_source_quote(second, source))
                self.assertTrue(complete_source_quote(source, source))
                self.assertFalse(complete_source_quote(first[3:], source))
                self.assertFalse(complete_source_quote(first.rstrip('.'), source))

    def test_all_supported_quote_marks_are_checked(self):
        for left, right in [('“', '”'), ('「', '」'), ('"', '"')]:
            self.assertTrue(quote_findings('Mara听到' + left + 'No.' + right))

    def test_display_roles_and_short_button_labels_are_preserved(self):
        for text, source in (
            ('手机屏幕显示“No.”。', 'No.'),
            ('纸条写着「Não.」', 'Não.'),
            ('短信内容为“No elegí a Ana.”', 'No elegí a Ana.'),
            ('下沿“End”按钮可辨。', 'End'),
        ):
            with self.subTest(text=text):
                self.assertEqual(quote_findings(text, source), [])

    def test_stress_words_and_incomplete_trigger_phrases_are_not_full_replays(self):
        for text, source in (
            ('重读“No.”', 'No.'), ('强调「Não.」', 'Não.'),
            ('重音落在“permission”', 'Who gave you permission to film me?'),
            ('听到“without asking me”后眉心收紧', WORDS),
        ):
            with self.subTest(text=text):
                self.assertEqual(quote_findings(text, source), [])

    def test_authored_repetition_in_the_actual_voice_is_not_removed(self):
        data = long_voice()
        data['sources'][0]['text'] = 'No. No.'
        data['segments'][0]['speech'][0]['text'] = 'No. No.'
        plan, text = render_plan(data)
        self.assertEqual(text.count('“No. No.”'), 1)
        self.assertEqual(plan['segments'][0]['speech'][0]['text'], 'No. No.')
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_explicit_continuing_voice_restart_is_rejected(self):
        for text in ('Mara重新起句。', 'Mara从头重新读。', 'Mara再说一遍。',
                     'Mara开口说出原句。', '同一句从头念。', '原台词重读整句。'):
            with self.subTest(text=text):
                self.assertTrue(restart_findings(text))

    def test_negated_restarts_and_continuation_are_not_rejected(self):
        for text in ('Mara不重新起句。', 'Mara不得从头读。', '同一句不重念整句。',
                     'Mara继续开口说出原句后文。', 'Mara接着开口说出原句后文。',
                     'Mara重读permission，口型接当前后文。'):
            with self.subTest(text=text):
                self.assertEqual(restart_findings(text), [])

    def test_actual_new_turn_or_ended_voice_is_not_a_continuing_restart(self):
        self.assertEqual(restart_findings('Mara开口说出原句。', new_turn=True), [])
        self.assertEqual(restart_findings('Mara开口说出原句。', ended=True), [])

    def test_legitimate_pause_can_resume_without_replaying_completed_words(self):
        self.assertEqual(restart_findings('Mara在必要停顿后重新开口，接续未说完后文。', prior=True), [])
        self.assertTrue(restart_findings('Mara在停顿后从头读。', prior=True))

    def test_other_speaker_and_quoted_screen_instruction_are_not_misattributed(self):
        self.assertEqual(restart_findings('Elias从头读整句。'), [])
        self.assertEqual(restart_findings('屏幕显示“Mara重新起句”。'), [])

    def test_no_audio_carry_is_added_after_actual_speech_end(self):
        plan, _ = render_plan(long_dialogue())
        segment = plan['segments'][0]
        shot = dict(start=12, end=14, visible_characters=['Mara'], start_subject='Mara')
        self.assertEqual(speech_carry_prose(segment, shot, speech_context(plan)), '')


if __name__ == '__main__':
    unittest.main()
