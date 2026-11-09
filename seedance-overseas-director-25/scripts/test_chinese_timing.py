"""Chinese estimates, mixed speech and scheduling; no real audio certification."""
from copy import deepcopy
import unittest

from chinese_timing import chinese_characters, chinese_speech_seconds
from delivery_checks import validate_delivery
from mode_rules import MODES
from render_prompt import render_plan
from test_language_policy import language_sample
from validate_plan import validate


def data_sample(mode='真人剧', text='一二三四五六七，一二三四五六七。', seconds=2):
    data = language_sample('zh', mode)
    data['sources'][0]['text'] = text
    voice = data['segments'][0]['speech'][0]
    voice.update(text=text, estimated_seconds=seconds, end=voice['start'] + seconds)
    for row in data.get('narration_sources', []):
        row['original_text'] = text
    return data


class ChineseTimingTests(unittest.TestCase):
    def test_spoken_han_only_ignores_punctuation_spaces_and_counts_script_forms(self):
        self.assertEqual(chinese_characters('一二三四五六七， 一二三四五六七！\n'), 14)
        self.assertEqual(chinese_characters('請稍候。〇𠮷'), 5)
        self.assertEqual(chinese_characters('Wait! 20 %'), 0)

    def test_four_modes_use_two_seconds_for_fourteen_chars_without_rewording(self):
        for mode in MODES:
            data = data_sample(mode)
            before = deepcopy(data)
            plan, text = render_plan(data)
            self.assertEqual(data, before)
            self.assertEqual(plan['segments'][0]['speech'][0]['estimated_seconds'], 2)
            self.assertEqual(plan['segments'][0]['english_words'], 0)
            self.assertIn('“' + data['sources'][0]['text'] + '”', text)
            self.assertEqual(validate(plan) + validate_delivery(plan, text, require_review=False), [])

    def test_missing_chinese_estimate_can_be_derived_without_changing_authored_timeline(self):
        data = data_sample()
        del data['segments'][0]['speech'][0]['estimated_seconds']
        before = deepcopy(data)
        plan, _ = render_plan(data)
        self.assertNotIn('estimated_seconds', data['segments'][0]['speech'][0])
        self.assertEqual(plan['segments'][0]['speech'][0]['estimated_seconds'], 2)
        self.assertEqual(data, before)
        self.assertEqual([(s['start'], s['end']) for s in plan['segments'][0]['shots']],
                         [(s['start'], s['end']) for s in before['segments'][0]['shots']])

    def test_wrong_estimate_and_padding_voice_interval_are_rejected(self):
        for field, value in [('estimated_seconds', 1), ('estimated_seconds', 2.5), ('end', 3.2)]:
            data = data_sample()
            data['segments'][0]['speech'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, '7 chars/s'):
                render_plan(data)

    def test_rounding_is_upward_less_than_point_one_not_a_fixed_buffer(self):
        data = data_sample(text='一二三四五六七八。', seconds=1.2)
        render_plan(data)
        for seconds in (1.1, 1.3):
            voice = data['segments'][0]['speech'][0]
            voice.update(estimated_seconds=seconds, end=voice['start'] + seconds)
            with self.assertRaisesRegex(ValueError, '7 chars/s'):
                render_plan(data)

    def test_numeric_reading_counts_actual_characters_and_keeps_original_digits(self):
        data = data_sample(text='付20元。', seconds=.6)
        voice = data['segments'][0]['speech'][0]
        voice['number_expansions'] = [dict(start=1, end=3, spoken='二十')]
        errors = []
        self.assertAlmostEqual(chinese_speech_seconds(voice, 'zh', 'test', errors), 4/7)
        self.assertEqual(errors, [])
        plan, text = render_plan(data)
        self.assertIn('“付20元。”', text)
        self.assertEqual(plan['segments'][0]['english_words'], 0)

    def test_missing_numeric_reading_or_change_to_other_words_fails(self):
        data = data_sample(text='付20元。', seconds=.6)
        with self.assertRaisesRegex(ValueError, 'expand spoken numbers'):
            render_plan(data)
        data['segments'][0]['speech'][0]['number_expansions'] = [dict(start=0, end=3, spoken='二十')]
        with self.assertRaisesRegex(ValueError, 'numeric expressions only'):
            render_plan(data)

    def test_mixed_chinese_and_english_are_counted_independently(self):
        data = data_sample(text='一二三四五六七Wait.', seconds=1.3)
        data['sources'][0].update(language='mul', language_exception_basis='合成原句明确中文后接英语Wait。')
        voice = data['segments'][0]['speech'][0]
        voice['language_parts'] = [dict(language='zh', text='一二三四五六七'), dict(language='en', text='Wait.')]
        errors = []
        self.assertAlmostEqual(chinese_speech_seconds(voice, 'mul', 'test', errors), 1 + 1/3.4)
        self.assertEqual(errors, [])
        plan, _ = render_plan(data)
        self.assertEqual(plan['segments'][0]['english_words'], 1)

    def test_portuguese_part_needs_its_own_estimate_not_chinese_rate(self):
        data = data_sample(text='一二三四五六七Por quê?', seconds=2.5)
        data['sources'][0].update(language='mul', language_exception_basis='合成原句明确中文后接葡语。')
        voice = data['segments'][0]['speech'][0]
        voice['language_parts'] = [dict(language='zh', text='一二三四五六七'), dict(language='pt', text='Por quê?')]
        with self.assertRaisesRegex(ValueError, 'pt part needs its own'):
            render_plan(data)
        voice['language_parts'][1]['estimated_seconds'] = 1.5
        plan, _ = render_plan(data)
        self.assertEqual(plan['segments'][0]['speech'][0]['estimated_seconds'], 2.5)

    def test_real_pause_is_added_once_with_basis_and_not_inferred_from_punctuation(self):
        data = data_sample(seconds=2.4)
        voice = data['segments'][0]['speech'][0]
        with self.assertRaisesRegex(ValueError, '7 chars/s'):
            render_plan(data)
        voice['chinese_timing'] = dict(pause_seconds=.4, basis='合成原文在第二分句前明确迟疑0.4秒。')
        render_plan(data)
        voice['chinese_timing']['basis'] = ''
        with self.assertRaisesRegex(ValueError, 'source/performance basis'):
            render_plan(data)

    def test_measured_audio_uses_actual_duration_and_cannot_double_add_pause(self):
        data = data_sample(seconds=2.8)
        voice = data['segments'][0]['speech'][0]
        voice.update(measured_seconds=2.8, measurement_basis='合成计时记录，仅测试接口，不证明真实音频存在。')
        render_plan(data)
        voice['chinese_timing'] = dict(pause_seconds=.8, basis='合成停顿已在测量时长中。')
        with self.assertRaisesRegex(ValueError, 'already includes pauses'):
            render_plan(data)

    def test_existing_timing_review_audio_format_is_supported(self):
        data = data_sample(seconds=1.8)
        data['segments'][0]['speech'][0]['timing_review'] = dict(basis='measured_audio', seconds=1.8,
            source='合成音频引用，仅测试记录格式', measurement='模拟测量接口，不代表实际测量')
        render_plan(data)

    def test_internal_camera_cuts_do_not_restart_or_recalculate_speech(self):
        data = data_sample()
        data['segments'][0]['shots'][1]['frame_id'] = '分镜1'
        plan, text = render_plan(data)
        self.assertEqual(len(plan['segments'][0]['speech']), 1)
        self.assertEqual(plan['segments'][0]['speech'][0]['estimated_seconds'], 2)
        self.assertEqual(text.count('“' + data['sources'][0]['text'] + '”'), 1)

    def test_other_languages_and_silent_chinese_screen_text_are_unaffected(self):
        for language in ('en', 'es', 'pt'):
            data = language_sample(language)
            plan, _ = render_plan(data)
            self.assertEqual(plan['segments'][0]['speech'][0]['estimated_seconds'], 1.5)
        data = language_sample('zh')
        data['sources'] = []; data['segments'][0]['speech'] = []
        plan, text = render_plan(data)
        self.assertIn('“出口”', text)
        self.assertEqual(plan['segments'][0]['speech'], [])


if __name__ == '__main__':
    unittest.main()
