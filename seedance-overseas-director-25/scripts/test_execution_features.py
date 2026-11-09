from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from delivery_checks import validate_delivery, frame_groups
from render_prompt import render_plan
from shot_pacing import check_shot_pacing, check_segment_duration
from sound_timing import sound_context, frame_sound_prose
from speech_continuity import speech_carry_prose, speech_context
from test_delivery_checks import review
from validate_plan import validate, validate_timing_preflight


def fixture(name):
    return json.loads((Path(__file__).resolve().parent.parent/'assets/evaluation'/name).read_text(encoding='utf-8'))


def continuing_plan():
    event = dict(id='fan-a', kind='continuous', source='走廊通风机', text='发出低鸣',
                 start=0, end=2, basis_kind='scene', basis='已建立的运行中风机。',
                 sustain='电机仍在运行', continues=True, end_reason='片段末仍在运行')
    following = dict(event, id='fan-b', continuation_of='fan-a', continues=False, end_reason='停机后声音结束')
    return dict(segments=[dict(id='A', episode='E', duration=2, sound_events=[event]),
                          dict(id='B', episode='E', duration=2, sound_events=[following])])


class ExecutionFeatureTests(unittest.TestCase):
    def test_hidden_phone_content_rejected_during_render_and_final_check(self):
        authored = fixture('14-visible-staging.json')
        authored['segments'][0]['shots'][1]['visual_text'] += '屏幕清楚显示英文短信 Pay now。'
        with self.assertRaisesRegex(ValueError, 'back-facing phone'):
            render_plan(authored)
        plan, text = render_plan(fixture('14-visible-staging.json'))
        changed = text.replace('右手与手机位置不变。', '右手与手机位置不变。屏幕清楚显示英文短信 Pay now。')
        review(plan, changed)
        self.assertTrue(any('back-facing phone' in e for e in validate_delivery(plan, changed)))

    def test_oncamera_voice_cannot_claim_whole_shot_closed_mouth(self):
        authored = fixture('14-visible-staging.json')
        authored['segments'][0]['shots'][2]['visual_text'] = '林晚独自站在东端门边，整镜始终闭口，保持持机。'
        with self.assertRaisesRegex(ValueError, 'closed mouth'):
            render_plan(authored)
        authored['sources'][0]['kind'] = authored['segments'][0]['speech'][0]['kind'] = 'OS'
        self.assertEqual(validate(render_plan(authored)[0]), [])

    def test_listener_closed_mouth_does_not_silence_original_speaker(self):
        authored = fixture('08-continuous-reaction.json')
        authored['segments'][0]['shots'][1]['visual_text'] += 'Elias整镜始终闭口。'
        plan, text = render_plan(authored)
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])

    def test_phone_reflection_or_turn_requires_review_not_automatic_rejection(self):
        from content_lint import check_explicit_contradictions
        for visible in ('镜子反射中屏幕清楚显示英文短信。', '人物翻转手机朝向摄影机，屏幕清楚显示英文短信。'):
            errors=[]
            check_explicit_contradictions(dict(id='S',speech=[]),dict(start=0,end=2),
                                          '固定机位，手机背板朝向摄影机。',visible,errors)
            self.assertEqual(errors, [])

    def test_compact_camera_supports_old_prose_and_keeps_physical_binding(self):
        from camera_views import camera_view_prose
        plan,text=render_plan(fixture('08-continuous-reaction.json'))
        shot=plan['segments'][0]['shots'][0]
        compact=camera_view_prose(shot)
        legacy=camera_view_prose(shot,style='legacy')
        self.assertLess(len(compact),len(legacy))
        changed=text.replace(compact,legacy);review(plan,changed)
        self.assertEqual(validate_delivery(plan,changed), [])
        changed=text.replace(compact,compact.replace(shot['view']['position'],'错误位置'))
        review(plan,changed)
        self.assertTrue(any('camera view evidence' in e for e in validate_delivery(plan,changed)))

    def test_sound_cut_point_and_unfinished_portion(self):
        plan, text = render_plan(fixture('16-sound-events.json'))
        review(plan, text)
        self.assertEqual(validate(plan)+validate_delivery(plan, text), [])
        first, second = text.split('分镜2（')
        self.assertIn('第0.6至2秒', first)
        self.assertNotIn('敲击声', first)
        self.assertIn('第2秒，门板', second)
        self.assertIn('第2至3.2秒，承接走廊通风机', second)
        self.assertNotIn('第0.6', second)
        self.assertEqual(text.count('一次敲击声'), 1)

    def test_sound_inside_one_parent_frame_is_scoped_to_actual_shots(self):
        authored = fixture('16-sound-events.json')
        for shot in authored['segments'][0]['shots']:
            shot['frame_id'] = '分镜1'
        plan, text = render_plan(authored)
        self.assertEqual(text.count('音效：'), 1)
        self.assertIn('第0.6至2秒', text)
        self.assertIn('第2至3.2秒', text)
        self.assertEqual(validate(plan), [])

    def test_future_or_negative_effect_time_rejected(self):
        for time in (-1, 4, 7, True, float('nan')):
            authored = fixture('16-sound-events.json')
            authored['segments'][0]['sound_events'][1].update(start=time, end=time)
            with self.subTest(time=time), self.assertRaises(ValueError):
                render_plan(authored)

    def test_same_trigger_cannot_be_registered_twice(self):
        authored = fixture('16-sound-events.json')
        authored['segments'][0]['sound_events'].append(deepcopy(authored['segments'][0]['sound_events'][1]))
        with self.assertRaisesRegex(ValueError, 'duplicate sound id'):
            render_plan(authored)

    def test_legacy_freeform_sound_cannot_bypass_timing(self):
        authored = fixture('14-visible-staging.json')
        authored['segments'][0]['frame_notes'] = {'分镜1': {'sound_effects': '第7秒后镜门铃声。'}}
        with self.assertRaisesRegex(ValueError, 'sound_events'):
            render_plan(authored)

    def test_secondary_time_hidden_in_prose_rejected(self):
        authored = fixture('16-sound-events.json')
        authored['segments'][0]['sound_events'][1]['text'] = '第7秒响起门铃'
        with self.assertRaisesRegex(ValueError, 'extra times'):
            render_plan(authored)

    def test_unrecorded_or_moved_sound_rejected_after_rehash(self):
        plan, text = render_plan(fixture('16-sound-events.json'))
        cases = [text.replace('第2秒，门板', '第0秒，门板'), text.replace('音效：', '音效：第7秒响铃。', 1)]
        segment = plan['segments'][0]
        sound = frame_sound_prose(segment, frame_groups(segment)[0])
        cases.append(text.replace('音效：'+sound+'\n', ''))
        for changed in cases:
            review(plan, changed)
            self.assertTrue(any('sound effects differ' in e for e in validate_delivery(plan, changed)))

    def test_no_effects_means_no_extra_effect_field(self):
        plan, text = render_plan(fixture('14-visible-staging.json'))
        changed = text.rstrip()+'\n音效：吸气声。\n'
        review(plan, changed)
        self.assertTrue(any('sound effects differ' in e for e in validate_delivery(plan, changed)))

    def test_continuing_effect_requires_adjacent_same_source(self):
        self.assertEqual(self.sound_errors(continuing_plan()), [])
        mutations = [lambda p: p['segments'][1]['sound_events'][0].update(start=.1),
                     lambda p: p['segments'][1]['sound_events'][0].update(source='另一台风机'),
                     lambda p: p['segments'][0]['sound_events'][0].update(continues=False),
                     lambda p: p['segments'][1].update(episode='Other'),
                     lambda p: p['segments'][1]['sound_events'][0].update(continuation_of='missing')]
        for mutate in mutations:
            plan = continuing_plan(); mutate(plan)
            self.assertTrue(self.sound_errors(plan))

    def test_continuation_not_silently_lost_or_duplicated(self):
        plan = continuing_plan()
        plan['segments'][1]['sound_events'] = []
        self.assertTrue(any('needs next-clip' in e for e in self.sound_errors(plan)))
        plan = continuing_plan()
        plan['segments'][1]['sound_events'].append(dict(plan['segments'][1]['sound_events'][0], id='fan-c'))
        self.assertTrue(any('branch' in e for e in self.sound_errors(plan)))

    def test_unfinished_effect_allowed_at_actual_scope_end(self):
        plan = continuing_plan()
        plan['segments'][1]['sound_events'][0].update(continues=True)
        self.assertEqual(self.sound_errors(plan), [])
        plan['segments'][1]['sound_events'][0]['end'] = 1.5
        self.assertTrue(any('clip end' in e for e in self.sound_errors(plan)))

    def test_effect_cannot_span_flashback_editing_cut(self):
        plan = continuing_plan()
        plan['segments'][0]['duration'] = 4
        plan['segments'][0]['sound_events'][0]['end'] = 4
        plan['playback'] = [dict(segment_id='A', start=0, end=2),
                            dict(segment_id='B', start=0, end=2),
                            dict(segment_id='A', start=2, end=4)]
        self.assertTrue(any('fit one playback clip' in e for e in self.sound_errors(plan)))

    def test_gasp_requires_source_voice_or_confirmed_basis(self):
        authored = fixture('16-sound-events.json')
        effect = authored['segments'][0]['sound_events'][1]
        effect.update(source='Mara', text='惊讶吸气')
        with self.assertRaisesRegex(ValueError, 'source voice'):
            render_plan(authored)
        effect.update(basis_kind='source_voice', basis='测试变体正文明确写出Mara吸气。')
        self.assertEqual(validate(render_plan(authored)[0]), [])

    def test_short_replies_keep_full_voice_and_no_padding(self):
        plan, text = render_plan(fixture('15-short-replies.json'))
        review(plan, text)
        self.assertEqual(validate(plan)+validate_delivery(plan, text), [])
        self.assertEqual(plan['segments'][0]['duration'], 1)
        self.assertEqual(text.count('“Wait.”'), 1)
        self.assertEqual(text.count('“No.”'), 1)
        self.assertEqual([v['end']-v['start'] for v in plan['segments'][0]['speech']], [.5, .5])

    def test_short_shot_without_basis_or_readability_rejected(self):
        for key in ('reason', 'basis', 'readability'):
            authored = fixture('15-short-replies.json')
            del authored['segments'][0]['shots'][0]['short_shot'][key]
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'short_shot'):
                render_plan(authored)

    def test_short_exception_cannot_allow_multispeaker_parent_frame(self):
        authored = fixture('15-short-replies.json')
        authored['segments'][0]['shots'][1]['frame_id'] = '分镜1'
        with self.assertRaisesRegex(ValueError, 'multiple speakers'):
            render_plan(authored)

    def test_short_exception_not_retained_for_normal_shot(self):
        shot = fixture('15-short-replies.json')['segments'][0]['shots'][0]
        shot['end'] = 1
        errors = []; check_shot_pacing(shot, 'S', errors)
        self.assertTrue(any('only to a shot shorter' in e for e in errors))

    def test_preflight_rejects_irreducible_fractional_audio(self):
        plan = {'segments':[{'id':'A', 'pre_round_duration':12.8,
                             'timing_feasibility':{'minimum_seconds':12.8,'basis':'实测完整音频12.8秒'}}]}
        self.assertTrue(any('timing infeasible' in e for e in validate_timing_preflight(plan)))
        plan['segments'][0]['timing_feasibility'] = dict(minimum_seconds=12, basis='必要过程12秒，额外0.8秒为无任务尾停')
        self.assertEqual(validate_timing_preflight(plan), [])

    def test_preflight_without_cameras_and_requires_evidence(self):
        for raw in (.8, 31.2):
            p={'segments':[dict(id='A',pre_round_duration=raw,timing_feasibility=dict(minimum_seconds=raw,basis='来源'))]}
            self.assertTrue(validate_timing_preflight(p))
        self.assertTrue(validate_timing_preflight({'segments':[dict(id='A',pre_round_duration=2)]}))

    def test_timing_preflight_respects_explicit_limit_override(self):
        plan = dict(segments=[dict(id='S',pre_round_duration=35,
                                  timing_feasibility=dict(minimum_seconds=35,basis='对应原声'))],
                    limits=dict(segment_seconds=40), limits_basis='用户明确要求本次上限40秒。')
        self.assertEqual(validate_timing_preflight(plan), [])
        del plan['limits_basis']
        with self.assertRaises(KeyError):
            validate_timing_preflight(plan)

    def test_measured_audio_cannot_shrink_by_changing_estimate(self):
        authored = fixture('15-short-replies.json')
        authored['segments'][0]['speech'][0].update(measured_seconds=.7, measurement_basis='本条实测0.7秒')
        with self.assertRaisesRegex(ValueError, 'measured audio'):
            render_plan(authored)

    def test_minimum_time_and_measurement_are_not_boolean_or_nan(self):
        for bad in (True,float('nan'),-1):
            errors=[]
            check_segment_duration(dict(duration=2,timing_feasibility=dict(minimum_seconds=bad,basis='依据')), 'S', errors)
            self.assertTrue(errors)

    def test_compact_and_legacy_carry_both_bind_but_wrong_source_fails(self):
        plan, text = render_plan(fixture('08-continuous-reaction.json'))
        s=plan['segments'][0]; shot=s['shots'][1]; context=speech_context(plan)
        compact=speech_carry_prose(s,shot,context)
        legacy=speech_carry_prose(s,shot,context,style='legacy')
        self.assertLess(len(compact),len(legacy))
        for cue in (compact,legacy):
            changed=text.replace(compact,cue); review(plan,changed)
            self.assertEqual(validate_delivery(plan,changed), [])
        changed=text.replace(compact,compact.replace('Mara的','Elias的'))
        review(plan,changed)
        self.assertTrue(any('speech carry' in e for e in validate_delivery(plan,changed)))

    def test_timing_cli_is_not_final_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'timing.json'
            p.write_text(json.dumps({'mode':'真人剧','segments':[dict(id='A',pre_round_duration=2,
                                                     timing_feasibility=dict(minimum_seconds=2,basis='对应原声'))]}),encoding='utf-8')
            cmd=[sys.executable,'-X','utf8','-B',str(Path(__file__).with_name('validate_plan.py')),str(p),'--timing-only']
            result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('preflight only',result.stdout)
            result=subprocess.run(cmd+['--final-prompt',str(p)],capture_output=True,text=True,encoding='utf-8')
            self.assertNotEqual(result.returncode,0)

    def sound_errors(self,plan):
        errors=[]; sound_context(plan,errors); return errors


if __name__ == '__main__':
    unittest.main()
