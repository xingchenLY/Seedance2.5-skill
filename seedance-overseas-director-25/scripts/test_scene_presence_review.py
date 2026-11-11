"""Scoped passive-presence review; no automatic composition edits or media claims."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import unittest

from scene_presence_review import review_scene_presence
from review_report import collect_review_report, print_review_report
from render_prompt import render_plan
from delivery_checks import validate_delivery
from test_scene_cast import offscreen_sample
from test_delivery_checks import review


def plan_sample():
    return dict(mode='真人剧',segments=[dict(id='S1',episode='E1',duration=4,shots=[
        dict(start=i,end=i+2,view=dict(space='房间',subject='A'),
             visible_characters=['A','B'],relation=dict(scene_characters=['A','B','C']))
        for i in (0,2)])])


class ScenePresenceReviewTests(unittest.TestCase):
    def test_unseen_person_is_a_scoped_candidate_without_mutation(self):
        p=plan_sample();before=deepcopy(p)
        findings=review_scene_presence(p)
        self.assertEqual(p,before)
        self.assertEqual(len(findings),1)
        self.assertEqual(findings[0]['characters'],['C'])
        self.assertEqual([s['start'] for s in findings[0]['shots']],[0,2])

    def test_passive_people_with_same_scope_share_one_candidate(self):
        p=plan_sample()
        for shot in p['segments'][0]['shots']:shot['relation']['scene_characters'].append('D')
        self.assertEqual(review_scene_presence(p)[0]['characters'],['C','D'])
        self.assertEqual(len(review_scene_presence(p)),1)

    def test_existing_visibility_needs_no_dialogue_action_or_dedicated_shot(self):
        p=plan_sample();p['segments'][0]['shots'][0]['visible_characters'].append('C')
        self.assertEqual(review_scene_presence(p),[])

    def test_continuous_scene_across_segments_does_not_demand_repeat_appearance(self):
        p=plan_sample();second=deepcopy(p['segments'][0]);second['id']='S2';p['segments'].append(second)
        p['segments'][0]['shots'][0]['visible_characters'].append('C')
        self.assertEqual(review_scene_presence(p),[])
        p['segments'][0]['shots'][0]['visible_characters'].remove('C')
        self.assertEqual([s['segment_id'] for s in review_scene_presence(p)[0]['shots']],['S1','S1','S2','S2'])

    def test_scene_and_episode_boundaries_do_not_borrow_another_appearance(self):
        for field in ('space','episode'):
            p=plan_sample();second=deepcopy(p['segments'][0]);second['id']='S2';p['segments'].append(second)
            p['segments'][0]['shots'][0]['visible_characters'].append('C')
            if field=='episode':second['episode']='E2'
            else:
                for shot in second['shots']:shot['view']['space']='另一房间'
            self.assertEqual({s['segment_id'] for s in review_scene_presence(p)[0]['shots']},{'S2'})

    def test_event_boundaries_do_not_merge_flashback_with_current_room(self):
        p=plan_sample();p['segments'][0]['shots'][0]['visible_characters'].append('C')
        p['events']=[dict(id='past',segment_id='S1',start=0,end=2),
                     dict(id='present',segment_id='S1',start=2,end=4)]
        self.assertEqual([s['start'] for s in review_scene_presence(p)[0]['shots']],[2])

    def test_exit_then_return_restarts_presence_scope(self):
        p=plan_sample();shots=p['segments'][0]['shots']
        shots[0]['visible_characters'].append('C')
        shots[1]['relation']['scene_characters'].remove('C')
        returned=deepcopy(shots[0]);returned.update(start=4,end=6);returned['visible_characters'].remove('C')
        shots.append(returned);p['segments'][0]['duration']=6
        self.assertEqual([s['start'] for s in review_scene_presence(p)[0]['shots']],[4])

    def test_offscreen_voice_only_identity_is_not_added_to_the_scene(self):
        p=plan_sample();p['sources']=[dict(speaker='Remote')]
        self.assertEqual(review_scene_presence(p)[0]['characters'],['C'])

    def test_only_actual_played_shots_count_as_visibility(self):
        p=plan_sample();p['segments'][0]['shots'][0]['visible_characters'].append('C')
        p['playback']=[dict(segment_id='S1',start=2,end=4)]
        self.assertEqual([s['start'] for s in review_scene_presence(p)[0]['shots']],[2])

    def test_missing_space_does_not_get_an_invented_continuity_scope(self):
        p=plan_sample();p['segments'][0]['shots'][1].pop('view')
        self.assertEqual([s['start'] for s in review_scene_presence(p)[0]['shots']],[0])

    def test_report_is_complete_and_not_inserted_into_the_prompt(self):
        plan,text=render_plan(offscreen_sample())
        before=deepcopy(plan);stream=io.StringIO()
        with redirect_stdout(stream):print_review_report(plan)
        self.assertEqual(plan,before)
        self.assertIn('REVIEW [scene_presence]',stream.getvalue())
        self.assertIn('Observer',stream.getvalue())
        self.assertNotIn('scene_presence',text)
        item=next(i for i in collect_review_report(plan)['items'] if i['code']=='scene_presence')
        self.assertEqual(set(item['locations']),{('S1',0),('S1',2)})

    def test_unhandled_presence_candidate_blocks_final_delivery(self):
        plan,text=render_plan(offscreen_sample());review(plan,text)
        key=next(i['key'] for i in collect_review_report(plan)['items'] if i['code']=='scene_presence')
        plan['delivery_review']['findings']=[r for r in plan['delivery_review']['findings'] if r['key']!=key]
        self.assertTrue(any(key in e and 'unresolved REVIEW' in e for e in validate_delivery(plan,text)))

    def test_justified_offscreen_keeps_all_camera_action_voice_and_timing_records(self):
        for mode in ('真人剧','3D漫真人剧','推文','3D漫推文'):
            data=offscreen_sample(mode);before=deepcopy(data)
            plan,text=render_plan(data);review(plan,text)
            key=next(i['key'] for i in collect_review_report(plan)['items'] if i['code']=='scene_presence')
            row=next(r for r in plan['delivery_review']['findings'] if r['key']==key)
            row['basis']='合成测试：两镜均为桌边双方过肩近距离取景，东侧门边在其视野之外；保留Observer既定站位，不为展示而移人或拉远。'
            self.assertEqual(validate_delivery(plan,text),[])
            self.assertEqual(data,before)
            self.assertEqual(plan['segments'][0]['duration'],before['segments'][0]['duration'])
            self.assertEqual(plan['segments'][0]['speech'],before['segments'][0]['speech'])
            self.assertEqual(plan['action_tracks'],before['action_tracks'])
            for actual,original in zip(plan['segments'][0]['shots'],before['segments'][0]['shots']):
                for field in ('view','start','end','start_scale','end_scale','visible_characters'):
                    self.assertEqual(actual[field],original[field])


if __name__=='__main__':unittest.main()
