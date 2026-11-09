"""Regression checks for audible triggers and their final visual instructions.

Semantic compatibility and estimated speech timing still need content review.
"""
from copy import deepcopy
import unittest

from dialogue_reactions import quote_spans, reaction_prose
from test_dialogue_reactions import long_dialogue, errors, WORDS, REACTION
from render_prompt import render_plan
from delivery_checks import validate_delivery
from mode_rules import MODES


def node_of(data):
    return data['segments'][0]['speech'][0]['dialogue_review']['reactions'][0]


class ReactionSyncTests(unittest.TestCase):
    def test_sentence_final_phrase_cannot_be_heard_at_speech_start(self):
        for phrase in ('what I wanted.', 'You used my name without asking me.'):
            data = long_dialogue()
            node_of(data)['trigger'].update(quote=phrase, heard_at=0)
            with self.subTest(phrase=phrase):
                self.assertTrue(any('after speech starts' in e for e in errors(data)))
                with self.assertRaises(ValueError):
                    render_plan(data)

    def test_nonzero_speech_start_is_also_an_exclusive_boundary(self):
        data = long_dialogue()
        data['segments'][0]['speech'][0]['start'] = 1
        node_of(data)['trigger']['heard_at'] = 1
        self.assertTrue(any('after speech starts' in e for e in errors(data)))

    def test_phrase_order_cannot_reverse_declared_arrival_times(self):
        for reverse in (False, True):
            data = long_dialogue()
            first = node_of(data)
            first['trigger']['heard_at'] = 3.8
            second = deepcopy(first)
            second['trigger'].update(quote='private', heard_at=2)
            nodes = [first, second]
            if reverse:
                nodes.reverse()
            data['segments'][0]['speech'][0]['dialogue_review']['reactions'] = nodes
            with self.subTest(reverse=reverse):
                self.assertTrue(any('phrase order' in e for e in errors(data)))

    def test_ordered_or_equal_arrival_is_not_a_fixed_pacing_formula(self):
        for arrival in (3.8, 4):
            data = long_dialogue()
            first = node_of(data)
            second = deepcopy(first)
            second['trigger'].update(quote='private', heard_at=arrival)
            data['segments'][0]['speech'][0]['dialogue_review']['reactions'].append(second)
            with self.subTest(arrival=arrival):
                self.assertEqual(errors(data), [])

    def test_repeated_phrase_is_not_silently_assigned_to_first_occurrence(self):
        data = long_dialogue()
        voice = data['segments'][0]['speech'][0]
        voice['text'] = 'You lied. You lied.'
        first = node_of(data)
        first['trigger'].update(quote='You lied.', heard_at=3.8)
        second = deepcopy(first)
        second['trigger'].update(quote='You lied. You lied.', heard_at=2)
        voice['dialogue_review']['reactions'].append(second)
        self.assertEqual(len(quote_spans('You lied.', voice['text'])), 2)
        self.assertEqual(errors(data), [])  # Ambiguous occurrences require content review.

    def test_accented_source_quotes_keep_exact_text_and_positions(self):
        for text, first, second in (
            ('No elegí a Ana; elegí a Lucía.', 'No elegí a Ana', 'elegí a Lucía'),
            ('Não escolhi Ana; escolhi Lúcia.', 'Não escolhi Ana', 'escolhi Lúcia'),
        ):
            self.assertEqual(len(quote_spans(first, text)), 1)
            self.assertLess(quote_spans(first, text)[0][1], quote_spans(second, text)[0][1])

    def test_overlapping_repeated_phrases_are_also_ambiguous(self):
        self.assertEqual(len(quote_spans('no no', 'no no no')), 2)

    def test_punctuation_does_not_create_a_different_information_endpoint(self):
        data = long_dialogue()
        first = node_of(data)
        second = deepcopy(first)
        second['trigger'].update(quote=first['trigger']['quote'].rstrip('.'), heard_at=4)
        data['segments'][0]['speech'][0]['dialogue_review']['reactions'].append(second)
        self.assertEqual(errors(data), [])

    def test_final_anchor_in_all_modes_preserves_original_voice_and_shots(self):
        for mode in MODES:
            data = long_dialogue(mode)
            before = deepcopy(data)
            plan, text = render_plan(data)
            node = node_of(plan)
            with self.subTest(mode=mode):
                self.assertEqual(data, before)
                self.assertEqual(plan['segments'][0]['speech'], before['segments'][0]['speech'])
                self.assertEqual(len(plan['segments'][0]['shots']), 3)
                self.assertNotIn(node['trigger']['quote'], reaction_prose(node))
                self.assertEqual(text.count(node['trigger']['quote']), 1)
                self.assertIn(REACTION, reaction_prose(node))
                self.assertIn(reaction_prose(node), text)
                self.assertEqual(text.count('“' + WORDS + '”'), 1)
                self.assertNotIn('heard_at', text)
                self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_removing_only_the_trigger_from_final_picture_fails(self):
        plan, text = render_plan(long_dialogue())
        node = node_of(plan)
        cue = 'Elias 听清未经许可的指责后，'
        broken = text.replace(cue, '')
        self.assertIn('原本勉强维持的笑容消失', broken)
        self.assertTrue(any('reception/performance is absent' in e
                            for e in validate_delivery(plan, broken, require_review=False)))

    def test_moving_trigger_to_segment_header_does_not_pass(self):
        plan, text = render_plan(long_dialogue())
        node = node_of(plan)
        cue = 'Elias 听清未经许可的指责后，'
        broken = text.replace(cue, '').replace('对话氛围：', '对话氛围：' + cue, 1)
        self.assertTrue(validate_delivery(plan, broken, require_review=False))

    def test_ordinary_performance_beat_without_hearing_trigger_is_unchanged(self):
        beat = dict(start=1, end=2, evidence='Mara 承接原有前倾，目光仍锁住 Elias')
        self.assertNotIn('承接已听清的原台词信息', reaction_prose(beat))
        self.assertIn(beat['evidence'], reaction_prose(beat))


if __name__ == '__main__':
    unittest.main()
