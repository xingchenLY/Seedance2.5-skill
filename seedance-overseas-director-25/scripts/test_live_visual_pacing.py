"""Content review routing without fixed durations, new records or automatic edits."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from live_visual_pacing import review_live_visual_pacing, unvoiced_intervals, print_live_visual_review
from test_modes import mode_sample
from test_delivery_checks import review
from render_prompt import render_plan
from delivery_checks import validate_delivery


def pure_picture(seconds=2):
    """Deliberately ambiguous motion: structure alone must not certify its pacing."""
    data = mode_sample('真人剧')
    data['sources'] = []
    segment = data['segments'][0]
    segment['speech'] = []
    segment['presentation']['整体风格'] = 'AI真人写实，人物保持自然状态。'
    segment['presentation']['对话氛围'] = '两人无声对视，无人物发声。'
    segment['shots'] = segment['shots'][:1]
    shot = segment['shots'][0]
    shot.update(end=seconds, start_scale='中近景', end_scale='近景', end_camera='push-end',
                camera_text='摄影机沿原轴线同侧向 Mara 短距离推进，在近景落稳；人物站位不变。',
                visual_text='Mara 闭口看向 Elias，Elias 的肩背留在前景，两人站位不变。')
    shot['end_view'] = deepcopy(shot['view'])
    shot['end_view']['position'] += '向 Mara 推进约0.3米处'
    return data


class LiveVisualPacingTests(unittest.TestCase):
    def test_only_live_drama_is_in_scope(self):
        for mode in ('真人剧', '3D漫真人剧', '推文', '3D漫推文'):
            data = pure_picture()
            data['mode'] = mode
            with self.subTest(mode=mode):
                self.assertEqual(bool(review_live_visual_pacing(data)), mode == '真人剧')

    def test_short_unvoiced_shots_are_reviewed_without_a_three_second_floor(self):
        for seconds in (0.5, 1, 1.3, 2, 3):
            data = pure_picture(seconds)
            finding = review_live_visual_pacing(data)[0]
            with self.subTest(seconds=seconds):
                self.assertEqual((finding['start'], finding['end']), (0, seconds))
                self.assertTrue(finding['whole_shot'])
                self.assertFalse(finding['longer_review'])

    def test_same_picture_at_two_eight_thirty_seconds_all_requires_content_review(self):
        for seconds in (2, 8, 30):
            data = pure_picture(seconds)
            before = deepcopy(data)
            plan, text = render_plan(data)
            findings = review_live_visual_pacing(plan)
            with self.subTest(seconds=seconds):
                self.assertEqual(data, before)
                self.assertEqual(plan['segments'][0]['duration'], seconds)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0]['longer_review'], seconds > 3)
                self.assertEqual(validate_delivery(plan, text, require_review=False), [])
                self.assertNotIn('visual_timing', plan['segments'][0]['shots'][0])
                self.assertNotIn('REVIEW', text)

    def test_speech_vo_and_os_coverage_are_not_silent_pictures(self):
        for kind in ('dialogue', 'VO', 'OS'):
            data = pure_picture(8)
            data['segments'][0]['speech'] = [dict(start=0, end=8, kind=kind)]
            with self.subTest(kind=kind): self.assertEqual(review_live_visual_pacing(data), [])

    def test_voice_started_in_previous_frame_still_covers_current_picture(self):
        data = pure_picture(6)
        segment = data['segments'][0]
        first = segment['shots'][0]
        first['end'] = 2
        second = deepcopy(first)
        second.update(start=2, end=6, frame_id='分镜2')
        segment['shots'].append(second)
        segment['speech'] = [dict(start=0, end=6, kind='VO')]
        self.assertEqual(review_live_visual_pacing(data), [])

    def test_only_uncovered_intervals_of_mixed_shots_are_reported(self):
        data = pure_picture(12)
        data['segments'][0]['speech'] = [dict(start=4, end=7, kind='OS')]
        findings = review_live_visual_pacing(data)
        self.assertEqual([(f['start'], f['end']) for f in findings], [(0, 4), (7, 12)])
        self.assertTrue(all(not f['whole_shot'] and f['longer_review'] for f in findings))

    def test_brief_natural_gaps_in_voiced_shots_are_not_automatically_flagged(self):
        data = pure_picture(4)
        data['segments'][0]['speech'] = [dict(start=0.2, end=3.8, kind='dialogue')]
        self.assertEqual(review_live_visual_pacing(data), [])

    def test_union_of_overlapping_touching_and_unsorted_voice_ranges(self):
        segment = dict(speech=[dict(start=4, end=6), dict(start=1, end=3), dict(start=2, end=4), dict(start=5, end=7)])
        before = deepcopy(segment)
        self.assertEqual(unvoiced_intervals(segment, dict(start=0, end=8)), [(0, 1), (7, 8)])
        self.assertEqual(segment, before)

    def test_sound_effects_do_not_hide_an_unvoiced_shot(self):
        data = pure_picture()
        data['segments'][0]['sound_events'] = [dict(start=0, end=2, sound='脚步与房间环境声')]
        self.assertEqual(len(review_live_visual_pacing(data)), 1)

    def test_each_actual_shot_is_reviewed_inside_one_formal_frame(self):
        data = pure_picture()
        segment = data['segments'][0]
        second = deepcopy(segment['shots'][0])
        second.update(start=2, end=3)
        segment['shots'].append(second)
        findings = review_live_visual_pacing(data)
        self.assertEqual([(f['shot_start'], f['end']) for f in findings], [(0, 2), (2, 3)])

    def test_diagnostics_group_by_segment_without_mutating_records(self):
        data = pure_picture()
        second = deepcopy(data['segments'][0])
        second['id'] = 'S2'
        data['segments'].append(second)
        before = deepcopy(data)
        output = io.StringIO()
        with redirect_stdout(output): print_live_visual_review(data)
        self.assertEqual(data, before)
        self.assertEqual(output.getvalue().count('[live_visual_pacing]'), 2)

    def test_cli_render_and_final_validation_keep_prompt_and_exit_behavior(self):
        script_dir = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, prompt, derived = root/'source.json', root/'final.txt', root/'plan.json'
            source.write_text(json.dumps(dict(pure_picture(), content_language='en'), ensure_ascii=False), encoding='utf-8')
            rendered = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(script_dir/'render_prompt.py'), str(source),
                                       '--output', str(prompt), '--plan-output', str(derived)],
                                      capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertIn('[live_visual_pacing]', rendered.stdout)
            text = prompt.read_text(encoding='utf-8')
            self.assertNotIn('REVIEW', text)
            plan = json.loads(derived.read_text(encoding='utf-8'))
            review(plan, text)  # Synthetic fixture review, not a production approval.
            derived.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            checked = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(script_dir/'validate_plan.py'), str(derived),
                                      '--final-prompt', str(prompt)], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn('scoped content-review records complete', checked.stdout)
            self.assertNotIn('REVIEW:', checked.stdout)
            self.assertEqual(prompt.read_text(encoding='utf-8'), text)


if __name__ == '__main__':
    unittest.main()
