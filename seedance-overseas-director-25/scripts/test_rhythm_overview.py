from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from render_prompt import render_plan
from review_closure import plan_digest
from review_report import collect_review_report
from rhythm_overview import render_rhythm_overview
from test_render_prompt import authored_sample


def shot(start, end, scale='近景', person='甲'):
    return dict(start=start, end=end, frame_id='分镜1',
                start_scale=scale, end_scale=scale, start_camera='A', end_camera='A',
                view=dict(subject=person))


class RhythmOverviewTests(unittest.TestCase):
    def test_actual_cuts_stay_separate_inside_one_frame(self):
        data = dict(segments=[dict(id='s1', episode='e1',
                                  shots=[shot(0, 0.8), shot(0.8, 4, '中近景', '乙')])])
        result = render_rhythm_overview(data)
        self.assertIn('| 1（分镜1） | 0–0.8 / 0.8 | 近景 | 甲 |', result)
        self.assertIn('| 2（分镜1） | 0.8–4 / 3.2 | 中近景 | 乙 |', result)
        self.assertIn('合计：4秒', result)

    def test_intra_shot_change_is_one_row(self):
        item = shot(0, 3, '中近景')
        item.update(end_scale='近景', end_view=dict(subject='乙'), end_camera='B')
        result = render_rhythm_overview(dict(segments=[dict(id='s', episode='e', shots=[item])]))
        self.assertIn('中近景 → 近景 | 甲 → 乙 |', result)
        self.assertNotIn('| 2（', result)

    def test_null_and_missing_are_different_and_unproven_end_not_inferred(self):
        a, b, c = shot(0, 1, person=None), shot(1, 2), shot(2, 4)
        del b['view']
        c['end_camera'] = 'B'
        result = render_rhythm_overview(dict(segments=[dict(id='s', episode='e', shots=[a, b, c])]))
        self.assertIn('| 无主拍人物 |', result)
        self.assertIn('| 未记录 |', result)
        self.assertIn('| 甲 → 未记录 |', result)

    def test_generation_and_edit_orders_are_distinct(self):
        data = dict(segments=[dict(id='a', episode='e1', shots=[shot(0, 4)]),
                              dict(id='b', episode='e2', shots=[shot(0, 2)])],
                    playback=[dict(segment_id='b', start=0, end=2),
                              dict(segment_id='a', start=1, end=4),
                              dict(segment_id='b', start=0, end=2)])
        result = render_rhythm_overview(data)
        self.assertLess(result.index('e1 / a'), result.index('e2 / b'))
        self.assertIn('1. b：0–2秒\n2. a：1–4秒\n3. b：0–2秒', result)

    def test_internal_only_no_voice_copy_or_mutation_or_new_review_obligation(self):
        data, prompt = render_plan(authored_sample())
        original, digest = deepcopy(data), plan_digest(data)
        report = collect_review_report(data)
        result = render_rhythm_overview(data)
        self.assertNotIn(data['segments'][0]['speech'][0]['text'], result)
        self.assertEqual(data, original)
        self.assertEqual(plan_digest(data), digest)
        self.assertEqual(collect_review_report(data), report)
        self.assertEqual(render_plan(authored_sample()), (data, prompt))

    def test_overview_recomputed_when_shot_changes(self):
        data = dict(segments=[dict(id='s', episode='e', shots=[shot(0, 4)])])
        before = render_rhythm_overview(data)
        data['segments'][0]['shots'][0]['end'] = 5
        after = render_rhythm_overview(data)
        self.assertIn('合计：4秒', before)
        self.assertIn('合计：5秒', after)

    def test_labels_cannot_break_rows(self):
        data = dict(segments=[dict(id='s', episode='e', shots=[shot(0, 2, person='甲|乙\n丙')])])
        self.assertIn('| 甲&#124;乙 丙 |', render_rhythm_overview(data))

    def test_cli_overview_does_not_change_prompt_or_derived_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            authored, prompt, derived, overview = [root/name for name in ('authored.json', 'final.txt', 'plan.json', 'rhythm.md')]
            authored.write_text(json.dumps(dict(authored_sample(), mode='真人剧'), ensure_ascii=False), encoding='utf-8')
            command = [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).with_name('render_prompt.py')),
                       str(authored), '--output', str(prompt), '--plan-output', str(derived)]
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            before = {p: p.read_bytes() for p in (authored, prompt, derived)}
            result = subprocess.run(command + ['--rhythm-output', str(overview)], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('内部节奏总览', overview.read_text(encoding='utf-8'))
            self.assertEqual({p: p.read_bytes() for p in before}, before)
            for conflicting in (authored, prompt, derived):
                result = subprocess.run(command + ['--rhythm-output', str(conflicting)], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual({p: p.read_bytes() for p in before}, before)


if __name__ == '__main__':
    unittest.main()
