"""Target-language routing, preservation and final-output regression checks."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from delivery_checks import validate_delivery
from language_policy import content_language, require_content_language
from mode_rules import MODES, presentation_defaults
from render_prompt import render_plan
from test_delivery_checks import review
from test_modes import mode_sample
from validate_plan import validate


def language_sample(language, mode='真人剧'):
    plan = mode_sample(mode)
    plan['content_language'] = language
    base = language.lower().split('-')[0]
    utterance, sign = {'zh': ('为什么？', '出口'), 'en': ('Why?', 'Exit'),
                       'es': ('¿Por qué?', 'Salida'),
                       'pt': ('Por quê?', 'Saída')}[base]
    plan['sources'][0].update(language=language, text=utterance)
    plan['segments'][0]['speech'][0]['text'] = utterance
    if base == 'zh':
        plan['segments'][0]['speech'][0].update(estimated_seconds=0.5, end=0.7)
    plan['segments'][0]['shots'][0]['visual_text'] += f'背景门牌清楚显示“{sign}”。'
    for source in plan.get('narration_sources', []):
        source.update(source_language=language, original_text=utterance)
    return plan


class LanguagePolicyTests(unittest.TestCase):
    def test_four_languages_render_in_all_modes_without_translating_text(self):
        for language, name in [('zh', '中文'), ('en', '英语'), ('es', '西班牙语'), ('pt', '葡萄牙语')]:
            for mode in MODES:
                with self.subTest(language=language, mode=mode):
                    authored = language_sample(language, mode)
                    plan, text = render_plan(authored)
                    review(plan, text)
                    self.assertEqual(validate(plan) + validate_delivery(plan, text), [])
                    self.assertIn(f'“{authored["sources"][0]["text"]}”', text)
                    sign = {'zh': '出口', 'en': 'Exit', 'es': 'Salida', 'pt': 'Saída'}[language]
                    self.assertIn(f'“{sign}”', text)
                    self.assertIn(f'原文发声统一使用{name}', text)
                    self.assertIn(f'剧情必要文字清晰可读，统一使用{name}', text)
                    self.assertNotIn('默认英文', text)
                    self.assertEqual(plan['segments'][0]['english_words'], 1 if language == 'en' else 0)
                    self.assertEqual(plan['segments'][0]['speech'][0]['estimated_seconds'], 0.5 if language == 'zh' else 1.5)

    def test_legacy_single_language_records_get_matching_screen_policy(self):
        for language in ('zh', 'en', 'es', 'pt'):
            plan = language_sample(language)
            del plan['content_language']
            derived, text = render_plan(plan)
            self.assertEqual(content_language(plan), language)
            self.assertNotIn('默认英文', text)
            review(derived, text)
            self.assertEqual(validate_delivery(derived, text), [])

    def test_english_is_inferred_from_speech_not_the_chinese_directions(self):
        authored = mode_sample('真人剧')
        _, old = render_plan(authored)
        self.assertIn('统一使用英语', old)
        self.assertEqual(content_language(authored), 'en')
        authored['content_language'] = 'en'
        plan, text = render_plan(authored)
        self.assertIn('统一使用英语', text)
        self.assertIn('“Put it down.”', text)
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])

    def test_mismatched_speech_language_cannot_pass_by_relabeling_target(self):
        for language in ('es', 'pt'):
            plan = mode_sample('真人剧')
            plan['content_language'] = language
            with self.subTest(language=language), self.assertRaisesRegex(ValueError, 'source language differs'):
                render_plan(plan)

    def test_custom_headers_cannot_override_language_back_to_english(self):
        for field in ('声音限制', '画面限制'):
            plan = language_sample('es')
            plan['segments'][0]['presentation'][field] = '默认英文。'
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'content_language'):
                render_plan(plan)
            plan['segments'][0]['presentation'][field] = presentation_defaults(plan)[field] + '默认英文。'
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'conflicting'):
                render_plan(plan)

    def test_final_header_tampering_fails_even_with_fresh_review_hash(self):
        for field in ('声音限制', '画面限制'):
            plan, text = render_plan(language_sample('pt'))
            rows = text.splitlines()
            rows = [row.replace('统一使用葡萄牙语', '默认英文') if row.startswith(field + '：') else row for row in rows]
            modified = '\n'.join(rows)
            review(plan, modified)
            findings = validate_delivery(plan, modified)
            self.assertTrue(any(f'final {field} differs' in e for e in findings), findings)

    def test_final_utterance_cannot_be_translated_with_fresh_review_hash(self):
        plan, text = render_plan(language_sample('es'))
        modified = text.replace('“¿Por qué?”', '“Why?”')
        review(plan, modified)
        findings = validate_delivery(plan, modified)
        self.assertTrue(any('final speech text' in e for e in findings), findings)

    def test_original_foreign_quote_keeps_mixed_language_counting(self):
        authored = language_sample('es')
        source = authored['sources'][0]
        source.update(language='mul', text='Hola. Wait.')
        voice = authored['segments'][0]['speech'][0]
        voice.update(text=source['text'], language_parts=[
            {'language': 'es', 'text': 'Hola.'}, {'language': 'en', 'text': 'Wait.'}])
        with self.assertRaisesRegex(ValueError, 'language_exception_basis'):
            render_plan(authored)
        source['language_exception_basis'] = '合成原稿L1明确先说西语Hola，随后引用英语Wait。'
        plan, text = render_plan(authored)
        review(plan, text)
        self.assertEqual(validate(plan) + validate_delivery(plan, text), [])
        self.assertEqual(plan['segments'][0]['english_words'], 1)
        voice['language_parts'][1]['text'] = 'Stop.'
        with self.assertRaisesRegex(ValueError, 'language_parts'):
            render_plan(authored)

    def test_silent_segments_keep_the_explicit_screen_language(self):
        for language, name in [('zh', '中文'), ('en', '英语'), ('es', '西班牙语'), ('pt', '葡萄牙语')]:
            authored = language_sample(language)
            authored['sources'] = []
            authored['segments'][0]['speech'] = []
            plan, text = render_plan(authored)
            self.assertIn(f'剧情必要文字清晰可读，统一使用{name}', text)
            self.assertNotIn('台词参考', text)
            review(plan, text)
            self.assertEqual(validate_delivery(plan, text), [])

    def test_language_parts_cannot_hide_a_different_language_under_spanish_tag(self):
        authored = language_sample('es')
        authored['sources'][0]['text'] = 'Hola. Wait.'
        authored['segments'][0]['speech'][0].update(text='Hola. Wait.', language_parts=[
            {'language': 'es', 'text': 'Hola.'}, {'language': 'en', 'text': 'Wait.'}])
        with self.assertRaisesRegex(ValueError, 'language_exception_basis'):
            render_plan(authored)

    def test_custom_prohibition_of_english_is_not_an_english_default(self):
        authored = language_sample('pt')
        authored['segments'][0]['presentation']['画面限制'] = presentation_defaults(authored)['画面限制'] + '不得默认使用英文。'
        plan, text = render_plan(authored)
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])

    def test_invalid_codes_and_conflicting_explicit_regions(self):
        for value in (None, '', [], {}, 'mul', '西班牙语', 'es\n画面限制：英语'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'content_language'):
                content_language({'content_language': value})
        plan = language_sample('pt-BR')
        _, text = render_plan(plan)
        self.assertIn('葡萄牙语（pt-br）', text)
        plan['sources'][0]['language'] = 'pt-PT'
        with self.assertRaisesRegex(ValueError, 'source language differs'):
            render_plan(plan)

    def test_chinese_target_rejects_old_blanket_chinese_bans(self):
        for prohibition in ('全画面任何位置均不得出现简体或繁体中文。', '禁止中文。', '无中文。'):
            data = language_sample('zh')
            data['segments'][0]['presentation']['画面限制'] = presentation_defaults(data)['画面限制'] + prohibition
            with self.subTest(prohibition=prohibition), self.assertRaisesRegex(ValueError, 'Chinese prohibition'):
                render_plan(data)

    def test_custom_policy_cannot_contradict_any_selected_language(self):
        for language, wrong in [('zh', '英语'), ('en', '中文'), ('es', '葡萄牙语'), ('pt', '西班牙语')]:
            data = language_sample(language)
            data['segments'][0]['presentation']['画面限制'] = presentation_defaults(data)['画面限制'] + f'统一使用{wrong}。'
            with self.subTest(language=language), self.assertRaisesRegex(ValueError, 'conflicting language-default'):
                render_plan(data)

    def test_chinese_project_can_prohibit_unwanted_captions_without_banning_all_text(self):
        data = language_sample('zh')
        data['segments'][0]['presentation']['画面限制'] = presentation_defaults(data)['画面限制'] + '禁止中文对照字幕，不添加中文水印。'
        plan, text = render_plan(data)
        self.assertIn('“出口”', text)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_chinese_visual_prohibition_cannot_be_added_only_to_final_text(self):
        plan, text = render_plan(language_sample('zh'))
        broken = text.replace('画面限制：', '画面限制：禁止中文。', 1)
        review(plan, broken)
        errors = validate_delivery(plan, broken)
        self.assertTrue(any('Chinese prohibition' in e or 'final 画面限制 differs' in e for e in errors), errors)

    def test_explicit_chinese_script_form_is_preserved(self):
        data = language_sample('zh-Hant')
        data['sources'][0]['text'] = data['segments'][0]['speech'][0]['text'] = '為什麼？'
        del data['content_language']
        self.assertEqual(require_content_language(data), 'zh-hant')
        plan, text = render_plan(data)
        self.assertIn('中文（zh-hant）', text)
        self.assertIn('“為什麼？”', text)
        self.assertNotIn('不得出现简体或繁体中文', text)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_mixed_chinese_english_preserves_actual_parts_and_needs_target(self):
        data = language_sample('zh')
        source = data['sources'][0]
        source.update(language='mul', text='别动。Wait.', language_exception_basis='原文明确中文命令后引用英语Wait。')
        data['segments'][0]['speech'][0].update(text=source['text'], language_parts=[
            dict(language='zh', text='别动。'), dict(language='en', text='Wait.')])
        data['segments'][0]['speech'][0].update(estimated_seconds=0.6, end=0.8)
        del data['content_language']
        with self.assertRaisesRegex(ValueError, 'explicit target'):
            require_content_language(data)
        data['content_language'] = 'zh'
        plan, text = render_plan(data)
        self.assertIn('“别动。Wait.”', text)
        self.assertEqual(plan['segments'][0]['english_words'], 1)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_public_silent_render_requires_a_chosen_language_instead_of_defaulting(self):
        data = language_sample('zh')
        data['sources'] = []; data['segments'][0]['speech'] = []
        del data['content_language']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, output, derived = root/'in.json', root/'out.txt', root/'derived.json'
            command = [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).with_name('render_prompt.py')),
                       str(path), '--output', str(output), '--plan-output', str(derived)]
            path.write_text(json.dumps(data), encoding='utf-8')
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 2)
            self.assertIn('explicit target', result.stderr)
            self.assertFalse(output.exists())
            data['content_language'] = 'zh'
            path.write_text(json.dumps(data), encoding='utf-8')
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('统一使用中文', output.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
