"""Keep subtle acting and sourced VFX; reject unreviewed color directions."""
from copy import deepcopy
import unittest

from appearance_review import check_appearance, findings
from delivery_checks import validate_delivery
from mode_rules import MODES
from render_prompt import render_plan
from test_delivery_checks import review
from test_modes import mode_sample


def permission(evidence, *, scope='shot:0', kind='source_vfx'):
    return dict(scope=scope, evidence=evidence, subject='Mara', kind=kind,
                source_ref='合成测试原稿第4场第2段',
                source_evidence='诅咒触发时，Mara的虹膜变为血红，皮肤保持原色。',
                basis='只呈现该人物虹膜的原文变化，不扩大到皮肤、眼白或发光。')


class AppearanceTests(unittest.TestCase):
    def test_previous_unreviewed_color_reproduction_is_blocked_in_all_modes(self):
        for mode in MODES:
            plan = mode_sample(mode)
            plan['segments'][0]['shots'][0]['visual_text'] += 'Mara脸色惨白，眼睛发红。'
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'facial appearance REVIEW'):
                render_plan(plan)

    def test_subtle_cheeks_and_eye_rims_survive_in_all_modes(self):
        for mode in MODES:
            plan = mode_sample(mode)
            plan['segments'][0]['shots'][0]['visual_text'] += 'Mara脸颊轻微泛红，眼眶微微发红；眼白不发红，虹膜不变色。'
            derived, text = render_plan(plan)
            review(derived, text)
            self.assertEqual(validate_delivery(derived, text), [])
            self.assertIn('眼眶微微发红', text)

    def test_subtle_adjective_cannot_cover_intensity_or_wrong_eye_part(self):
        for sentence in ('Mara眼眶明显发红。', 'Mara眼眶轻微但整圈鲜红。', 'Mara眼睛轻微发红。',
                         'Mara眼白微微泛红。', 'Mara虹膜淡淡泛红。', 'Mara眼眶通红。'):
            with self.subTest(sentence=sentence):
                self.assertTrue(list(findings(sentence)))

    def test_source_vfx_allowed_and_original_words_unchanged_in_all_modes(self):
        evidence = 'Mara的虹膜随诅咒触发变为血红'
        for mode in MODES:
            plan = mode_sample(mode)
            segment = plan['segments'][0]
            segment['shots'][0]['visual_text'] += evidence + '，其余面部保持原样。'
            segment['appearance_reviews'] = [permission(evidence)]
            derived, text = render_plan(plan)
            review(derived, text)
            self.assertEqual(validate_delivery(derived, text), [])
            self.assertIn(evidence, text)
            self.assertNotIn('source_evidence', text)
            self.assertIn('“Put it down.”', text)

    def test_vfx_genre_or_generic_source_is_not_permission(self):
        plan = mode_sample('真人剧')
        evidence = 'Mara脸色惨白'
        plan['segments'][0]['presentation']['整体风格'] += '魔法视效剧本。'
        plan['segments'][0]['shots'][0]['visual_text'] += evidence + '。'
        with self.assertRaisesRegex(ValueError, 'facial appearance REVIEW'):
            render_plan(plan)
        for key in ('source_ref', 'source_evidence', 'basis'):
            row = permission(evidence)
            row[key] = '原文有视效'
            plan['segments'][0]['appearance_reviews'] = [row]
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'concrete source/basis'):
                render_plan(plan)

    def test_source_state_and_existing_appearance_are_reviewable(self):
        cases = [
            ('source_state', 'Mara脸色苍白', '合成原稿明确该镜处于Mara受伤后脸色苍白的状态。'),
            ('existing_appearance', 'Mara虹膜呈现红色', '合成人物设定图在本次时点已明确保留红色虹膜，不是新变色。'),
            ('lighting', 'Mara脸部泛红', '合成场景设计的红色灯光照到Mara脸部，随灯光关闭消失，固有肤色未变。'),
            ('user_requested', 'Mara眼睛发光', '合成用户明确要求本镜Mara的眼睛随原有法术启动发光。'),
        ]
        for kind, evidence, source in cases:
            plan = mode_sample('真人剧')
            plan['segments'][0]['shots'][0]['visual_text'] += evidence + '。'
            row = permission(evidence, kind=kind)
            row['source_evidence'] = source
            row['basis'] = '合成测试只验证绑定结构，来源类别的真实性仍须实际内容复核。'
            plan['segments'][0]['appearance_reviews'] = [row]
            derived, text = render_plan(plan)
            review(derived, text)
            self.assertEqual(validate_delivery(derived, text), [])

    def test_review_does_not_leak_to_other_shots_or_new_changes(self):
        evidence = 'Mara的虹膜变为血红'
        plan = mode_sample('真人剧')
        segment = plan['segments'][0]
        segment['shots'][0]['visual_text'] += evidence + '。'
        segment['appearance_reviews'] = [permission(evidence)]
        render_plan(plan)
        moved = deepcopy(plan)
        moved['segments'][0]['shots'][1]['visual_text'] += evidence + '。'
        with self.assertRaisesRegex(ValueError, 'shot:2.*facial appearance REVIEW'):
            render_plan(moved)
        segment['shots'][0]['visual_text'] += 'Mara脸色惨白。'
        with self.assertRaisesRegex(ValueError, 'facial appearance REVIEW'):
            render_plan(plan)

    def test_wrong_scope_or_changed_evidence_does_not_pass(self):
        evidence = 'Mara的虹膜变为血红'
        for field, value in [('scope', 'shot:2'), ('evidence', 'Mara眼睛发光'), ('subject', 'Elias')]:
            plan = mode_sample('真人剧')
            segment = plan['segments'][0]
            segment['shots'][0]['visual_text'] += evidence + '。'
            row = permission(evidence)
            row[field] = value
            segment['appearance_reviews'] = [row]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'appearance evidence'):
                render_plan(plan)

    def test_actual_final_text_is_checked_even_with_fresh_review_hash(self):
        plan, text = render_plan(mode_sample('真人剧'))
        changed = text.replace('Mara 看向 Elias', 'Mara脸色惨白，Mara 看向 Elias', 1)
        self.assertNotEqual(changed, text)
        review(plan, changed)
        errors = validate_delivery(plan, changed)
        self.assertTrue(any('facial appearance REVIEW' in e for e in errors), errors)

    def test_prefix_and_voice_directions_are_checked(self):
        for target in ('prefix', 'voice'):
            plan = mode_sample('真人剧')
            segment = plan['segments'][0]
            if target == 'prefix':
                segment['presentation']['对话氛围'] += 'Mara脸色惨白。'
            else:
                segment['speech'][0]['performance'] += '，Mara眼睛发红'
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'facial appearance REVIEW'):
                render_plan(plan)

    def test_spoken_content_and_scene_identifier_not_rewritten(self):
        plan = mode_sample('真人剧')
        words = '脸色惨白，眼睛发红。'
        plan['sources'][0].update(text=words, language='zh')
        plan['segments'][0]['speech'][0]['text'] = words
        plan['segments'][0]['speech'][0].update(estimated_seconds=1.2, end=1.4)
        plan['segments'][0]['presentation']['场景'] = '脸色惨白_展厅'
        derived, text = render_plan(plan)
        review(derived, text)
        self.assertEqual(validate_delivery(derived, text), [])
        self.assertIn('“' + words + '”', text)
        self.assertIn('场景：脸色惨白_展厅', text)

    def test_negation_and_literal_carrier_text_are_distinguished_from_directions(self):
        # Literal-text language/readability is a separate source-review concern.
        self.assertFalse(list(findings('屏幕显示“脸色惨白”；不得让人物眼睛发红。')))
        self.assertTrue(list(findings('Mara呈现“脸色惨白”的状态。')))
        self.assertTrue(list(findings('Mara眼睛发红，屏幕显示“不要开门”。')))

    def test_invalid_review_shapes_do_not_crash(self):
        for rows in (None, {}, [None], [{'scope': 'shot:0'}]):
            errors = []
            check_appearance({'id': 'S1', 'appearance_reviews': rows}, {'shot:0': 'Mara脸色惨白。'}, errors)
            self.assertTrue(errors)


if __name__ == '__main__':
    unittest.main()
