"""Prop loss regressions: declaration, derivation, final binding and mode scope."""
from copy import deepcopy
import unittest

from prop_inventory import FIELD, prop_declaration, check_prop_binding
from test_modes import mode_sample
from test_delivery_checks import review
from render_prompt import render_plan
from delivery_checks import validate_delivery
from validate_plan import validate

MODES = ('真人剧', '3D漫真人剧', '推文', '3D漫推文')


def with_props(mode='真人剧'):
    data = mode_sample(mode)
    data['segments'][0]['props'] = ['Mara 右手手机', 'Elias 右手花束']
    data['segments'][0]['shots'][0]['visual_text'] += 'Mara 右手握着手机，Elias 右手握着花束。'
    return data


def remove_field(text):
    return '\n'.join(row for row in text.split('\n') if not row.startswith(FIELD + '：'))


class PropInventoryTests(unittest.TestCase):
    def test_declared_props_render_and_pass_final_in_every_mode(self):
        for mode in MODES:
            data = with_props(mode)
            before = deepcopy(data)
            plan, text = render_plan(data)
            review(plan, text)
            with self.subTest(mode=mode):
                self.assertEqual(data, before)
                self.assertEqual(text.count(FIELD + '：'), 1)
                self.assertIn(FIELD + '：Mara 右手手机、Elias 右手花束', text)
                self.assertEqual(validate(plan) + validate_delivery(plan, text), [])

    def test_only_prop_heading_is_added_and_no_timing_or_speech_changes(self):
        data = with_props()
        base = deepcopy(data)
        base['segments'][0].pop('props')
        old_plan, old_text = render_plan(base)
        plan, text = render_plan(data)
        self.assertEqual(remove_field(text), old_text)
        self.assertEqual(plan['sources'], old_plan['sources'])
        for key in ('speech', 'shots', 'duration'):
            self.assertEqual(plan['segments'][0][key], old_plan['segments'][0][key])

    def test_deleting_field_is_rejected_even_when_props_remain_in_shot(self):
        for mode in MODES:
            plan, text = render_plan(with_props(mode))
            changed = remove_field(text)
            self.assertIn('Mara 右手握着手机', changed)
            review(plan, changed)  # Synthetic hash refresh cannot bypass binding.
            with self.subTest(mode=mode):
                for required in (False, True):
                    errors = validate_delivery(plan, changed, require_review=required)
                    self.assertTrue(any('final 特殊道具 is missing' in e for e in errors), errors)

    def test_partial_loss_replacement_and_unrecorded_addition_are_rejected(self):
        for replacement in ('Mara 右手手机', 'Elias 右手花束', '桌上戒指',
                            'Mara 右手手机、Elias 右手花束、桌上戒指'):
            plan, text = render_plan(with_props())
            changed = text.replace('特殊道具：Mara 右手手机、Elias 右手花束', '特殊道具：' + replacement)
            with self.subTest(replacement=replacement):
                errors = validate_delivery(plan, changed, require_review=False)
                self.assertTrue(any('/props:' in e for e in errors), errors)

    def test_missing_declaration_is_not_silently_treated_as_no_props(self):
        for mode in MODES:
            data = with_props(mode)
            data['segments'][0].pop('props')
            plan, text = render_plan(data)
            review(plan, text)
            with self.subTest(mode=mode):
                self.assertNotIn(FIELD + '：', text)
                errors = validate_delivery(plan, text)
                self.assertTrue(any('explicit prop declaration' in e for e in errors), errors)

    def test_explicit_reviewed_absence_omits_field_and_passes(self):
        for mode in MODES:
            plan, text = render_plan(mode_sample(mode))
            review(plan, text)
            with self.subTest(mode=mode):
                self.assertEqual(plan['segments'][0]['props'], [])
                self.assertNotIn(FIELD + '：', text)
                self.assertEqual(validate_delivery(plan, text), [])

    def test_blank_legacy_field_is_not_an_explicit_absence(self):
        data = mode_sample('真人剧')
        segment = data['segments'][0]
        segment.pop('props')
        segment['presentation'][FIELD] = ''
        plan, text = render_plan(data)
        review(plan, text)
        self.assertTrue(any('explicit prop declaration' in e for e in validate_delivery(plan, text)))

    def test_legacy_nonempty_field_remains_compatible_and_protected(self):
        data = with_props()
        segment = data['segments'][0]
        segment.pop('props')
        segment['presentation'][FIELD] = 'Mara 右手手机、Elias 右手花束。'
        plan, text = render_plan(data)
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])
        self.assertTrue(any('/props:' in e for e in validate_delivery(plan, remove_field(text), require_review=False)))

    def test_conflicting_dual_sources_are_rejected_without_mutating_input(self):
        for items in ([], ['手机']):
            data = mode_sample('真人剧')
            data['segments'][0].update(props=items)
            data['segments'][0]['presentation'][FIELD] = '花束'
            before = deepcopy(data)
            with self.subTest(items=items), self.assertRaisesRegex(ValueError, 'disagree'):
                render_plan(data)
            self.assertEqual(data, before)

    def test_matching_dual_sources_are_allowed_but_need_not_be_written_twice(self):
        data = with_props()
        data['segments'][0]['presentation'][FIELD] = 'Mara 右手手机、Elias 右手花束'
        _, text = render_plan(data)
        self.assertEqual(text.count(FIELD + '：'), 1)

    def test_invalid_or_duplicate_inventory_is_rejected(self):
        for items in (None, {}, '手机', [None], [''], ['无'], ['无特殊道具'], ['none'],
                      ['手机\n音效：错误字段'], ['手机', ' 手机 ']):
            data = mode_sample('真人剧')
            data['segments'][0]['props'] = items
            with self.subTest(items=items), self.assertRaisesRegex(ValueError, '/props:'):
                render_plan(data)

    def test_unrecorded_prop_field_cannot_be_added_to_empty_inventory(self):
        segment = dict(id='S1', props=[], presentation={})
        errors = []
        check_prop_binding(segment, [(FIELD, '手机')], errors, required=True)
        self.assertTrue(errors)

    def test_prop_presence_is_segment_local_not_copied_between_segments(self):
        errors = []
        first = dict(id='S1', props=['手机'], presentation={})
        second = dict(id='S2', props=[], presentation={})
        check_prop_binding(first, [(FIELD, '手机')], errors, required=True)
        check_prop_binding(second, [], errors, required=True)
        self.assertEqual(errors, [])
        check_prop_binding(second, [(FIELD, '手机')], errors, required=True)
        self.assertTrue(errors)

    def test_dialogue_only_object_mentions_do_not_generate_props(self):
        data = mode_sample('真人剧')
        voice = data['segments'][0]['speech'][0]
        voice['text'] = data['sources'][0]['text'] = 'Remember the ring.'
        plan, text = render_plan(data)
        self.assertEqual(plan['segments'][0]['props'], [])
        self.assertNotIn(FIELD + '：', text)
        self.assertIn('Remember the ring.', text)

    def test_explicit_absence_and_unreviewed_missing_are_distinguishable(self):
        errors = []
        self.assertEqual(prop_declaration(dict(id='S1', props=[]), errors), (True, None))
        self.assertEqual(prop_declaration(dict(id='S2'), errors), (False, None))
        self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
