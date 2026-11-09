"""Narrative framing diagnostics preserve legitimate choices, speech and playback."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from narrative_framing import review_narrative_framing, print_framing_review
from test_narration_coverage import rich_plan
from test_delivery_checks import review
from render_prompt import render_plan
from delivery_checks import validate_delivery


def mini_plan(scales=('中景', '全景', '中全景'), mode='推文'):
    return dict(mode=mode, segments=[dict(id='A', episode='E1', duration=2 * len(scales),
        speech=[dict(kind='narration', start=0, end=2 * len(scales))],
        shots=[dict(start=i * 2, end=(i + 1) * 2, start_scale=scale, end_scale=scale,
                    frame_id='分镜1') for i, scale in enumerate(scales)])])


def runs(plan):
    return [f for f in review_narrative_framing(plan) if f['code'] == 'broad_narrative_run']


class NarrativeFramingTests(unittest.TestCase):
    def test_both_narrative_modes_flag_actual_broad_shots_even_in_one_formal_frame(self):
        for mode in ('推文', '3D漫推文'):
            with self.subTest(mode=mode):
                findings = runs(mini_plan(mode=mode))
                self.assertEqual(len(findings), 1)
                self.assertEqual([s['start'] for s in findings[0]['shots']], [0, 2, 4])

    def test_three_is_review_trigger_not_a_quota_for_shorter_sequences(self):
        self.assertEqual(runs(mini_plan(('全景', '中景'))), [])

    def test_close_information_breaks_broad_sequence(self):
        self.assertEqual(runs(mini_plan(('中景', '近景', '全景', '中全景'))), [])

    def test_cross_segment_sequence_is_not_reset_by_prompt_boundary(self):
        plan = mini_plan(('全景', '中景'))
        b = deepcopy(mini_plan(('中全景',))['segments'][0]); b['id'] = 'B'
        plan['segments'].append(b)
        self.assertEqual([s['segment_id'] for s in runs(plan)[0]['shots']], ['A', 'A', 'B'])

    def test_new_episode_starts_a_new_sequence(self):
        plan = mini_plan(('全景', '中景'))
        b = deepcopy(plan['segments'][0]); b.update(id='B', episode='E2')
        plan['segments'].append(b)
        self.assertEqual(runs(plan), [])

    def test_actual_playback_order_and_clip_selection_control_diagnostics(self):
        plan = mini_plan(('近景', '全景', '中景'))
        b = deepcopy(mini_plan(('中全景',))['segments'][0]); b['id'] = 'B'
        plan['segments'].append(b)
        plan['playback'] = [dict(segment_id='B', start=0, end=2),
                            dict(segment_id='A', start=2, end=6)]
        self.assertEqual([(s['segment_id'], s['start']) for s in runs(plan)[0]['shots']],
                         [('B', 0), ('A', 2), ('A', 4)])

    def test_inner_voice_gets_framing_review_without_narration_coverage(self):
        plan = mini_plan(); plan['segments'][0]['speech'][0]['kind'] = 'inner'
        self.assertEqual(len(runs(plan)), 1)

    def test_optional_absent_coverage_does_not_break_short_narrative_review(self):
        plan = mini_plan(('中景',))
        plan['segments'][0]['narration_coverage'] = None
        self.assertEqual(review_narrative_framing(plan), [])

    def test_dialogue_silence_and_vo_do_not_trigger_non_scene_sequence(self):
        for kind in ('dialogue', 'VO', 'OS', None):
            plan = mini_plan()
            if kind is None: plan['segments'][0]['speech'] = []
            else: plan['segments'][0]['speech'][0]['kind'] = kind
            with self.subTest(kind=kind): self.assertEqual(runs(plan), [])

    def test_drama_and_legacy_modes_do_not_gain_this_review(self):
        for mode in ('真人剧', '3D漫真人剧', None):
            with self.subTest(mode=mode):
                self.assertEqual(review_narrative_framing(mini_plan(mode=mode)), [])

    def test_touching_voice_endpoint_is_not_positive_narration_overlap(self):
        plan = mini_plan(); plan['segments'][0]['speech'][0]['end'] = 4
        self.assertEqual(runs(plan), [])
        plan['segments'][0]['speech'][0]['end'] = 4.2
        self.assertEqual(len(runs(plan)), 1)

    def test_camera_move_to_close_view_is_not_labelled_continuously_broad(self):
        plan = mini_plan(); plan['segments'][0]['shots'][1]['end_scale'] = '近景'
        self.assertEqual(runs(plan), [])

    def test_detail_and_reaction_review_bind_to_the_actual_referenced_shot(self):
        plan = mini_plan(('中景', '中全景'))
        segment = plan['segments'][0]
        segment['narration_coverage'] = [
            dict(source_id='N1', task='detail', focus='按钮文字',
                 views=[dict(shot_start=0, evidence='屏幕内End按钮可读')]),
            dict(source_id='N2', task='reaction', focus='接收后的表情',
                 views=[dict(shot_start=2, evidence='听者原有笑意收住')])]
        findings = review_narrative_framing(plan)
        self.assertEqual([(f['task'], f['shots'][0]['start']) for f in findings], [('detail', 0), ('reaction', 2)])
        segment['shots'][0].update(start_scale='道具特写', end_scale='道具特写')
        self.assertEqual([f['task'] for f in review_narrative_framing(plan)], ['reaction'])

    def test_medium_body_reaction_is_not_automatically_treated_as_distant_face(self):
        plan = mini_plan(('中景',))
        plan['segments'][0]['narration_coverage'] = [dict(source_id='N1', task='reaction',
            focus='身体退让', views=[dict(shot_start=0, evidence='她收回原本前倾的上身')])]
        self.assertEqual(review_narrative_framing(plan), [])

    def test_unplayed_task_evidence_does_not_create_a_finding(self):
        plan = mini_plan(('中景', '近景'))
        plan['segments'][0]['narration_coverage'] = [dict(source_id='N1', task='detail',
            focus='按钮', views=[dict(shot_start=0, evidence='按钮可读')])]
        plan['playback'] = [dict(segment_id='A', start=2, end=4)]
        self.assertEqual(review_narrative_framing(plan), [])

    def test_advisories_do_not_mutate_records_or_block_legitimate_spatial_sequences(self):
        data = rich_plan()
        for shot, scale, unit in zip(data['segments'][0]['shots'], ('中景', '全景', '中全景'),
                                     data['segments'][0]['narration_coverage']):
            shot.update(start_scale=scale, end_scale=scale)
            unit['task'] = 'relationship'
        plan, text = render_plan(data)
        before = deepcopy(plan)
        self.assertEqual(len(runs(plan)), 1)
        stream = io.StringIO()
        with redirect_stdout(stream): print_framing_review(plan)
        self.assertEqual(plan, before)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])
        self.assertEqual(plan['segments'][0]['speech'], data['segments'][0]['speech'])
        self.assertIn('broad_narrative_run', stream.getvalue())
        self.assertNotIn('REVIEW', text)
        self.assertNotIn('narrative_framing', text)

    def test_render_and_final_validation_clis_surface_review_without_changing_prompt(self):
        data = rich_plan('推文')
        for shot, scale in zip(data['segments'][0]['shots'], ('中景', '全景', '中全景')):
            shot.update(start_scale=scale, end_scale=scale)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, final, derived = root/'source.json', root/'final.txt', root/'plan.json'
            source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8',
                str(Path(__file__).with_name('render_prompt.py')), str(source), '--output', str(final),
                '--plan-output', str(derived)], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('REVIEW [broad_narrative_run]', result.stdout)
            plan, text = json.loads(derived.read_text(encoding='utf-8')), final.read_text(encoding='utf-8')
            self.assertNotIn('REVIEW', text)
            review(plan, text)
            derived.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8',
                str(Path(__file__).with_name('validate_plan.py')), str(derived), '--final-prompt', str(final)],
                capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(final.read_text(encoding='utf-8'), text)
            self.assertIn('scoped content-review records complete', result.stdout)
            self.assertNotIn('REVIEW:', result.stdout)


if __name__ == '__main__':
    unittest.main()
