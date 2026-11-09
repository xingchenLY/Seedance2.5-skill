"""Name edits retain untouched dialogue, raw narration, timing and style identity."""
from copy import deepcopy
from pathlib import Path
import unittest

from name_policy import adapted_name_text, check_source_name_adaptations
from mode_rules import MODES
from render_prompt import render_plan
from validate_plan import validate
from delivery_checks import validate_delivery
from validate_styles import load_catalog, resolve_style
from test_modes import mode_sample


def edited_source(before, old, new, kind='real_place', start=None):
    start = before.index(old) if start is None else start
    source = dict(id='S1', name_adaptation=dict(before_text=before, replacements=[dict(
        start=start, end=start+len(old), original=old, replacement=new,
        kind=kind, basis='Synthetic test: change only the identified name occurrence')]))
    source['text'] = adapted_name_text(source)
    return source


class NamePolicyTests(unittest.TestCase):
    def test_bounded_unicode_edits_keep_other_words_and_homographs(self):
        cases = [
            ('Me fui de París. ¿Vienes?', 'París', 'la ciudad', 'Me fui de la ciudad. ¿Vienes?'),
            ('Saí de Lisboa. Você vem?', 'Lisboa', 'casa', 'Saí de casa. Você vem?'),
            ('Jordan left Jordan today.', 'Jordan', 'the country', 'Jordan left the country today.'),
        ]
        for before, old, new, expected in cases:
            with self.subTest(before=before):
                source = edited_source(before, old, new, start=before.rindex(old))
                snapshot = deepcopy(source)
                errors=[]
                check_source_name_adaptations({'sources':[source]}, errors)
                self.assertEqual(errors, [])
                self.assertEqual(source['text'], expected)
                self.assertEqual(source, snapshot)

    def test_extra_rewrite_outside_name_is_rejected(self):
        source=edited_source('Paris still needs you.', 'Paris', 'Home')
        source['text']='Home no longer needs you.'
        errors=[]
        check_source_name_adaptations({'sources':[source]},errors)
        self.assertTrue(any('differs from the bounded' in e for e in errors))

    def test_invalid_ranges_and_records_are_rejected(self):
        original=edited_source('Paris still needs you.', 'Paris', 'Home')
        for update in ({'start':True}, {'start':-1}, {'end':999}, {'end':0},
                       {'original':'Rome'}, {'replacement':''}, {'replacement':'Paris'},
                       {'kind':'rewrite'}, {'basis':''}):
            with self.subTest(update=update):
                source=deepcopy(original)
                source['name_adaptation']['replacements'][0].update(update)
                with self.assertRaises(ValueError):
                    adapted_name_text(source)
        for record in (None, [], {}, {'before_text':'x','replacements':[]}):
            with self.subTest(record=record), self.assertRaises(ValueError):
                adapted_name_text({'name_adaptation':record})

    def test_multiple_occurrences_require_distinct_nonoverlapping_spans(self):
        source=edited_source('Paris, then Paris.', 'Paris', 'Home')
        second=deepcopy(source['name_adaptation']['replacements'][0])
        second.update(start=12,end=17)
        source['name_adaptation']['replacements'].append(second)
        self.assertEqual(adapted_name_text(source),'Home, then Home.')
        second.update(start=0,end=5)
        with self.assertRaises(ValueError):
            adapted_name_text(source)

    def test_two_brands_stay_distinct_and_other_information_is_unchanged(self):
        before='Apple owes Nike 50, not 15.'
        source=edited_source(before,'Apple','Company A',kind='brand')
        source['name_adaptation']['replacements'].append(dict(
            start=11,end=15,original='Nike',replacement='Company B',kind='brand',
            basis='Second company is a different entity'))
        self.assertEqual(adapted_name_text(source),'Company A owes Company B 50, not 15.')

    def test_speech_must_follow_the_prepared_source_after_a_name_edit(self):
        authored=mode_sample('真人剧')
        edit=edited_source('Paris still needs you.', 'Paris', 'Home')
        authored['sources'][0].update({k:v for k,v in edit.items() if k!='id'})
        authored['segments'][0]['speech'][0]['text']='Paris still needs you.'
        with self.assertRaises(ValueError):
            render_plan(authored)

    def test_four_mode_render_keeps_raw_source_and_emits_only_prepared_speech(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                authored=mode_sample(mode)
                source=authored['sources'][0]
                edit=edited_source('Paris still needs you.', 'Paris', 'Home')
                source.update({k:v for k,v in edit.items() if k!='id'})
                authored['segments'][0]['speech'][0]['text']=source['text']
                if MODES[mode]['narrative']:
                    authored['narration_sources'][0]['original_text']=edit['name_adaptation']['before_text']
                snapshot=deepcopy(authored)
                plan,text=render_plan(authored)
                self.assertEqual(validate(plan)+validate_delivery(plan,text,require_review=False),[])
                self.assertIn('Home still needs you.',text)
                self.assertNotIn('Paris',text)
                self.assertEqual(authored,snapshot)
                self.assertEqual(plan['segments'][0]['duration'],authored['segments'][0]['shots'][-1]['end'])

    def test_original_narration_inventory_cannot_be_replaced_with_adapted_text(self):
        for mode in ('推文','3D漫推文'):
            authored=mode_sample(mode)
            edit=edited_source('Paris still needs you.', 'Paris', 'Home')
            authored['sources'][0].update({k:v for k,v in edit.items() if k!='id'})
            authored['segments'][0]['speech'][0]['text']=edit['text']
            authored['narration_sources'][0]['original_text']=edit['text']
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError,'original narration inventory'):
                render_plan(authored)

    def test_unrecorded_same_language_narration_change_remains_rejected(self):
        authored=mode_sample('推文')
        authored['narration_sources'][0]['original_text']='Paris still needs you.'
        authored['sources'][0]['text']=authored['segments'][0]['speech'][0]['text']='Home still needs you.'
        with self.assertRaisesRegex(ValueError,'same-language narration changed'):
            render_plan(authored)

    def test_old_style_selection_uses_generic_output_label_without_changing_identity(self):
        catalog,index=load_catalog(Path(__file__).resolve().parents[1]/'styles/catalog.json')
        for alias in ('疯狂动物城','Zootopia','Zootropolis','zootopia','风格化都市3D'):
            with self.subTest(alias=alias):
                style=resolve_style(catalog,index,alias)
                self.assertEqual(style['id'],'zootopia')
                self.assertEqual(style['label'],'风格化都市3D')
        for mode in ('3D漫推文','3D漫真人剧'):
            plan,text=render_plan(mode_sample(mode))
            self.assertEqual(plan['style_id'],'zootopia')
            self.assertIn('风格标签：风格化都市3D',text)
            self.assertNotIn('疯狂动物城',text)


if __name__=='__main__':
    unittest.main()
