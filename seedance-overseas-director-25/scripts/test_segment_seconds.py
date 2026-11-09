from copy import deepcopy
import unittest

from delivery_checks import validate_delivery
from render_prompt import render_plan
from shot_pacing import check_segment_duration
from test_delivery_checks import review
from test_shot_pacing import fixture
from test_validate_plan import sample
from validate_plan import validate


class WholeSegmentTests(unittest.TestCase):
    def test_truncation_not_rounding(self):
        for raw in (12, 12.1, 12.8, 12.9):
            errors=[]
            check_segment_duration(dict(duration=12,pre_round_duration=raw),'case',errors,required=True)
            self.assertEqual(errors, [])
            for wrong in (11,13):
                errors=[]
                check_segment_duration(dict(duration=wrong,pre_round_duration=raw),'case',errors,required=True)
                self.assertTrue(any('truncated pre_round_duration' in e for e in errors),errors)

    def test_bad_pre_round_estimate_rejected(self):
        for raw in (True,None,'8.8',0,.9,-1,float('inf'),float('nan')):
            errors=[]
            check_segment_duration(dict(duration=8,pre_round_duration=raw),'case',errors,required=True)
            self.assertTrue(errors)

    def test_floor_checked_even_in_basic_records(self):
        plan=sample()
        plan['segments'][0]['pre_round_duration']=3.8
        self.assertTrue(any('truncated pre_round_duration' in e for e in validate(plan)))

    def test_prepared_integer_timeline_renders_without_extra_time(self):
        authored=fixture('12-long-dialogue.json')
        authored['segments'][0]['pre_round_duration']=8.8
        original=deepcopy(authored)
        plan,text=render_plan(authored)
        self.assertEqual(authored,original)
        self.assertEqual(plan['segments'][0]['duration'],8)
        self.assertEqual(plan['segments'][0]['speech'],original['segments'][0]['speech'])
        self.assertIn('预估时长 8 秒',text)
        review(plan,text)
        self.assertEqual(validate(plan)+validate_delivery(plan,text),[])

    def test_fractional_final_timeline_is_not_just_relabelled(self):
        authored=fixture('12-long-dialogue.json')
        authored['segments'][0]['shots'][-1]['end']=8.8
        original=deepcopy(authored)
        with self.assertRaisesRegex(ValueError,'final segment duration must be an integer'):
            render_plan(authored)
        self.assertEqual(authored,original)

    def test_decimal_header_rejected_even_for_integer_value(self):
        plan,text=render_plan(fixture('12-long-dialogue.json'))
        text=text.replace('预估时长 8 秒','预估时长 8.0 秒')
        review(plan,text)
        self.assertTrue(any('without decimals' in e for e in validate_delivery(plan,text)))

    def test_changing_only_header_does_not_change_total(self):
        plan,text=render_plan(fixture('12-long-dialogue.json'))
        text=text.replace('预估时长 8 秒','预估时长 7 秒')
        review(plan,text)
        self.assertTrue(any('final duration or word count' in e for e in validate_delivery(plan,text)))

    def test_integer_internal_boundary_not_required(self):
        plan,text=render_plan(fixture('10-jump-cut.json'))
        self.assertEqual(plan['segments'][0]['shots'][0]['end'],1.5)
        self.assertEqual(plan['segments'][0]['duration'],3)
        review(plan,text)
        self.assertEqual(validate(plan)+validate_delivery(plan,text),[])

    def test_voice_cannot_overrun_new_integer_end(self):
        plan=sample()
        segment=plan['segments'][0]
        segment.update(duration=1,pre_round_duration=1.8,shots=[dict(start=0,end=1)])
        self.assertTrue(any('outside the segment' in e for e in validate(plan)))

    def test_integer_floor_does_not_waive_one_second_shots(self):
        authored=fixture('11-closeup-chain.json')
        authored['segments'][0]['shots'][0]['end']=2.1
        authored['segments'][0]['shots'][1]['start']=2.1
        with self.assertRaisesRegex(ValueError,'actual shot is shorter than 1 second'):
            render_plan(authored)

    def test_visible_staging_case_preserves_source_and_final_structure(self):
        authored=fixture('14-visible-staging.json')
        plan,text=render_plan(authored)
        self.assertEqual(len(plan['sources']),1)
        self.assertEqual(plan['sources'][0]['text'],'What?')
        self.assertEqual(plan['segments'][0]['duration'],5)
        self.assertEqual(text.count('“What?”'),1)
        review(plan,text)
        self.assertEqual(validate(plan)+validate_delivery(plan,text),[])


if __name__=='__main__':
    unittest.main()
