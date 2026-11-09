from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from shoulder_axis_review import review_shoulder_axis
from review_report import collect_review_report, print_review_report
from render_prompt import render_plan
from test_modes import mode_sample


def exchange(side='西'):
    shoulders = ('左', '右') if side == '西' else ('右', '左')
    shots = []
    for i, (front, subject, shoulder, end) in enumerate(zip(('甲', '乙'), ('乙', '甲'), shoulders, ('南', '北'))):
        screen = '右' if shoulder == '左' else '左'
        shots.append(dict(start=i*2, end=(i+1)*2, start_camera=f'C{i}', end_camera=f'C{i}',
            visible_characters=[front, subject],
            view=dict(space='办公室', position=f'{end}端{side}侧{front}{shoulder}肩后', direction=subject,
                      height='眼高', subject=subject, foreground=[front], background=f'{end}端对面墙'),
            relation=dict(mode='same_scene', characters=[front, subject], scene_characters=[front, subject],
                          placements=[dict(character=front, visibility='visible',
                                           placement=f'的{shoulder}肩背占画{screen}前景')]),
            camera_text='固定过肩', visual_text=f'{subject}继续讲话。'))
    return dict(segments=[dict(id='S1', episode='E1', duration=4, shots=shots)])


class ShoulderAxisReviewTests(unittest.TestCase):
    def test_both_valid_axis_sides(self):
        for side in ('西', '东'):
            self.assertEqual(review_shoulder_axis(exchange(side)), [])

    def test_camera_and_screen_same_side_is_located_not_rewritten(self):
        data = exchange()
        data['segments'][0]['shots'][0]['relation']['placements'][0]['placement'] = '的左肩背占画左前景'
        before = deepcopy(data)
        findings = review_shoulder_axis(data)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['kind'], 'shoulder_projection')
        self.assertEqual(findings[0]['shots'][0]['start'], 0)
        self.assertEqual(data, before)

    def test_anatomical_shoulder_conflict_is_located(self):
        data = exchange()
        data['segments'][0]['shots'][0]['relation']['placements'][0]['placement'] = '的右肩背占画右前景'
        self.assertEqual(review_shoulder_axis(data)[0]['kind'], 'shoulder_projection')

    def test_same_shoulder_reverse_is_found_even_when_each_projection_agrees(self):
        data = exchange()
        second = data['segments'][0]['shots'][1]
        second['view']['position'] = '北端东侧乙左肩后'
        second['relation']['placements'][0]['placement'] = '的左肩背占画右前景'
        findings = review_shoulder_axis(data)
        self.assertEqual([f['kind'] for f in findings], ['same_shoulder_reverse'])
        self.assertEqual([s['start'] for s in findings[0]['shots']], [0, 2])

    def test_cross_segment_reverse_is_not_reset(self):
        data = exchange()
        second = data['segments'][0]['shots'].pop()
        data['segments'][0]['duration'] = 2
        second.update(start=0, end=2)
        second['view']['position'] = '北端东侧乙左肩后'
        second['relation']['placements'][0]['placement'] = '的左肩背占画右前景'
        data['segments'].append(dict(id='S2', episode='E1', duration=2, shots=[second]))
        findings = review_shoulder_axis(data)
        self.assertEqual([s['segment_id'] for s in findings[0]['shots']], ['S1', 'S2'])
        data['segments'][1]['episode'] = 'E2'
        self.assertEqual(review_shoulder_axis(data), [])

    def test_explicit_playback_order_and_end_view_are_used(self):
        data = exchange()
        first, second = data['segments'][0]['shots']
        first['end_view'] = deepcopy(first['view'])
        first['end_view']['position'] = '南端东侧甲右肩后'
        findings = review_shoulder_axis(data)
        self.assertEqual(findings[0]['kind'], 'same_shoulder_reverse')
        data['playback'] = [dict(segment_id='S1', start=2, end=4), dict(segment_id='S1', start=0, end=2)]
        self.assertEqual(review_shoulder_axis(data), [])

    def test_different_space_is_not_a_reverse_pair(self):
        data = exchange()
        second = data['segments'][0]['shots'][1]
        second['view'].update(space='另一房间', position='北端东侧乙左肩后')
        second['relation']['placements'][0]['placement'] = '的左肩背占画右前景'
        self.assertEqual(review_shoulder_axis(data), [])

    def test_solo_phone_insert_and_unnamed_shoulder_not_inferred(self):
        for mode in ('solo', 'remote', 'insert', 'empty'):
            data = exchange()
            for shot in data['segments'][0]['shots']:
                shot['relation']['mode'] = mode
                shot['view']['foreground'] = []
            self.assertEqual(review_shoulder_axis(data), [])
        data = exchange()
        for shot in data['segments'][0]['shots']:
            shot['view']['position'] = '长桌侧面'
        self.assertEqual(review_shoulder_axis(data), [])

    def test_insert_breaks_automatic_pair_guess_but_does_not_claim_axis_pass(self):
        data = exchange()
        insert = deepcopy(data['segments'][0]['shots'][0])
        insert.update(start=2, end=3)
        insert['view'].update(subject=None, foreground=[], position='桌边')
        data['segments'][0]['shots'][1].update(start=3, end=5)
        data['segments'][0]['shots'].insert(1, insert)
        data['segments'][0]['duration'] = 5
        self.assertEqual(review_shoulder_axis(data), [])

    def test_report_runs_locator_once_and_indexes_both_sides(self):
        data = mode_sample('真人剧')
        first, second = data['segments'][0]['shots']
        # Keep each original screen placement; make both explicit shoulder labels
        # identical so this real rendered plan has a cross-shot candidate.
        second['view']['position'] = second['view']['position'].replace('左肩后', '右肩后')
        with patch('review_report.review_shoulder_axis', wraps=review_shoulder_axis) as locator:
            report = collect_review_report(data)
            self.assertEqual(locator.call_count, 1)
        item = next(i for i in report['items'] if i['finding'].get('kind') == 'same_shoulder_reverse')
        self.assertEqual(len(item['locations']), 2)
        self.assertEqual(sum(item['id'] in g['items'] for g in report['groups']), 2)

    def test_all_modes_keep_sources_and_rendered_prompt_unchanged_by_report(self):
        for mode in ('真人剧', '3D漫真人剧', '推文', '3D漫推文'):
            authored = mode_sample(mode)
            plan, text = render_plan(authored)
            before = deepcopy(plan)
            with redirect_stdout(io.StringIO()):
                print_review_report(plan)
            self.assertEqual(plan, before)
            self.assertEqual(render_plan(authored), (plan, text))

    def test_maintained_examples_have_no_detected_explicit_shoulder_conflicts(self):
        folder = Path(__file__).resolve().parent.parent / 'assets/evaluation'
        for path in sorted(folder.glob('*.json')):
            with self.subTest(case=path.name):
                data = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(review_shoulder_axis(data), [])


if __name__ == '__main__':
    unittest.main()
