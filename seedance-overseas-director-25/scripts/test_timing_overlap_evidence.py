"""Observable review coverage, exact references, scope and non-retiming regressions."""
from copy import deepcopy
import unittest

from timing_overlap_review import review_timing_overlap, adjacent_speech
from timing_overlap_evidence import check_timing_overlap_evidence, required_overlap_gaps
from test_timing_overlap_review import sample
from test_dialogue_reactions import long_dialogue
from test_delivery_checks import review
from render_prompt import render_plan
from delivery_checks import validate_delivery


def checked_gap():
    data = sample(voices=((0, 4), (6, 10)))
    data['delivery_review'] = dict(timing_overlap=[dict(segment_id='S1', start=4, end=6,
        shot_start=3, task_evidence='Mara 将钥匙放入锁孔后转动，锁舌弹回', comparisons=[
            dict(segment_id='S1', speech_index=i, constraint='physical',
                 basis='合成剧情规定先停声辨认门外动静，随后开锁，锁舌弹回才向门外答话。')
            for i in range(2)])])
    content = {('S1', 3): ['Mara 将钥匙放入锁孔后转动，锁舌弹回。']}
    return data, content


class TimingOverlapEvidenceTests(unittest.TestCase):
    def failures(self, data, text):
        errors = []
        check_timing_overlap_evidence(data, text, errors)
        return errors

    def test_both_adjacent_original_voices_are_located_without_text(self):
        data, _ = checked_gap()
        candidates = required_overlap_gaps(data)[0]['speech_candidates']
        self.assertEqual([(c['speech_index'], c['direction']) for c in candidates], [(0, 'before'), (1, 'after')])
        self.assertTrue(all('text' not in c for c in candidates))

    def test_real_scoped_receipt_preserves_independent_time_and_input(self):
        data, content = checked_gap()
        before = deepcopy(data)
        self.assertEqual(self.failures(data, content), [])
        self.assertEqual(before, data)

    def test_omitted_gap_cannot_be_replaced_by_freeform_timing_note(self):
        data, content = checked_gap()
        data['delivery_review'] = dict(timing='需要完整呈现情绪变化')
        self.assertTrue(self.failures(data, content))

    def test_each_missing_or_stale_reference_is_rejected(self):
        mutations = [lambda row: row.update(start=3.9),
                     lambda row: row.update(shot_start=0),
                     lambda row: row.update(task_evidence='另一镜才出现的剧情'),
                     lambda row: row['comparisons'].pop(),
                     lambda row: row['comparisons'][0].update(speech_index=7),
                     lambda row: row['comparisons'][0].update(constraint='arbitrary'),
                     lambda row: row['comparisons'][0].update(basis='通过'),
                     lambda row: row['comparisons'][0].update(speech_index=True)]
        for index, mutate in enumerate(mutations):
            data, content = checked_gap()
            mutate(data['delivery_review']['timing_overlap'][0])
            with self.subTest(index=index):
                self.assertTrue(self.failures(data, content))

    def test_duplicate_or_malformed_records_fail_without_crashing(self):
        for value in (None, {}, [None], [dict(segment_id=[], start=0, end=2)]):
            data, content = checked_gap()
            data['delivery_review']['timing_overlap'] = value
            with self.subTest(value=value):
                self.assertTrue(self.failures(data, content))
        data, content = checked_gap()
        data['delivery_review']['timing_overlap'] *= 2
        self.assertTrue(self.failures(data, content))

    def test_generic_emotion_reason_cannot_approve_each_comparison(self):
        for reason in ('需要完整呈现情绪变化', '需要完整呈现情绪变化。',
                       '给反应留时间', '为了完整展示动作', 'Allow time for reaction.'):
            data, content = checked_gap()
            for comparison in data['delivery_review']['timing_overlap'][0]['comparisons']:
                comparison.update(constraint='performance', basis=reason)
            with self.subTest(reason=reason):
                self.assertTrue(self.failures(data, content))

    def test_concrete_constraint_can_include_performance_words(self):
        data, content = checked_gap()
        for comparison in data['delivery_review']['timing_overlap'][0]['comparisons']:
            comparison.update(constraint='performance',
                basis='需要完整呈现情绪变化：本段明确听完后失语，尝试起声失败后才收住呼吸，不能在失语阶段说出下一句。')
        self.assertEqual(self.failures(data, content), [])

    def test_short_mixed_gap_is_not_exempt_and_silence_has_no_fake_candidate(self):
        data = sample(voices=((0, 3.9), (4.1, 10)))
        self.assertEqual([(g['start'], g['end']) for g in required_overlap_gaps(data)], [(3.9, 4.1)])
        self.assertEqual(required_overlap_gaps(sample(voices=())), [])

    def test_other_modes_keep_existing_final_review_requirements(self):
        for mode in ('3D漫真人剧', '推文', '3D漫推文'):
            data = sample(mode=mode)
            self.assertEqual(required_overlap_gaps(data), [])
            self.assertEqual(self.failures(data, {}), [])

    def test_clip_order_not_segment_list_selects_cross_segment_window(self):
        data = sample(voices=((0, 8),))
        first = data['segments'][0]
        first['episode'] = 'E1'
        second = deepcopy(first)
        second.update(id='S2', speech=[dict(start=1, end=6, kind='dialogue')])
        data['segments'].append(second)
        candidates = adjacent_speech(data, first, 8, 10)
        self.assertEqual([(v['segment_id'], v['speech_index']) for v in candidates], [('S1', 0), ('S2', 0)])
        data['playback'] = [dict(segment_id='S2', start=0, end=10), dict(segment_id='S1', start=0, end=10)]
        self.assertEqual([v['segment_id'] for v in adjacent_speech(data, first, 8, 10)], ['S1'])

    def test_episode_boundary_and_silent_neighbor_do_not_get_skipped(self):
        data = sample()
        first = data['segments'][0]
        first['episode'] = 'E1'
        second = deepcopy(first); second.update(id='S2', episode='E2')
        third = deepcopy(first); third['id'] = 'S3'
        data['segments'] += [second, third]
        self.assertEqual([v['segment_id'] for v in adjacent_speech(data, first, 8, 10)], ['S1'])
        second.update(episode='E1', speech=[])
        self.assertEqual([v['segment_id'] for v in adjacent_speech(data, first, 8, 10)], ['S1'])

    def test_cropped_neighbor_does_not_supply_excluded_voice(self):
        data = sample()
        first = data['segments'][0]; first['episode'] = 'E1'
        second = deepcopy(first); second['id'] = 'S2'
        data['segments'].append(second)
        data['playback'] = [dict(segment_id='S1', start=0, end=10), dict(segment_id='S2', start=8, end=10)]
        self.assertEqual([v['segment_id'] for v in adjacent_speech(data, first, 8, 10)], ['S1'])

    def test_previous_late_reaction_example_needs_final_gap_review(self):
        data = long_dialogue()
        segment = data['segments'][0]
        tail = deepcopy(segment['shots'][1])
        tail.update(start=12, end=14, frame_id='分镜2')
        segment['shots'].append(tail)
        segment['duration'] = 14
        node = segment['speech'][0]['dialogue_review']['reactions'][0]
        node.update(shot_start=12, start=12, end=14, after_basis='需要完整呈现情绪变化')
        plan, text = render_plan(data)
        review(plan, text)
        del plan['delivery_review']['timing_overlap']
        errors = validate_delivery(plan, text)
        self.assertTrue(any('timing_overlap' in e for e in errors), errors)
        self.assertEqual(plan['segments'][0]['duration'], 14)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_coincident_speech_and_cuts_add_neither_gaps_nor_receipts(self):
        data = sample(voices=((0, 4), (4, 10)), cuts=(0, 2, 4, 5, 10))
        self.assertEqual(required_overlap_gaps(data), [])
        self.assertEqual(self.failures(data, {}), [])


if __name__ == '__main__':
    unittest.main()
