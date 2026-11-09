"""Mode-specific final output checks, including changed text with a fresh receipt.

The review helper makes synthetic test receipts, never real content reviews.
"""
import re
import unittest

from mode_rules import MODES, check_narration_tone
from render_prompt import render_plan
from validate_plan import validate
from delivery_checks import validate_delivery
from test_modes import mode_sample
from test_delivery_checks import review


class FinalModeTests(unittest.TestCase):
    def check_changed(self, plan, text, changed, finding):
        self.assertNotEqual(text, changed, 'mutation must change final text')
        review(plan, changed)  # A fresh hash alone cannot certify mode consistency.
        findings = validate_delivery(plan, changed)
        self.assertTrue(any(finding in item for item in findings), findings)

    def test_final_visual_form_cannot_switch_to_opposite_mode(self):
        for mode, policy in MODES.items():
            plan, text = render_plan(mode_sample(mode))
            original = '风格化3D电影动画' if policy['animation'] else '真人写实'
            opposite = '真人实拍' if policy['animation'] else '3D电影动画'
            changed = re.sub(r'^画面规格：.*$', lambda m: m[0].replace(original, opposite), text, flags=re.M)
            with self.subTest(mode=mode):
                self.check_changed(plan, text, changed, 'final 画面规格 differs')
                self.assertTrue(any('incompatible' in e for e in validate_delivery(plan, changed)))

    def test_final_3d_style_must_match_selected_catalog_label(self):
        for mode in ('3D漫推文', '3D漫真人剧'):
            plan, text = render_plan(mode_sample(mode))
            with self.subTest(mode=mode):
                self.check_changed(plan, text, text.replace('风格标签：风格化都市3D', '风格标签：通用3D'),
                                   'selected style label')
                plan['style_id'] = 'generic-3d'
                self.assertTrue(any('selected style label' in e for e in validate(plan)))

    def test_final_mode_fields_stay_bound_after_recomputing_hash(self):
        for mode in MODES:
            for field in ('整体风格', '画面规格', '声音限制'):
                plan, text = render_plan(mode_sample(mode))
                changed = re.sub(r'^' + field + r'：(.*)$', lambda m: m[0] + '另加未经记录的要求。', text, flags=re.M)
                with self.subTest(mode=mode, field=field):
                    self.check_changed(plan, text, changed, f'final {field} differs')

    def test_final_narrator_cannot_gain_character_emotion(self):
        for mode in ('推文', '3D漫推文'):
            plan, text = render_plan(mode_sample(mode))
            for wrong in ('清晰平稳中性，但带愤怒和哽咽', '不平稳，不中性'):
                with self.subTest(mode=mode, wrong=wrong):
                    fragment = 'character emotion' if '愤怒' in wrong else 'neutral/steady'
                    self.check_changed(plan, text, text.replace('清晰平稳中性', wrong), fragment)

    def test_narrator_explicit_prohibitions_do_not_become_emotion(self):
        for mode in ('推文', '3D漫推文'):
            for performance in ('清晰平稳中性，不带愤怒或哽咽',
                                '中性，不随画面人物的愤怒起伏',
                                '平稳，不带明显哭腔、怒气和颤声'):
                authored = mode_sample(mode)
                authored['segments'][0]['speech'][0]['performance'] = performance
                with self.subTest(mode=mode, performance=performance):
                    plan, text = render_plan(authored)
                    review(plan, text)
                    self.assertEqual(validate(plan) + validate_delivery(plan, text), [])

    def test_negated_neutral_and_positive_emotion_are_still_rejected(self):
        for performance in ('不平稳', '不中性，也不平稳', '中性，不带愤怒，但带哭腔'):
            errors = []
            check_narration_tone(performance, 'test', errors)
            with self.subTest(performance=performance):
                self.assertTrue(errors)

    def test_drama_keeps_emotional_dialogue_vo_os(self):
        for mode in ('真人剧', '3D漫真人剧'):
            for kind in ('dialogue', 'VO', 'OS'):
                authored = mode_sample(mode)
                authored['sources'][0]['kind'] = authored['segments'][0]['speech'][0]['kind'] = kind
                authored['segments'][0]['speech'][0]['performance'] = '愤怒哽咽，句尾带哭腔'
                with self.subTest(mode=mode, kind=kind):
                    plan, text = render_plan(authored)
                    review(plan, text)
                    self.assertEqual(validate(plan) + validate_delivery(plan, text), [])

    def test_narrative_keeps_emotional_characters_without_inventing_narrator(self):
        for mode in ('推文', '3D漫推文'):
            for kind in ('dialogue', 'inner'):
                authored = mode_sample(mode)
                authored['sources'][0]['kind'] = authored['segments'][0]['speech'][0]['kind'] = kind
                authored['segments'][0]['speech'][0]['performance'] = '愤怒哽咽，句尾带哭腔'
                authored['narration_sources'] = []
                authored['narration_absence_basis'] = '本测试正文只有人物声音，无独立叙事旁白'
                with self.subTest(mode=mode, kind=kind):
                    plan, text = render_plan(authored)
                    review(plan, text)
                    self.assertEqual(validate(plan) + validate_delivery(plan, text), [])
                    self.assertNotIn('叙事旁白，', text)

    def test_final_voice_labels_follow_mode_instead_of_one_universal_rule(self):
        for mode, policy in MODES.items():
            plan, text = render_plan(mode_sample(mode))
            if policy['narrative']:
                changed = text.replace('旁白参考', 'VO 参考')
            else:
                changed = text.replace('台词参考', '旁白参考').replace('音色：（', '音色：（叙事旁白，中性，')
            with self.subTest(mode=mode):
                self.check_changed(plan, text, changed, 'incompatible with selected mode')

    def test_inner_cannot_be_silently_relabelled_as_narrator(self):
        for mode in ('推文', '3D漫推文'):
            authored = mode_sample(mode)
            authored['sources'][0]['kind'] = authored['segments'][0]['speech'][0]['kind'] = 'inner'
            authored['segments'][0]['speech'][0]['performance'] = '愤怒哽咽'
            authored['narration_sources'] = []
            authored['narration_absence_basis'] = '本测试只有人物内心声'
            plan, text = render_plan(authored)
            with self.subTest(mode=mode):
                self.check_changed(plan, text, text.replace('内心声，', '叙事旁白，'), 'character emotion')

    def test_negated_category_does_not_create_two_simultaneous_voice_kinds(self):
        for mode in ('推文', '3D漫推文'):
            authored = mode_sample(mode)
            authored['segments'][0]['speech'][0]['performance'] = '中性平稳，非内心声'
            with self.subTest(mode=mode):
                plan, text = render_plan(authored)
                review(plan, text)
                self.assertEqual(validate_delivery(plan, text), [])

    def test_source_words_are_not_misread_as_production_directions(self):
        for mode in MODES:
            authored = mode_sample(mode)
            # Chinese dialogue is not part of English word-count timing in this fixture.
            spoken = '他提到三维动画、真人实拍和愤怒哭腔。'
            authored['sources'][0].update(text=spoken, language='zh')
            authored['segments'][0]['speech'][0]['text'] = spoken
            authored['segments'][0]['speech'][0].update(estimated_seconds=2.3, end=2.5)
            if MODES[mode]['narrative']:
                authored['narration_sources'][0].update(original_text=spoken, source_language='zh')
            with self.subTest(mode=mode):
                plan, text = render_plan(authored)
                review(plan, text)
                self.assertEqual(validate(plan) + validate_delivery(plan, text), [])

    def test_visual_prohibitions_are_allowed_but_positive_conflicts_fail(self):
        for mode, policy in MODES.items():
            authored = mode_sample(mode)
            opposite = '真人实拍' if policy['animation'] else '3D电影动画'
            authored['segments'][0]['presentation']['整体风格'] += f'不采用{opposite}。'
            with self.subTest(mode=mode):
                plan, text = render_plan(authored)
                review(plan, text)
                self.assertEqual(validate_delivery(plan, text), [])
                authored['segments'][0]['presentation']['整体风格'] += f'另外采用{opposite}。'
                with self.assertRaisesRegex(ValueError, 'incompatible'):
                    render_plan(authored)


if __name__ == '__main__':
    unittest.main()
