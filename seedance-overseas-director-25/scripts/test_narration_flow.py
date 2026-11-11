"""Narration flow regressions with synthetic source material, never video claims."""
from copy import deepcopy
import unittest

from delivery_checks import validate_delivery
from render_prompt import render_plan
from test_modes import mode_sample
from test_delivery_checks import review
from validate_plan import validate


def split_sample(mode='推文', left='She did not know that the',
                 right='man beside her was her brother.', gap=0):
    authored = mode_sample(mode)
    sentence = left + ' ' + right
    authored['sources'][0]['text'] = sentence
    authored['narration_sources'][0]['original_text'] = sentence
    first = authored['segments'][0]['speech'][0]
    first.update(text=left, start=0, end=2, estimated_seconds=2)
    second = deepcopy(first)
    second.update(text=right, start=2+gap, end=4, estimated_seconds=2-gap)
    if gap:
        second['pause_before'] = dict(reason='natural_pause', basis='逗号处自然换气。', seconds=gap)
    authored['segments'][0]['speech'].append(second)
    return authored


def boundary(left, right, kind='clause'):
    record = dict(kind=kind, left=left, right=right, basis='主句结束，随后进入时间状语从句。')
    if kind != 'clause':
        record['source_evidence'] = '合成测试原稿在此明确标注中断／两个独立口播单元。'
    return record


def separate_sources(authored):
    first, second = authored['segments'][0]['speech']
    second.pop('pause_before', None)
    authored['sources'][0]['text'] = first['text']
    source = deepcopy(authored['sources'][0])
    source.update(id='L2', text=second['text'])
    authored['sources'].append(source)
    second['source_id'] = 'L2'
    authored['narration_sources'][0]['original_text'] = first['text']
    entry = deepcopy(authored['narration_sources'][0])
    entry.update(id='N2', source_id='L2', source_ref='合成测试的后一来源', original_text=second['text'])
    authored['narration_sources'].append(entry)
    return authored


class NarrationFlowTests(unittest.TestCase):
    def assert_valid(self, authored):
        plan, text = render_plan(authored)
        review(plan, text)
        self.assertEqual(validate(plan) + validate_delivery(plan, text), [])
        return plan, text

    def test_single_narration_record_spans_formal_frames_without_repeating_words(self):
        for mode in ('推文', '3D漫推文'):
            authored = split_sample(mode)
            first, second = authored['segments'][0]['speech']
            first.update(text=authored['sources'][0]['text'], end=4, estimated_seconds=4)
            authored['segments'][0]['speech'] = [first]
            with self.subTest(mode=mode):
                _, text = self.assert_valid(authored)
                self.assertEqual(text.count('旁白参考'), 1)
                self.assertIn('分镜2', text)
                self.assertIn('接续同一句后文', text)

    def test_zero_gap_fragmentation_is_rejected_in_both_narrative_modes(self):
        for mode in ('推文', '3D漫推文'):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'one speech record across frames'):
                render_plan(split_sample(mode))

    def test_article_noun_split_with_real_gap_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unnatural narration split inside a phrase'):
            render_plan(split_sample(gap=.4))

    def test_unmarked_boundary_requires_local_linguistic_review(self):
        authored = split_sample(left='She closed the door', right='before anyone could enter.', gap=.4)
        with self.assertRaisesRegex(ValueError, 'boundary_before'):
            render_plan(authored)
        second = authored['segments'][0]['speech'][1]
        second['pause_before']['basis'] = '主句完成后自然换气，再接时间状语从句。'
        second['boundary_before'] = boundary('closed the door', 'before anyone')
        self.assert_valid(authored)

    def test_real_comma_pause_is_kept_without_extra_boundary_record(self):
        self.assert_valid(split_sample(left='She stopped,', right='then entered the room.', gap=.4))

    def test_object_pronoun_is_not_mistaken_for_a_possessive_determiner(self):
        authored = split_sample(left='He believed her', right='because she was right.', gap=.4)
        voice = authored['segments'][0]['speech'][1]
        voice['pause_before']['basis'] = '主句表达完整，随后接原因从句。'
        voice['boundary_before'] = boundary('believed her', 'because she')
        voice['boundary_before']['basis'] = 'her在此为宾语，完整主句后接原因从句。'
        self.assert_valid(authored)

    def test_mechanical_pause_reasons_are_rejected_even_at_a_comma(self):
        for basis in ('为切镜留出停顿', '等待画面切换', '给反应镜头留白', '凑时长到整数',
                      'pause for the next shot'):
            authored = split_sample(left='She stopped,', right='then entered the room.', gap=.4)
            authored['segments'][0]['speech'][1]['pause_before']['basis'] = basis
            with self.subTest(basis=basis), self.assertRaisesRegex(ValueError, 'pause basis cannot'):
                render_plan(authored)

    def test_negative_continuity_instructions_are_not_misread_as_pauses(self):
        authored = split_sample(left='She stopped,', right='then entered the room.', gap=.4)
        authored['segments'][0]['speech'][1]['pause_before']['basis'] = '逗号处自然换气，与切镜无关。'
        for voice in authored['segments'][0]['speech']:
            voice['performance'] += '，不因切镜停顿，do not pause for cuts'
        self.assert_valid(authored)

    def test_source_interruption_is_preserved_with_bound_evidence(self):
        for reason in ('source_pause', 'story_break'):
            authored = split_sample(gap=.4)
            voice = authored['segments'][0]['speech'][1]
            voice['pause_before'].update(reason=reason, basis='合成原稿要求此处被打断，随后接回原句。')
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValueError, 'source_break'):
                    render_plan(authored)
                voice['boundary_before'] = boundary('that the', 'man beside', 'source_break')
                self.assert_valid(authored)
                voice['boundary_before'].pop('source_evidence')
                with self.assertRaisesRegex(ValueError, 'source_evidence'):
                    render_plan(authored)

    def test_boundary_review_cannot_describe_a_different_break(self):
        authored = split_sample(left='She closed the door', right='before anyone could enter.', gap=.4)
        authored['segments'][0]['speech'][1]['boundary_before'] = boundary('wrong phrase', 'before anyone')
        with self.assertRaisesRegex(ValueError, 'match the actual boundary text'):
            render_plan(authored)

    def test_new_source_ids_cannot_hide_an_article_noun_split(self):
        for mode in ('推文', '3D漫推文'):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'suspicious source-ID split'):
                render_plan(separate_sources(split_sample(mode, gap=.4)))

    def test_two_complete_original_sentences_remain_independent(self):
        self.assert_valid(separate_sources(split_sample(left='She stopped.', right='Then she turned.', gap=.4)))

    def test_unpunctuated_independent_source_units_require_evidence(self):
        authored = separate_sources(split_sample(left='That night', right='Nobody came home.', gap=.4))
        with self.assertRaisesRegex(ValueError, 'independent_unit'):
            render_plan(authored)
        authored['narration_sources'][1]['boundary_before'] = boundary('That night', 'Nobody came', 'independent_unit')
        self.assert_valid(authored)

    def test_final_text_pause_instruction_is_rejected_after_hash_refresh(self):
        for mode in ('推文', '3D漫推文'):
            plan, text = self.assert_valid(mode_sample(mode))
            changed = text.replace('清晰平稳中性', '清晰平稳中性，每次切镜停顿')
            review(plan, changed)  # Synthetic receipt, not a real content review.
            with self.subTest(mode=mode):
                self.assertTrue(any('must not pause for camera' in e for e in validate_delivery(plan, changed)))

    def test_quoted_source_is_not_scanned_as_a_directing_instruction(self):
        authored = mode_sample('推文')
        spoken = '他坚持为了切镜添加停顿。'
        authored['sources'][0].update(text=spoken, language='zh')
        authored['narration_sources'][0].update(original_text=spoken, source_language='zh')
        authored['segments'][0]['speech'][0]['text'] = spoken
        authored['segments'][0]['speech'][0].update(estimated_seconds=1.6, end=1.8)
        self.assert_valid(authored)

    def test_spanish_portuguese_and_chinese_boundaries_are_checked(self):
        for lang, left, right in [('es', 'Ella vio al', 'hombre.'), ('pt', 'Ela viu o', 'homem.'),
                                  ('zh', '她不知道那个', '男人是谁。')]:
            authored = split_sample(left=left, right=right, gap=.4)
            authored['sources'][0]['language'] = lang
            authored['narration_sources'][0]['source_language'] = lang
            with self.subTest(language=lang), self.assertRaisesRegex(ValueError, 'unnatural narration split|boundary_before'):
                render_plan(authored)

    def test_cross_segment_natural_clause_can_continue_without_added_silence(self):
        authored = split_sample(left='After checking the dark hallway one more time, she closed the door',
                                right='before anyone outside could follow her into the room and see him.')
        first = authored['segments'][0]
        second = deepcopy(first)
        second['id'] = 'S2'
        first['shots'] = first['shots'][:1]
        first['speech'] = first['speech'][:1]
        first['shots'][0]['end'] = 4
        first['speech'][0].update(end=4, estimated_seconds=4)
        second['shots'] = second['shots'][1:]
        second['shots'][0].update(start=0, end=4, start_scale='近景', end_scale='近景')
        second['speech'] = second['speech'][1:]
        second['speech'][0].update(start=0, end=4, estimated_seconds=4,
                                  boundary_before=boundary('closed the door', 'before anyone'))
        authored['segments'].append(second)
        self.assert_valid(authored)
        second['speech'][0].update(start=.4, estimated_seconds=3.6)
        with self.assertRaisesRegex(ValueError, 'unexplained speech gap'):
            render_plan(authored)

    def test_title_abbreviation_is_not_a_sentence_boundary(self):
        with self.assertRaisesRegex(ValueError, 'suspicious source-ID split'):
            render_plan(separate_sources(split_sample(left='She saw Dr.', right='Smith.', gap=.4)))

    def test_character_inner_voice_does_not_inherit_narrator_record_rule(self):
        authored = split_sample()
        authored['sources'][0]['kind'] = 'inner'
        authored['narration_sources'] = []
        authored['narration_absence_basis'] = '本测试只有人物内心声。'
        for voice in authored['segments'][0]['speech']:
            voice['kind'] = 'inner'
        self.assert_valid(authored)


if __name__ == '__main__':
    unittest.main()
