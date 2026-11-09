"""Regression checks: python -m unittest discover -s scripts -p test_validate_plan.py"""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from validate_plan import english_words, validate


def sample():
    return {
        'episodes': [{'id': 'current', 'complete': False}],
        'sources': [dict(id='L1', episode='current', speaker='Mara',
                         kind='dialogue', language='en', text='Put it down.')],
        'segments': [dict(id='S1', episode='current', duration=4, english_words=3,
                         shots=[dict(start=0, end=2), dict(start=2, end=4)],
                         speech=[dict(source_id='L1', speaker='Mara', kind='dialogue',
                                      start=0.2, end=1.7, estimated_seconds=1.5,
                                      text='Put it down.')])]
    }


class PlanTests(unittest.TestCase):
    def assertFinding(self, plan, expected):
        findings = validate(plan)
        self.assertTrue(any(expected in finding for finding in findings), findings)

    def test_valid_example(self):
        self.assertEqual(validate(sample()), [])

    def test_unrelated_prefix_four_second_boundary(self):
        for duration in (3.9, 4, 4.1):
            with self.subTest(duration=duration):
                plan = sample()
                segment = plan['segments'][0]
                segment.update(duration=duration, unrelated_prefix=True,
                               shots=[dict(start=0, end=duration)])
                if duration < 4:
                    self.assertFinding(plan, 'unrelated prefix duration is below minimum')
                else:
                    self.assertEqual(validate(plan), [])

    def test_unrelated_prefix_minimum_does_not_apply_to_other_short_segments(self):
        for explicit_false in (False, True):
            with self.subTest(explicit_false=explicit_false):
                plan = sample()
                segment = plan['segments'][0]
                segment.update(duration=3, shots=[dict(start=0, end=3)])
                if explicit_false:
                    segment['unrelated_prefix'] = False
                self.assertEqual(validate(plan), [])

    def test_unrelated_prefix_marker_requires_boolean(self):
        for value in ('true', 'false', 1, 0, None):
            with self.subTest(value=value):
                plan = sample()
                plan['segments'][0]['unrelated_prefix'] = value
                with self.assertRaisesRegex(ValueError, 'unrelated_prefix must be a boolean'):
                    validate(plan)

    def test_unrelated_prefix_cannot_inflate_declared_duration(self):
        plan = sample()
        plan['segments'][0].update(unrelated_prefix=True,
                                   shots=[dict(start=0, end=3.9)])
        self.assertFinding(plan, 'shot total does not match segment duration')

    def test_prefix_and_merged_remainder_keep_thirty_second_cap(self):
        for duration in (30, 30.1):
            with self.subTest(duration=duration):
                plan = sample()
                plan['segments'][0]['unrelated_prefix'] = True
                plan['segments'].append(dict(id='S2', episode='current', duration=duration,
                    english_words=0, shots=[dict(start=0, end=duration)], speech=[]))
                if duration == 30:
                    self.assertEqual(validate(plan), [])
                else:
                    self.assertFinding(plan, 'segment duration exceeds limit')

    def test_unrelated_prefix_minimum_override_requires_user_basis(self):
        plan = sample()
        plan['segments'][0].update(duration=3, unrelated_prefix=True,
                                   shots=[dict(start=0, end=3)])
        plan['limits'] = dict(unrelated_prefix_seconds=3)
        with self.assertRaises((KeyError, ValueError)):
            validate(plan)
        plan['limits_basis'] = 'User explicitly changed the unrelated prefix minimum to three seconds.'
        self.assertEqual(validate(plan), [])

    def test_word_count_contractions_hyphens_and_punctuation(self):
        self.assertEqual(english_words("Don't — stop. It's well-lit! ..."), 4)

    def test_cross_shot_speech_counted_once(self):
        plan = sample()
        plan['segments'][0]['speech'][0].update(start=0, end=4, estimated_seconds=4)
        self.assertEqual(validate(plan), [])

    def test_cross_segment_split_reconstructs_source(self):
        plan = sample()
        second = deepcopy(plan['segments'][0])
        second['id'] = 'S2'
        plan['segments'].append(second)
        plan['segments'][0]['speech'][0]['text'] = 'Put it'
        plan['segments'][0]['english_words'] = 2
        second['speech'][0]['text'] = 'down.'
        second['english_words'] = 1
        for segment in plan['segments']:
            segment.update(duration=2, shots=[dict(start=0, end=2)])
            segment['speech'][0].update(start=0, end=2, estimated_seconds=2)
        self.assertEqual(validate(plan), [])
        second['speech'][0]['text'] = 'it down.'
        second['english_words'] = 2
        self.assertFinding(plan, 'duplicated or incompletely reconstructed')

    def test_source_missing(self):
        plan = sample()
        plan['segments'][0]['speech'] = []
        plan['segments'][0]['english_words'] = 0
        self.assertFinding(plan, 'source speech missing')

    def test_full_utterance_duplicated(self):
        plan = sample()
        repeat = deepcopy(plan['segments'][0]['speech'][0])
        repeat.update(start=2, end=3.5)
        plan['segments'][0]['speech'].append(repeat)
        plan['segments'][0]['english_words'] = 6
        self.assertFinding(plan, 'duplicated or incompletely reconstructed')

    def test_source_exact_case_punctuation_and_wording(self):
        for altered in ('put it down.', 'Put it down!', 'Put that down.'):
            with self.subTest(altered=altered):
                plan = sample()
                plan['segments'][0]['speech'][0]['text'] = altered
                self.assertFinding(plan, 'source text changed')

    def test_layout_whitespace_is_ignored(self):
        plan = sample()
        plan['sources'][0]['text'] = 'Put\n it   down.'
        self.assertEqual(validate(plan), [])

    def test_speaker_and_voice_kind_are_bound(self):
        for field, value in [('speaker', 'Elias'), ('kind', 'OS')]:
            with self.subTest(field=field):
                plan = sample()
                plan['segments'][0]['speech'][0][field] = value
                self.assertFinding(plan, 'speaker or voice kind differs')

    def test_unknown_source(self):
        plan = sample()
        plan['segments'][0]['speech'][0]['source_id'] = 'unprovided'
        self.assertFinding(plan, 'unknown source')

    def two_lines(self):
        plan = sample()
        source = deepcopy(plan['sources'][0])
        source.update(id='L2', speaker='Elias', text='No.')
        plan['sources'].append(source)
        voice = deepcopy(plan['segments'][0]['speech'][0])
        voice.update(source_id='L2', speaker='Elias', text='No.', start=2, end=3,
                     estimated_seconds=1)
        plan['segments'][0]['speech'].append(voice)
        plan['segments'][0]['english_words'] = 4
        return plan

    def test_source_order(self):
        plan = self.two_lines()
        self.assertEqual(validate(plan), [])
        plan['sources'].reverse()
        self.assertFinding(plan, 'source order changed')

    def test_overlapping_dialogue_needs_original_basis(self):
        plan = self.two_lines()
        plan['segments'][0]['speech'][1].update(start=1, end=2)
        self.assertFinding(plan, 'overlapping speech')
        plan['segments'][0]['overlap_basis'] = 'Source scene explicitly says they speak over each other.'
        self.assertEqual(validate(plan), [])

    def test_speech_estimate_and_bounds(self):
        plan = sample()
        plan['segments'][0]['speech'][0]['estimated_seconds'] = 1.6
        self.assertFinding(plan, 'does not fit')
        plan['segments'][0]['speech'][0].update(start=3, end=4.5, estimated_seconds=1.5)
        self.assertFinding(plan, 'outside the segment')

    def test_shot_gaps_overlaps_and_totals(self):
        for start in (1.9, 2.1):
            with self.subTest(start=start):
                plan = sample()
                plan['segments'][0]['shots'][1]['start'] = start
                self.assertFinding(plan, 'gap, overlap')
        plan = sample()
        plan['segments'][0]['duration'] = 5
        self.assertFinding(plan, 'shot total')

    def test_parent_and_subshot_double_count_not_allowed(self):
        plan = sample()
        plan['segments'][0]['shots'].insert(0, dict(start=0, end=4))
        self.assertFinding(plan, 'gap, overlap')

    def test_time_precision_and_silent_minimum(self):
        plan = sample()
        plan['segments'][0]['shots'] = [dict(start=0, end=3.5), dict(start=3.5, end=4)]
        self.assertFinding(plan, 'actual shot is shorter')
        plan['segments'][0]['shots'] = [dict(start=0, end=2.01), dict(start=2.01, end=4)]
        self.assertFinding(plan, '0.1-second')

    def test_word_and_time_limits_are_independent(self):
        plan = sample()
        segment = plan['segments'][0]
        segment.update(duration=30, english_words=96)
        segment['shots'] = [dict(start=0, end=30)]
        phrase = ' '.join(['word'] * 96)
        plan['sources'][0]['text'] = phrase
        segment['speech'][0].update(text=phrase, start=0, end=30, estimated_seconds=29)
        self.assertEqual(validate(plan), [])
        plan['sources'][0]['text'] += ' extra'
        segment['speech'][0]['text'] += ' extra'
        segment['english_words'] = 97
        self.assertFinding(plan, 'word count exceeds limit')
        segment['duration'] = 31.8
        segment['shots'][0]['end'] = 31.8
        self.assertFinding(plan, 'duration exceeds limit')

    def test_declared_word_count_is_recomputed(self):
        plan = sample()
        plan['segments'][0]['english_words'] = 2
        self.assertFinding(plan, '!= actual 3')

    def test_non_english_speech_is_not_silent(self):
        plan = sample()
        plan['sources'][0].update(language='zh', kind='VO', speaker='Mara', text='门突然关上。')
        voice = plan['segments'][0]['speech'][0]
        voice.update(text='门突然关上。', kind='VO', speaker='Mara')
        plan['segments'][0]['english_words'] = 0
        self.assertEqual(validate(plan), [])
        voice['estimated_seconds'] = 3
        self.assertFinding(plan, 'does not fit')

    def test_numbers_expand_only_in_counting_form(self):
        plan = sample()
        plan['sources'][0]['text'] = 'Pay $20.'
        voice = plan['segments'][0]['speech'][0]
        voice['text'] = 'Pay $20.'
        self.assertFinding(plan, 'expand spoken numbers')
        voice['spoken_form'] = 'Pay twenty dollars.'
        voice['number_expansions'] = [dict(start=4, end=7, spoken='twenty dollars')]
        self.assertEqual(validate(plan), [])

    def test_each_complete_episode_is_checked(self):
        plan = sample()
        plan['limits'] = dict(episode_seconds=50)
        plan['limits_basis'] = 'Test user explicitly requests at least 50 seconds per complete episode.'
        plan['episodes'] = [dict(id='E1', complete=True), dict(id='E2', complete=True)]
        plan['sources'] = []
        plan['segments'] = []
        for eid, durations in [('E1', [25, 25]), ('E2', [25, 24])]:
            for index, duration in enumerate(durations):
                plan['segments'].append(dict(id=f'{eid}-{index}', episode=eid, duration=duration,
                    english_words=0, shots=[dict(start=0, end=duration)], speech=[]))
        findings = validate(plan)
        self.assertEqual(len(findings), 1)
        self.assertIn('E2: complete episode', findings[0])
        plan['segments'][-1].update(duration=25, shots=[dict(start=0, end=25)])
        self.assertEqual(validate(plan), [])

    def test_scope_missing_extra_mixed_and_interleaved(self):
        plan = sample()
        plan['episodes'].append(dict(id='E2', complete=False))
        self.assertFinding(plan, 'cover selected episodes')
        plan = sample()
        plan['segments'][0]['episode'] = 'E2'
        self.assertFinding(plan, 'outside selected episodes')
        self.assertFinding(plan, 'belongs to another episode')
        plan['episodes'].append(dict(id='E2', complete=False))
        first = deepcopy(plan['segments'][0])
        first.update(id='S0', episode='current')
        plan['segments'].insert(0, first)
        last = deepcopy(first)
        last['id'] = 'S2'
        plan['segments'].append(last)
        self.assertFinding(plan, 'without interleaving')

    def test_user_limit_override_requires_basis(self):
        plan = sample()
        plan['episodes'][0]['complete'] = True
        self.assertEqual(validate(plan), [])
        plan['limits'] = dict(episode_seconds=4)
        with self.assertRaises(KeyError):
            validate(plan)
        plan['limits_basis'] = 'User explicitly requested a four-second full episode.'
        self.assertEqual(validate(plan), [])

    def test_malformed_numbers_and_language(self):
        for value in (float('nan'), float('inf'), -1, True, '4'):
            with self.subTest(value=value):
                plan = sample()
                plan['segments'][0]['duration'] = value
                with self.assertRaises(ValueError):
                    validate(plan)
        plan = sample()
        plan['sources'][0]['language'] = 'English'
        with self.assertRaises(ValueError):
            validate(plan)
        plan['sources'][0]['language'] = 'en-US'
        self.assertEqual(validate(plan), [])

    def flashback_plan(self):
        plan = sample()
        memory_source = dict(id='L2', episode='current', speaker='Elias', kind='dialogue',
                             language='en', text='Stay here.')
        return_source = dict(id='L3', episode='current', speaker='Mara', kind='dialogue',
                             language='en', text='Now I know.')
        plan['sources'].extend([memory_source, return_source])
        plan['segments'][0]['speech'].append(dict(source_id='L3', speaker='Mara', kind='dialogue',
            start=2.2, end=3.7, estimated_seconds=1.5, text='Now I know.'))
        plan['segments'][0]['english_words'] = 6
        plan['segments'].append(dict(id='S2', episode='current', duration=2, english_words=2,
            shots=[dict(start=0, end=2)], speech=[dict(source_id='L2', speaker='Elias',
                kind='dialogue', start=0.2, end=1.2, estimated_seconds=1, text='Stay here.')]))
        plan['playback'] = [dict(segment_id='S1', start=0, end=2),
                            dict(segment_id='S2', start=0, end=2),
                            dict(segment_id='S1', start=2, end=4)]
        plan['events'] = [
            dict(id='A', episode='current', text='Present trigger.', segment_id='S1', start=0, end=2),
            dict(id='B', episode='current', text='Memory.', segment_id='S2', start=0, end=2),
            dict(id='C', episode='current', text='Present return.', segment_id='S1', start=2, end=4)]
        return plan

    def test_flashback_grouping_restores_original_order(self):
        self.assertEqual(validate(self.flashback_plan()), [])

    def test_flashback_preserves_vo_os_classification(self):
        for kind in ('VO', 'OS'):
            with self.subTest(kind=kind):
                plan = self.flashback_plan()
                plan['sources'][1]['kind'] = kind
                plan['segments'][1]['speech'][0]['kind'] = kind
                self.assertEqual(validate(plan), [])
                plan['segments'][1]['speech'][0]['kind'] = 'dialogue'
                self.assertFinding(plan, 'speaker or voice kind differs from source')

    def test_flashback_without_playback_mapping_fails(self):
        plan = self.flashback_plan()
        del plan['playback']
        self.assertFinding(plan, 'source order changed')

    def test_flashback_moved_to_end_changes_story_order(self):
        plan = self.flashback_plan()
        plan['playback'] = [dict(segment_id='S1', start=0, end=4),
                            dict(segment_id='S2', start=0, end=2)]
        self.assertFinding(plan, 'source order changed')

    def test_flashback_silent_memory_must_still_be_used(self):
        plan = self.flashback_plan()
        plan['sources'].pop(1)
        plan['segments'][1]['speech'] = []
        plan['segments'][1]['english_words'] = 0
        self.assertEqual(validate(plan), [])
        plan['playback'].pop(1)
        self.assertFinding(plan, 'S2: playback does not use the complete')

    def test_flashback_duplicate_range_is_rejected(self):
        plan = self.flashback_plan()
        plan['playback'].insert(1, deepcopy(plan['playback'][0]))
        self.assertFinding(plan, 'repeats or reverses generated material')

    def test_flashback_missing_return_is_rejected(self):
        plan = self.flashback_plan()
        plan['playback'].pop()
        self.assertFinding(plan, 'S1: playback does not use the complete')
        self.assertFinding(plan, 'L3: source speech missing')

    def test_flashback_cannot_cut_inside_speech_even_at_shot_boundary(self):
        plan = self.flashback_plan()
        plan['segments'][0]['shots'] = [dict(start=0, end=1), dict(start=1, end=4)]
        plan['playback'][0]['end'] = 1
        plan['playback'][2]['start'] = 1
        self.assertFinding(plan, 'cut splits a speech record')

    def test_flashback_cut_must_be_a_planned_shot_boundary(self):
        plan = self.flashback_plan()
        plan['playback'][0]['end'] = 1.9
        plan['playback'][2]['start'] = 1.9
        self.assertFinding(plan, 'cut must align with a shot boundary')

    def test_flashback_unknown_or_invalid_clip(self):
        plan = self.flashback_plan()
        plan['playback'][1]['segment_id'] = 'missing'
        self.assertFinding(plan, 'unknown generated segment')
        plan = self.flashback_plan()
        plan['playback'][0]['end'] = 5
        self.assertFinding(plan, 'playback range is outside')

    def test_multiple_flashbacks_with_one_related_present_segment(self):
        plan = self.flashback_plan()
        plan['sources'].extend([
            dict(id='L4', episode='current', speaker='Elias', kind='dialogue',
                 language='en', text="Don't return."),
            dict(id='L5', episode='current', speaker='Mara', kind='dialogue',
                 language='en', text='You warned me.')])
        present = plan['segments'][0]
        present.update(duration=6, english_words=9)
        present['shots'].append(dict(start=4, end=6))
        present['speech'].append(dict(source_id='L5', speaker='Mara', kind='dialogue',
            start=4.2, end=5.7, estimated_seconds=1.5, text='You warned me.'))
        memory = deepcopy(plan['segments'][1])
        memory['id'] = 'S3'
        memory['speech'][0].update(source_id='L4', text="Don't return.")
        plan['segments'].append(memory)
        plan['playback'].extend([dict(segment_id='S3', start=0, end=2),
                                 dict(segment_id='S1', start=4, end=6)])
        plan['events'].extend([
            dict(id='D', episode='current', text='Second memory.', segment_id='S3', start=0, end=2),
            dict(id='E', episode='current', text='Second return.', segment_id='S1', start=4, end=6)])
        self.assertEqual(validate(plan), [])

    def test_flashback_cannot_hide_oversize_generation_unit(self):
        plan = self.flashback_plan()
        plan['segments'][0].update(duration=32, shots=[dict(start=0, end=2), dict(start=2, end=32)])
        plan['playback'][2]['end'] = 32
        self.assertFinding(plan, 'segment duration exceeds limit')

    def test_flashback_episode_duration_counts_each_interval_once(self):
        plan = dict(episodes=[dict(id='E1', complete=True)], sources=[], segments=[
            dict(id='S1', episode='E1', duration=30, english_words=0, speech=[],
                 shots=[dict(start=0, end=15), dict(start=15, end=30)]),
            dict(id='S2', episode='E1', duration=20, english_words=0, speech=[],
                 shots=[dict(start=0, end=20)])], playback=[
            dict(segment_id='S1', start=0, end=15), dict(segment_id='S2', start=0, end=20),
             dict(segment_id='S1', start=15, end=30)], events=[
             dict(id='A', episode='E1', text='Present.', segment_id='S1', start=0, end=15),
             dict(id='B', episode='E1', text='Memory.', segment_id='S2', start=0, end=20),
             dict(id='C', episode='E1', text='Return.', segment_id='S1', start=15, end=30)])
        plan['limits'] = dict(episode_seconds=50)
        plan['limits_basis'] = 'Test user explicitly requests a 50-second minimum for this episode.'
        self.assertEqual(validate(plan), [])
        plan['segments'][0]['duration'] = 29
        plan['segments'][0]['shots'][1]['end'] = 29
        plan['playback'][2]['end'] = 29
        plan['events'][2]['end'] = 29
        findings = validate(plan)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn('complete episode is shorter', findings[0])

    def test_flashback_playback_cannot_interleave_episodes(self):
        plan = self.flashback_plan()
        plan['episodes'].append(dict(id='E2', complete=False))
        plan['segments'].append(dict(id='S3', episode='E2', duration=1, english_words=0,
                                    speech=[], shots=[dict(start=0, end=1)]))
        plan['playback'].insert(1, dict(segment_id='S3', start=0, end=1))
        self.assertFinding(plan, 'playback must cover selected episodes in order')

    def test_cli_exit_codes(self):
        script = Path(__file__).with_name('validate_plan.py')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'plan.json'
            for data, expected in [(json.dumps(dict(sample(), mode='真人剧')), 0), ('{}', 2), ('not json', 2)]:
                path.write_text(data, encoding='utf-8')
                result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script), str(path)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            plan = dict(sample(), mode='真人剧')
            plan['segments'][0]['english_words'] = 0
            path.write_text(json.dumps(plan), encoding='utf-8')
            result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script), str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)


class ReviewFixTests(unittest.TestCase):
    def assertFinding(self, plan, expected):
        findings = validate(plan)
        self.assertTrue(any(expected in finding for finding in findings), findings)

    def silent_memory(self):
        plan = PlanTests().flashback_plan()
        plan['sources'] = []
        for segment in plan['segments']:
            segment.update(speech=[], english_words=0)
        return plan

    def split_plan(self, text, parts, language='zh'):
        plan = sample()
        plan['sources'][0].update(text=text, language=language)
        template = deepcopy(plan['segments'][0])
        plan['segments'] = []
        for index, part in enumerate(parts):
            segment = deepcopy(template)
            segment.update(id=f'S{index}', english_words=english_words(part) if language == 'en' else 0)
            segment.update(duration=2, shots=[dict(start=0, end=2)])
            segment['speech'][0].update(text=part, start=0, end=2, estimated_seconds=2)
            plan['segments'].append(segment)
        return plan

    def test_silent_story_with_correct_insertion_passes(self):
        self.assertEqual(validate(self.silent_memory()), [])

    def test_silent_memory_moved_to_end_is_rejected(self):
        plan = self.silent_memory()
        plan['playback'] = [dict(segment_id='S1', start=0, end=4),
                            dict(segment_id='S2', start=0, end=2)]
        self.assertFinding(plan, 'event order changed')

    def test_silent_memory_missing_mapping_is_rejected(self):
        plan = self.silent_memory()
        del plan['playback']
        self.assertFinding(plan, 'event order changed')

    def test_explicit_playback_requires_source_story_units(self):
        for events in (None, []):
            with self.subTest(events=events):
                plan = self.silent_memory()
                if events is None:
                    del plan['events']
                else:
                    plan['events'] = events
                self.assertFinding(plan, 'source story units are required')

    def test_events_require_present_trigger_and_return_coverage(self):
        plan = self.silent_memory()
        plan['events'] = plan['events'][1:2]
        self.assertFinding(plan, 'S1: event coverage must include the complete')
        plan = self.silent_memory()
        plan['events'].pop()
        self.assertFinding(plan, 'S1: event coverage must include the complete')

    def test_event_gap_and_overlap_rejected(self):
        for end in (1.9, 2.1):
            with self.subTest(end=end):
                plan = self.silent_memory()
                plan['events'][0]['end'] = end
                self.assertFinding(plan, 'event coverage has a gap or overlap')

    def test_event_segment_episode_and_time_are_checked(self):
        for key, value, finding in [('segment_id', 'unknown', 'unknown event segment'),
                                    ('episode', 'E2', 'event belongs to another episode'),
                                    ('end', 9, 'event is outside'),
                                    ('end', 0, 'event is outside')]:
            with self.subTest(key=key, value=value):
                plan = self.silent_memory()
                plan['events'][0][key] = value
                self.assertFinding(plan, finding)

    def test_duplicate_event_and_malformed_event_are_rejected(self):
        plan = self.silent_memory()
        plan['events'][1]['id'] = 'A'
        self.assertFinding(plan, 'duplicate event id')
        for value in (None, 'A', True):
            with self.subTest(value=value):
                plan = self.silent_memory()
                plan['events'] = [value]
                with self.assertRaises(ValueError):
                    validate(plan)

    def test_one_event_cannot_hide_an_inserted_memory(self):
        plan = self.silent_memory()
        plan['events'][0]['end'] = 4
        plan['events'].pop()
        self.assertFinding(plan, 'without splitting across playback clips')

    def test_nested_silent_memories_keep_their_return_levels(self):
        plan = dict(episodes=[dict(id='current', complete=False)], sources=[], segments=[], events=[], playback=[])
        for sid, duration in [('S1', 2), ('S2', 2), ('S3', 1)]:
            plan['segments'].append(dict(id=sid, episode='current', duration=duration, english_words=0,
                                        speech=[], shots=[dict(start=i, end=i+1) for i in range(duration)]))
        for index, (sid, start, end) in enumerate([('S1', 0, 1), ('S2', 0, 1), ('S3', 0, 1), ('S2', 1, 2), ('S1', 1, 2)]):
            plan['events'].append(dict(id=f'F{index}', episode='current', text=f'Source beat {index}.',
                                       segment_id=sid, start=start, end=end))
            plan['playback'].append(dict(segment_id=sid, start=start, end=end))
        self.assertEqual(validate(plan), [])
        plan['playback'][3], plan['playback'][4] = plan['playback'][4], plan['playback'][3]
        self.assertFinding(plan, 'event order changed')

    def test_linear_story_units_need_no_playback_mapping(self):
        plan = sample()
        plan['events'] = [dict(id='A', episode='current', text='Mara tells him to put it down.',
                               segment_id='S1', start=0, end=4)]
        self.assertEqual(validate(plan), [])

    def test_multiple_episodes_preserve_event_order(self):
        plan = dict(episodes=[dict(id='E1', complete=False), dict(id='E2', complete=False)],
                    sources=[], segments=[], events=[])
        for eid in ('E1', 'E2'):
            sid = eid + '-S1'
            plan['segments'].append(dict(id=sid, episode=eid, duration=2, english_words=0,
                                        speech=[], shots=[dict(start=0, end=2)]))
            plan['events'].append(dict(id=eid, episode=eid, text='Source event.',
                                       segment_id=sid, start=0, end=2))
        self.assertEqual(validate(plan), [])
        plan['events'].reverse()
        self.assertFinding(plan, 'event order changed')

    def test_chinese_split_preserves_original_join(self):
        self.assertEqual(validate(self.split_plan('门关上后，她才转身。', ['门关上后，', '她才转身。'])), [])

    def test_unspaced_text_and_punctuation_can_cross_parts(self):
        for text, parts, language in [('扉が閉まる。彼女は振り返る。', ['扉が閉まる。', '彼女は振り返る。'], 'ja'),
                                      ('Wait!', ['Wait', '!'], 'en'),
                                      ('先说 Stop，再转身。', ['先说 Stop，', '再转身。'], 'zh')]:
            with self.subTest(text=text):
                plan = self.split_plan(text, parts, language)
                if language == 'zh':
                    plan['sources'][0]['language_exception_basis'] = '合成原句明确中文中引用英语 Stop，保留原词。'
                    voice = plan['segments'][0]['speech'][0]
                    voice['language_parts'] = [dict(language='zh', text='先说'),
                                               dict(language='en', text='Stop'),
                                               dict(language='zh', text='，')]
                    plan['segments'][0]['english_words'] = 1
                self.assertEqual(validate(plan), [])

    def test_chinese_missing_repeated_reordered_and_altered_parts_fail(self):
        for parts in [['门关上后，', '才转身。'], ['门关上后，', '后，她才转身。'],
                      ['她才转身。', '门关上后，'], ['门关上后，', '他才转身。'],
                      ['门关上后。', '她才转身。']]:
            with self.subTest(parts=parts):
                self.assertFinding(self.split_plan('门关上后，她才转身。', parts), 'source text changed')

    def test_english_word_spaces_are_preserved(self):
        self.assertEqual(validate(self.split_plan('Put it down.', ['Put it', 'down.'], 'en')), [])
        self.assertEqual(validate(self.split_plan('Put\n it   down.', ['Put', 'it down.'], 'en')), [])
        self.assertFinding(self.split_plan('Put it down.', ['Putit', 'down.'], 'en'), 'source text changed')
        self.assertFinding(self.split_plan('Put it down.', ['Put it', 'it down.'], 'en'), 'source text changed')



if __name__ == '__main__':
    unittest.main()
