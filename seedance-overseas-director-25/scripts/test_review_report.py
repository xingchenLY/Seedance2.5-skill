"""The consolidated report must preserve every original finding and its scope."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import unittest
from unittest.mock import patch

import review_report
from narrative_framing import review_narrative_framing
from live_visual_pacing import review_live_visual_pacing
from narrative_visual_pacing import review_narrative_visual_pacing
from timing_overlap_review import review_timing_overlap
from test_narrative_framing import mini_plan
from test_modes import mode_sample
from render_prompt import render_plan
from delivery_checks import validate_delivery


def mixed_plan(mode='推文'):
    data = mini_plan(mode=mode)
    segment = data['segments'][0]
    segment['speech'][0]['end'] = 5
    if mode in ('真人剧', '3D漫真人剧'):
        segment['speech'][0]['kind'] = 'dialogue'
        segment['speech'][0]['end'] = 2
    segment['narration_coverage'] = [dict(source_id='N1', task='detail', focus='手机按钮',
        views=[dict(shot_start=0, evidence='结束按钮朝向镜头且可读')])]
    return data


class ReviewReportTests(unittest.TestCase):
    def test_each_original_locator_runs_once(self):
        names = ('review_narrative_framing', 'review_live_visual_pacing',
                 'review_narrative_visual_pacing', 'review_timing_overlap')
        with patch.object(review_report, names[0], wraps=getattr(review_report, names[0])) as a, \
             patch.object(review_report, names[1], wraps=getattr(review_report, names[1])) as b, \
             patch.object(review_report, names[2], wraps=getattr(review_report, names[2])) as c, \
             patch.object(review_report, names[3], wraps=getattr(review_report, names[3])) as d:
            with redirect_stdout(io.StringIO()):
                review_report.print_review_report(mixed_plan())
            for mock in (a, b, c, d):
                self.assertEqual(mock.call_count, 1)

    def test_all_raw_findings_survive_in_all_four_modes(self):
        functions = {'narrative_framing.py': review_narrative_framing,
                     'live_visual_pacing.py': review_live_visual_pacing,
                     'narrative_visual_pacing.py': review_narrative_visual_pacing,
                     'timing_overlap_review.py': review_timing_overlap}
        for mode in ('推文', '3D漫推文', '真人剧', '3D漫真人剧'):
            data = mixed_plan(mode)
            before = deepcopy(data)
            report = review_report.collect_review_report(data)
            for source, function in functions.items():
                with self.subTest(mode=mode, source=source):
                    self.assertEqual([i['finding'] for i in report['items'] if i['source'] == source],
                                     function(data))
            self.assertEqual(data, before)

    def test_same_shot_retains_separate_topics_and_independent_evidence(self):
        data = mixed_plan()
        second = deepcopy(data['segments'][0]['narration_coverage'][0])
        second.update(source_id='N2', focus='手机数字')
        second['views'][0]['evidence'] = '数字可以辨认'
        data['segments'][0]['narration_coverage'].append(second)
        report = review_report.collect_review_report(data)
        group = report['groups'][0]
        entries = [i for i in report['items'] if i['id'] in group['items']]
        details = [i for i in entries if i['code'] == 'narrative_task_readability']
        self.assertEqual(len(details), 2)
        self.assertEqual([i['finding']['source_id'] for i in details], ['N1', 'N2'])
        self.assertIn('narrative_visual_pacing', [i['code'] for i in entries])
        self.assertIn('broad_narrative_run', [i['code'] for i in entries])

    def test_every_item_is_indexed_at_all_its_shots(self):
        for mode in ('推文', '真人剧'):
            report = review_report.collect_review_report(mixed_plan(mode))
            for item in report['items']:
                locations = {(g['segment_id'], g['start']) for g in report['groups'] if item['id'] in g['items']}
                self.assertEqual(locations, set(item['locations']))
                self.assertTrue(locations)

    def test_cross_segment_playback_chain_keeps_original_order(self):
        data = mini_plan()
        second = deepcopy(data['segments'][0]); second['id'] = 'B'
        data['segments'].append(second)
        data['playback'] = [dict(segment_id='B', start=0, end=2), dict(segment_id='A', start=2, end=6)]
        item = next(i for i in review_report.collect_review_report(data)['items'] if i['code'] == 'broad_narrative_run')
        self.assertEqual([(s['segment_id'], s['start']) for s in item['finding']['shots']],
                         [('B', 0), ('A', 2), ('A', 4)])
        text = review_report.finding_text(item)
        self.assertLess(text.index('B@0'), text.index('A@2'))
        self.assertLess(text.index('A@2'), text.index('A@4'))

    def test_independent_episodes_are_not_merged(self):
        data = mini_plan(scales=('中景', '全景'))
        second = deepcopy(data['segments'][0]); second.update(id='B', episode='E2')
        data['segments'].append(second)
        report = review_report.collect_review_report(data)
        self.assertFalse(any(i['code'] == 'broad_narrative_run' for i in report['items']))
        self.assertEqual({g['segment_id'] for g in report['groups']}, {'A', 'B'})

    def test_mutating_collected_findings_cannot_mutate_the_plan(self):
        data = mixed_plan()
        before = deepcopy(data)
        report = review_report.collect_review_report(data)
        for item in report['items']:
            item['finding'].clear()
        self.assertEqual(data, before)

    def test_a_changed_plan_recomputes_findings_without_cached_receipts(self):
        data = mixed_plan()
        first = review_report.collect_review_report(data)
        data['segments'][0]['speech'][0]['end'] = 6
        second = review_report.collect_review_report(data)
        self.assertTrue(any(i['code'] == 'timing_overlap' for i in first['items']))
        self.assertFalse(any(i['code'] == 'timing_overlap' for i in second['items']))

    def test_empty_report_does_not_print_a_pass_claim(self):
        data = mini_plan(mode='3D漫真人剧')
        data['segments'][0]['speech'][0]['kind'] = 'dialogue'
        output = io.StringIO()
        with redirect_stdout(output): review_report.print_review_report(data)
        self.assertEqual(output.getvalue(), '')

    def test_printing_keeps_evidence_and_links_without_copying_utterance(self):
        data = mixed_plan()
        data['segments'][0]['speech'][0]['text'] = 'A complete original utterance stays in its voice record.'
        before = deepcopy(data)
        output = io.StringIO()
        with redirect_stdout(output): review_report.print_review_report(data)
        text = output.getvalue()
        self.assertIn('结束按钮朝向镜头且可读', text)
        self.assertIn('REVIEW [broad_narrative_run]', text)
        self.assertIn('REVIEW [narrative_visual_pacing]', text)
        self.assertIn('REVIEW [timing_overlap]', text)
        self.assertIn('关联：', text)
        self.assertIn('/B（跨镜）', text)
        self.assertNotIn(data['segments'][0]['speech'][0]['text'], text)
        self.assertEqual(data, before)

    def test_rendered_prompt_and_final_checks_stay_unchanged(self):
        for mode in ('推文', '3D漫推文', '真人剧', '3D漫真人剧'):
            authored = mode_sample(mode)
            plan, text = render_plan(authored)
            snapshot = deepcopy(plan)
            with redirect_stdout(io.StringIO()): review_report.print_review_report(plan)
            self.assertEqual(plan, snapshot)
            self.assertEqual(validate_delivery(plan, text, require_review=False), [])
            self.assertEqual(render_plan(authored), (plan, text))
            self.assertNotIn('REVIEW', text)


if __name__ == '__main__':
    unittest.main()
