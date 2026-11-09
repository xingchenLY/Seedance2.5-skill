"""Interval union, scope and non-mutation checks; semantic decisions stay reviewed."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from timing_overlap_review import review_timing_overlap, print_timing_overlap_review


def plain_ranges(result):
    return [{k: v for k, v in g.items() if k != 'speech_candidates'}
            for g in result['uncovered_ranges']]

from test_modes import mode_sample
from test_delivery_checks import review
from render_prompt import render_plan
from delivery_checks import validate_delivery


def sample(voices=((0, 8),), cuts=(0, 3, 5, 10), mode='真人剧'):
    return dict(mode=mode, segments=[dict(id='S1', duration=cuts[-1],
                shots=[dict(start=a, end=b) for a, b in zip(cuts, cuts[1:])],
                speech=[dict(start=a, end=b, kind='dialogue') for a, b in voices])])


class TimingOverlapReviewTests(unittest.TestCase):
    def test_cutting_a_fully_covered_line_does_not_add_time(self):
        for cuts in ((0, 8), (0, 3, 5, 8)):
            data = sample(cuts=cuts)
            before = deepcopy(data)
            self.assertEqual(review_timing_overlap(data), [])
            self.assertEqual(data, before)

    def test_only_uncovered_tail_is_reported_not_whole_action_duration(self):
        result = review_timing_overlap(sample())[0]
        self.assertEqual(result['speech_covered_seconds'], 8)
        self.assertEqual(result['duration'], 10)
        self.assertEqual(plain_ranges(result), [dict(start=8, end=10, shot_starts=[5])])

    def test_following_original_line_can_cover_the_same_tail(self):
        self.assertEqual(review_timing_overlap(sample(voices=((0, 8), (8, 10)))), [])

    def test_overlapping_or_nested_speech_is_counted_once(self):
        result = review_timing_overlap(sample(voices=((0, 8), (2, 4), (6, 9))))[0]
        self.assertEqual(result['speech_covered_seconds'], 9)
        self.assertEqual(result['uncovered_ranges'][0]['start'], 9)

    def test_adjacent_speech_has_no_artificial_cut_gap(self):
        self.assertEqual(review_timing_overlap(sample(voices=((0, 3), (3, 5), (5, 10)))), [])

    def test_head_middle_tail_and_short_gaps_are_all_located(self):
        result = review_timing_overlap(sample(voices=((0.2, 3.9), (4.1, 9.8))))[0]
        self.assertEqual([(g['start'], g['end']) for g in result['uncovered_ranges']],
                         [(0, 0.2), (3.9, 4.1), (9.8, 10)])
        self.assertAlmostEqual(result['speech_covered_seconds'], 9.4)

    def test_one_gap_across_cuts_remains_one_interval(self):
        result = review_timing_overlap(sample(voices=((0, 2), (6, 10))))[0]
        self.assertEqual(plain_ranges(result), [dict(start=2, end=6, shot_starts=[0, 3, 5])])

    def test_cut_endpoint_does_not_include_the_finished_shot(self):
        result = review_timing_overlap(sample(voices=((0, 5),)))[0]
        self.assertEqual(result['uncovered_ranges'][0]['shot_starts'], [5])

    def test_four_modes_and_all_voice_kinds_use_authored_coverage(self):
        for mode in ('真人剧', '3D漫真人剧', '推文', '3D漫推文'):
            for kind in ('dialogue', 'VO', 'OS', 'narration', 'inner'):
                data = sample(mode=mode)
                data['segments'][0]['speech'][0]['kind'] = kind
                with self.subTest(mode=mode, kind=kind):
                    self.assertEqual(review_timing_overlap(data)[0]['speech_covered_seconds'], 8)

    def test_wholly_unvoiced_segment_is_located_without_inventing_voice(self):
        data = sample(voices=())
        data['segments'][0]['sound_events'] = [dict(start=0, end=10)]
        before = deepcopy(data)
        result = review_timing_overlap(data)[0]
        self.assertEqual(result['speech_covered_seconds'], 0)
        self.assertEqual(plain_ranges(result), [dict(start=0, end=10, shot_starts=[0, 3, 5])])
        self.assertEqual(data, before)

    def test_segment_local_times_do_not_overlap_across_segments(self):
        data = sample()
        second = deepcopy(data['segments'][0])
        second['id'] = 'S2'
        second['speech'][0].update(start=2, end=10)
        data['segments'].append(second)
        results = review_timing_overlap(data)
        self.assertEqual([r['segment_id'] for r in results], ['S1', 'S2'])
        self.assertEqual([(r['uncovered_ranges'][0]['start'], r['uncovered_ranges'][0]['end'])
                          for r in results], [(8, 10), (0, 2)])

    def test_report_does_not_copy_utterances_or_modify_records(self):
        data = sample()
        data['segments'][0]['speech'][0]['text'] = 'Never repeat this utterance in a visual cue.'
        before = deepcopy(data)
        output = io.StringIO()
        with redirect_stdout(output):
            print_timing_overlap_review(data)
        self.assertIn('[timing_overlap] S1', output.getvalue())
        self.assertNotIn(data['segments'][0]['speech'][0]['text'], output.getvalue())
        self.assertNotIn('FAIL', output.getvalue())
        self.assertEqual(data, before)

    def test_rendered_voice_shots_and_prompt_are_unchanged_in_four_modes(self):
        for mode in ('真人剧', '3D漫真人剧', '推文', '3D漫推文'):
            data = mode_sample(mode)
            before = deepcopy(data)
            plan, text = render_plan(data)
            snapshot = deepcopy(plan)
            review_timing_overlap(plan)
            with self.subTest(mode=mode):
                self.assertEqual(data, before)
                self.assertEqual(plan, snapshot)
                self.assertEqual(plan['segments'][0]['speech'], data['segments'][0]['speech'])
                self.assertEqual(plan['segments'][0]['duration'], data['segments'][0]['shots'][-1]['end'])
                self.assertEqual(validate_delivery(plan, text, require_review=False), [])
                self.assertNotIn('[timing_overlap]', text)

    def test_cli_review_survives_plan_and_final_checks_without_retiming(self):
        scripts = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, prompt, derived = root/'source.json', root/'final.txt', root/'plan.json'
            authored = mode_sample('真人剧')
            source.write_text(json.dumps(authored, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(scripts/'render_prompt.py'), str(source),
                                     '--output', str(prompt), '--plan-output', str(derived)],
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('[timing_overlap]', result.stdout)
            text = prompt.read_text(encoding='utf-8')
            plan = json.loads(derived.read_text(encoding='utf-8'))
            self.assertEqual(plan['segments'][0]['speech'], authored['segments'][0]['speech'])
            review(plan, text)  # Synthetic receipt tests plumbing, not real content decisions.
            derived.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            before = derived.read_bytes()
            for extra in ([], ['--final-prompt', str(prompt)]):
                checked = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(scripts/'validate_plan.py'),
                                          str(derived), *extra], capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn('scoped content-review records complete' if extra else '[timing_overlap]', checked.stdout)
            self.assertEqual(derived.read_bytes(), before)
            self.assertEqual(prompt.read_text(encoding='utf-8'), text)
            self.assertNotIn('REVIEW', text)

    def test_timing_only_preflight_does_not_require_shots_or_run_review(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'plan.json'
            path.write_text(json.dumps(dict(mode='真人剧', segments=[dict(id='S1', pre_round_duration=8,
                             timing_feasibility=dict(minimum_seconds=8, basis='完整原声需要8秒'))])), encoding='utf-8')
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8',
                                     str(Path(__file__).with_name('validate_plan.py')), str(path), '--timing-only'],
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn('[timing_overlap]', result.stdout)


if __name__ == '__main__':
    unittest.main()
