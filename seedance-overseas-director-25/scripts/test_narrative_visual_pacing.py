"""Narrated-picture review routing without claiming semantic or video validation."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from narrative_visual_pacing import review_narrative_visual_pacing, print_narrative_visual_review
from test_modes import mode_sample
from test_delivery_checks import review
from render_prompt import render_plan
from delivery_checks import validate_delivery


def minimal(mode='推文', kind='narration', seconds=3):
    return dict(mode=mode, segments=[dict(id='S1', speech=[dict(kind=kind, start=0, end=seconds)],
                                        shots=[dict(start=0, end=seconds, frame_id='分镜1')])])


class NarrativeVisualPacingTests(unittest.TestCase):
    def test_routes_only_narrative_modes_and_non_dialogue_story_voices(self):
        for mode in ('推文', '3D漫推文', '真人剧', '3D漫真人剧'):
            for kind in ('narration', 'inner', 'dialogue', 'VO', 'OS'):
                with self.subTest(mode=mode, kind=kind):
                    found = review_narrative_visual_pacing(minimal(mode, kind))
                    self.assertEqual(bool(found), mode in ('推文', '3D漫推文') and kind in ('narration', 'inner'))

    def test_short_and_long_shots_all_receive_review_without_auto_duration_verdict(self):
        for seconds in (1, 1.2, 2, 3, 4, 6, 6.1, 12):
            data = minimal(seconds=seconds)
            before = deepcopy(data)
            found = review_narrative_visual_pacing(data)
            with self.subTest(seconds=seconds):
                self.assertEqual(len(found), 1)
                self.assertEqual((found[0]['shot_start'], found[0]['shot_end']), (0, seconds))
                self.assertEqual(data, before)
                self.assertNotIn('too_long', found[0])

    def test_all_actual_shots_inside_one_formal_frame_are_reviewed(self):
        data = minimal(seconds=6)
        data['segments'][0]['shots'] = [dict(start=a, end=b, frame_id='分镜1')
                                      for a, b in ((0, 1), (1, 3), (3, 6))]
        found = review_narrative_visual_pacing(data)
        self.assertEqual([(f['shot_start'], f['shot_end']) for f in found], [(0, 1), (1, 3), (3, 6)])
        self.assertEqual([f['voice_ranges'][0]['speech_index'] for f in found], [0, 0, 0])

    def test_carry_across_formal_frames_is_not_a_new_voice(self):
        data = minimal(seconds=6)
        data['segments'][0]['shots'] = [dict(start=0, end=2, frame_id='分镜1'),
                                      dict(start=2, end=6, frame_id='分镜2')]
        found = review_narrative_visual_pacing(data)
        self.assertEqual(found[1]['voice_ranges'], [dict(speech_index=0, kind='narration', start=2, end=6)])
        self.assertEqual(len(data['segments'][0]['speech']), 1)

    def test_small_voice_overlap_still_routes_entire_shot_including_tail(self):
        data = minimal(seconds=5)
        data['segments'][0]['speech'][0].update(start=1, end=2)
        item = review_narrative_visual_pacing(data)[0]
        self.assertEqual((item['shot_start'], item['shot_end']), (0, 5))
        self.assertEqual((item['voice_ranges'][0]['start'], item['voice_ranges'][0]['end']), (1, 2))

    def test_finished_or_not_yet_started_voice_does_not_cover_another_shot(self):
        data = minimal(seconds=6)
        segment = data['segments'][0]
        segment['speech'] = [dict(kind='narration', start=2, end=4)]
        segment['shots'] = [dict(start=a, end=b) for a, b in ((0, 2), (2, 4), (4, 6))]
        self.assertEqual([f['shot_start'] for f in review_narrative_visual_pacing(data)], [2])

    def test_mixed_voice_shot_retains_each_kind_and_actual_intersection(self):
        data = minimal(seconds=6)
        data['segments'][0]['speech'] = [dict(kind='inner', start=3, end=7),
                                        dict(kind='narration', start=0, end=2),
                                        dict(kind='dialogue', start=2, end=3)]
        found = review_narrative_visual_pacing(data)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['voice_ranges'], [dict(speech_index=0, kind='inner', start=3, end=6),
                                                   dict(speech_index=1, kind='narration', start=0, end=2)])

    def test_normal_speed_label_and_mapped_evidence_do_not_exempt_review(self):
        data = minimal()
        segment = data['segments'][0]
        segment['shots'][0].update(visual_text='人物以正常速度抬手，之后保持原位。', static_spans=[])
        segment['narration_coverage'] = [dict(task='action', views=[dict(shot_start=0, evidence='人物抬手')])]
        self.assertEqual(len(review_narrative_visual_pacing(data)), 1)

    def test_original_language_does_not_change_review_scope(self):
        for words in ('I ended the stream.', 'Terminé la transmisión.', 'Encerrei a transmissão.'):
            data = minimal()
            data['segments'][0]['speech'][0]['text'] = words
            before = deepcopy(data)
            with self.subTest(words=words):
                self.assertEqual(len(review_narrative_visual_pacing(data)), 1)
                self.assertEqual(data, before)

    def test_zero_narration_and_sound_effects_do_not_create_narrative_coverage(self):
        data = minimal()
        data['segments'][0]['speech'] = []
        data['segments'][0]['sound_events'] = [dict(start=0, end=3)]
        self.assertEqual(review_narrative_visual_pacing(data), [])

    def test_diagnostics_group_segments_without_outputting_full_utterances(self):
        data = minimal()
        data['segments'][0]['speech'][0]['text'] = 'Do not copy this sentence into diagnostics.'
        second = deepcopy(data['segments'][0]); second['id'] = 'S2'
        data['segments'].append(second)
        before = deepcopy(data)
        output = io.StringIO()
        with redirect_stdout(output): print_narrative_visual_review(data)
        self.assertEqual(output.getvalue().count('[narrative_visual_pacing]'), 2)
        self.assertNotIn(data['segments'][0]['speech'][0]['text'], output.getvalue())
        self.assertEqual(data, before)

    def test_report_does_not_retime_shorten_or_add_to_rendered_plan(self):
        for mode in ('推文', '3D漫推文'):
            data = mode_sample(mode)
            before = deepcopy(data)
            plan, text = render_plan(data)
            snapshot = deepcopy(plan)
            self.assertTrue(review_narrative_visual_pacing(plan))
            self.assertEqual(plan, snapshot)
            self.assertEqual(data, before)
            self.assertEqual(plan['segments'][0]['speech'], data['segments'][0]['speech'])
            self.assertEqual(validate_delivery(plan, text, require_review=False), [])
            self.assertNotIn('REVIEW', text)

    def test_cli_render_plan_and_final_validation_surface_same_scope(self):
        from test_narrative_budget import budgeted_fixture
        scripts = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, prompt, derived = root/'source.json', root/'final.txt', root/'plan.json'
            data = budgeted_fixture(mode_sample('推文'),
                'Put it down and leave it exactly where it is right now, please.')
            source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(scripts/'render_prompt.py'), str(source),
                                     '--output', str(prompt), '--plan-output', str(derived)],
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('[narrative_visual_pacing]', result.stdout)
            text = prompt.read_text(encoding='utf-8')
            plan = json.loads(derived.read_text(encoding='utf-8'))
            review(plan, text)  # Synthetic fixture receipt, not real content certification.
            derived.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            for extra in ([], ['--final-prompt', str(prompt)]):
                checked = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(scripts/'validate_plan.py'),
                                          str(derived), *extra], capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn('scoped content-review records complete' if extra else '[narrative_visual_pacing]', checked.stdout)
            self.assertEqual(prompt.read_text(encoding='utf-8'), text)
            self.assertNotIn('REVIEW', text)


if __name__ == '__main__':
    unittest.main()
