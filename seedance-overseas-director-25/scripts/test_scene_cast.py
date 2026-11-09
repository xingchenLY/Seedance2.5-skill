"""A scene roster retains offscreen people without inventing their visibility."""
from copy import deepcopy
import unittest

from delivery_checks import validate_delivery
from relation_layouts import scene_cast
from render_prompt import render_plan
from test_modes import mode_sample
from test_shot_pacing import fixture
from validate_plan import validate


def offscreen_sample(mode='真人剧'):
    data = mode_sample(mode)
    segment = data['segments'][0]
    for shot in segment['shots']:
        shot['relation']['scene_characters'].append('Observer')
    segment['presentation']['人物位置关系'] += 'Observer 在同一房间东侧门边，距 Mara 两米，朝向门口，本段位于画外。'
    return data


class SceneCastTests(unittest.TestCase):
    def test_all_modes_keep_whole_segment_offscreen_person_and_location(self):
        for mode in ('真人剧', '3D漫真人剧', '推文', '3D漫推文'):
            data = offscreen_sample(mode)
            before = deepcopy(data)
            plan, text = render_plan(data)
            segment = plan['segments'][0]
            self.assertEqual(data, before)
            self.assertEqual(validate_delivery(plan, text, require_review=False), [])
            self.assertEqual(segment['cast'], ['Mara', 'Elias', 'Observer'])
            self.assertIn('出镜人物：Mara、Elias、Observer', text)
            self.assertIn('Observer 在同一房间东侧门边，距 Mara 两米', text)
            self.assertTrue(all('Observer' not in s['visible_characters'] for s in segment['shots']))
            self.assertNotIn('Observer', text.split('分镜1')[1])
            self.assertEqual(segment['speech'], before['segments'][0]['speech'])
            self.assertEqual(segment['duration'], 4)
            self.assertEqual(len(segment['shots']), 2)
            self.assertEqual(plan['action_tracks'], before['action_tracks'])

    def test_previously_declared_offscreen_person_is_accepted_without_new_shot(self):
        data = offscreen_sample()
        data['segments'][0]['cast'].append('Observer')
        plan, _ = render_plan(data)
        self.assertEqual(len(plan['segments'][0]['shots']), 2)
        self.assertIn('Observer', plan['segments'][0]['cast'])

    def test_missing_authored_position_blocks_render(self):
        data = offscreen_sample()
        data['segments'][0]['presentation']['人物位置关系'] = 'Mara 与 Elias 面对面。'
        with self.assertRaisesRegex(ValueError, 'scene positions omit.*Observer'):
            render_plan(data)

    def test_final_offscreen_position_cannot_be_removed_or_moved_silently(self):
        plan, text = render_plan(offscreen_sample())
        position = plan['segments'][0]['presentation']['人物位置关系']
        for changed in ('Mara 与 Elias 面对面。', position.replace('东侧门边', '西侧窗边')):
            damaged = text.replace(position, changed)
            errors = validate_delivery(plan, damaged, require_review=False)
            self.assertTrue(any('scene positions' in error for error in errors), errors)

    def test_shortened_cast_and_final_header_cannot_hide_on_scene_roster(self):
        plan, text = render_plan(offscreen_sample())
        plan['segments'][0]['cast'].remove('Observer')
        text = text.replace('出镜人物：Mara、Elias、Observer', '出镜人物：Mara、Elias')
        self.assertTrue(any('scene-character union' in error
                            for error in validate_delivery(plan, text, require_review=False)))

    def test_on_scene_person_is_not_a_visible_primary_subject(self):
        plan, _ = render_plan(offscreen_sample())
        plan['segments'][0]['shots'][0]['start_subject'] = 'Observer'
        self.assertTrue(any('primary character is not in visible_characters' in error for error in validate(plan)))

    def test_revealing_the_existing_position_updates_visibility_not_cast_or_time(self):
        data = offscreen_sample()
        segment = data['segments'][0]
        segment['presentation']['人物位置关系'] = segment['presentation']['人物位置关系'].replace('本段位于画外', '首镜位于画外，反打中景纳入门边位置')
        first, second = segment['shots']
        first.update(start_scale='近景', end_scale='近景')
        second['visible_characters'].append('Observer')
        second['relation']['characters'].append('Observer')
        second['relation']['placements'].append(dict(character='Observer', visibility='visible',
            placement='位于门边原位置，身体朝向门口'))
        second['view']['background'] += '及门边原位的 Observer'
        plan, text = render_plan(data)
        self.assertEqual(plan['segments'][0]['cast'], ['Mara', 'Elias', 'Observer'])
        self.assertNotIn('Observer', first['visible_characters'])
        self.assertIn('Observer', plan['segments'][0]['shots'][1]['visible_characters'])
        self.assertEqual(plan['segments'][0]['duration'], 4)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_scene_roster_and_timing_are_not_replaced_by_all_episode_names(self):
        segment = dict(id='S', shots=[
            dict(visible_characters=['A'], relation=dict(scene_characters=['A', 'C'])),
            dict(visible_characters=['A'], relation=dict(scene_characters=['A', 'D']))],
            speech=[dict(speaker='RemoteOnly')])
        before = deepcopy(segment)
        self.assertEqual(scene_cast(segment), ['A', 'C', 'D'])
        self.assertEqual(segment, before)
        self.assertNotIn('C', segment['shots'][1]['relation']['scene_characters'])
        self.assertNotIn('D', segment['shots'][0]['relation']['scene_characters'])
        self.assertNotIn('RemoteOnly', scene_cast(segment))

    def test_two_actual_locations_merge_rosters_without_merging_local_presence(self):
        segment = dict(id='S', shots=[
            dict(visible_characters=['A'], relation=dict(scene_characters=['A', 'OfficeObserver'])),
            dict(visible_characters=['B'], relation=dict(scene_characters=['B', 'StreetObserver']))])
        self.assertEqual(scene_cast(segment), ['A', 'B', 'OfficeObserver', 'StreetObserver'])
        self.assertNotIn('B', segment['shots'][0]['relation']['scene_characters'])

    def test_no_people_in_frame_does_not_mean_empty_scene_cast(self):
        data = fixture('13-static-display.json')
        segment = data['segments'][0]
        for shot in segment['shots']:
            self.assertEqual(shot['visible_characters'], [])
            shot['relation']['scene_characters'] = ['Observer']
        segment['presentation']['人物位置关系'] = 'Observer 在同一房间东侧门边，位于本镜道具取景外。'
        plan, text = render_plan(data)
        self.assertEqual(plan['segments'][0]['cast'], ['Observer'])
        self.assertIn('出镜人物：Observer', text)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_truly_empty_scene_still_has_empty_cast(self):
        plan, text = render_plan(fixture('13-static-display.json'))
        self.assertEqual(plan['segments'][0]['cast'], [])
        self.assertIn('出镜人物：无', text)

    def test_missing_scene_roster_is_not_a_production_visibility_fallback(self):
        data = offscreen_sample()
        del data['segments'][0]['shots'][0]['relation']['scene_characters']
        with self.assertRaisesRegex(ValueError, 'scene_characters'):
            render_plan(data)


if __name__ == '__main__':
    unittest.main()
