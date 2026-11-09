"""Preserve passive visible people without inventing speech or widening shots."""
from copy import deepcopy
import unittest

from delivery_checks import validate_delivery
from relation_layouts import derive_scene_cast, relation_layout_prose
from render_prompt import render_plan
from test_modes import mode_sample


def passive_sample(mode='真人剧'):
    data = mode_sample(mode)
    segment = data['segments'][0]
    for item in segment['shots']:
        item['relation']['scene_characters'].extend(['Observer', 'Unseen'])
    first = segment['shots'][0]
    first['visible_characters'].append('Observer')
    first['relation']['characters'].append('Observer')
    first['relation']['placements'].append(dict(character='Observer', visibility='visible',
        placement='位于画右后景门边，身体朝向门口'))
    segment['presentation']['人物位置关系'] += 'Observer 在门边站立；Unseen 在同一房间西侧墙边，本段位于画外。'
    return data


class VisibleCastTests(unittest.TestCase):
    def test_four_modes_keep_passive_background_without_extra_speech_or_time(self):
        for mode in ('真人剧', '3D漫真人剧', '推文', '3D漫推文'):
            data = passive_sample(mode)
            before = deepcopy(data)
            plan, text = render_plan(data)
            segment = plan['segments'][0]
            self.assertEqual(data, before)
            self.assertEqual(validate_delivery(plan, text, require_review=False), [])
            self.assertEqual(segment['cast'], ['Mara', 'Elias', 'Observer', 'Unseen'])
            self.assertIn('出镜人物：Mara、Elias、Observer', text)
            self.assertIn('Observer位于画右后景门边，身体朝向门口。', text)
            self.assertIn('Unseen', text)
            self.assertNotIn('Observer', segment['shots'][1]['visible_characters'])
            self.assertEqual(segment['speech'], before['segments'][0]['speech'])
            self.assertEqual(segment['duration'], 4)
            self.assertEqual(len(segment['shots']), 2)
            self.assertEqual(plan['action_tracks'], before['action_tracks'])

    def test_missing_authored_cast_is_derived_from_all_on_scene_people(self):
        data = passive_sample()
        data['segments'][0].pop('cast')
        plan, _ = render_plan(data)
        self.assertIn('Observer', plan['segments'][0]['cast'])

    def test_adding_a_visible_person_updates_stale_subset_without_removing_anyone(self):
        data = passive_sample()
        data['segments'][0]['cast'] = ['Mara']
        plan, _ = render_plan(data)
        self.assertEqual(plan['segments'][0]['cast'], ['Mara', 'Elias', 'Observer', 'Unseen'])

    def test_one_brief_or_partial_appearance_still_counts_for_whole_segment(self):
        data = passive_sample()
        placement = data['segments'][0]['shots'][0]['relation']['placements'][-1]
        data['segments'][0]['shots'][0].update(end_scale='近景',
            camera_text='固定机位连续收紧取景，Observer 的肩背在第0.5秒退出画框，人物仍在门边原站位。')
        placement['placement'] = '的可辨肩背在第0.5秒前留在画右后景，随后随收紧取景退出画框，仍在门边'
        plan, text = render_plan(data)
        self.assertIn('Observer', plan['segments'][0]['cast'])
        self.assertIn('出镜人物：Mara、Elias、Observer', text)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_named_background_group_is_not_required_to_have_individual_speech(self):
        data = passive_sample()
        segment = data['segments'][0]
        for item in segment['shots']:
            item['relation']['scene_characters'] = [n.replace('Observer', '门边两名员工')
                                                    for n in item['relation']['scene_characters']]
        first = segment['shots'][0]
        first['visible_characters'][-1] = '门边两名员工'
        first['relation']['characters'][-1] = '门边两名员工'
        first['relation']['placements'][-1]['character'] = '门边两名员工'
        segment['presentation']['人物位置关系'] = segment['presentation']['人物位置关系'].replace('Observer', '门边两名员工')
        plan, text = render_plan(data)
        self.assertIn('门边两名员工', plan['segments'][0]['cast'])
        self.assertNotIn('Observer', text)

    def test_no_speech_or_action_is_needed_to_derive_cast(self):
        segment = dict(id='S', cast=['Observer'], shots=[dict(visible_characters=['Observer'])])
        self.assertEqual(derive_scene_cast(segment), ['Observer'])
        segment['shots'].append(dict(visible_characters=[]))
        self.assertEqual(derive_scene_cast(segment), ['Observer'])

    def test_declared_person_cannot_silently_disappear_from_all_scene_records(self):
        data = mode_sample('真人剧')
        segment = data['segments'][0]
        segment['cast'].append('Observer')
        original = deepcopy(data)
        with self.assertRaisesRegex(ValueError, 'refusing to silently remove declared cast: Observer'):
            render_plan(data)
        self.assertEqual(data, original)

    def test_declared_cast_does_not_force_offscreen_person_into_frame(self):
        data = mode_sample('真人剧')
        data['segments'][0]['cast'].append('Unseen')
        with self.assertRaisesRegex(ValueError, 'Unseen'):
            render_plan(data)
        # After checking actual framing, correct the erroneous declared roster.
        data['segments'][0]['cast'].remove('Unseen')
        for item in data['segments'][0]['shots']:
            item['relation']['scene_characters'].append('Unseen')
        data['segments'][0]['presentation']['人物位置关系'] += 'Unseen 在同一房间西侧墙边，本段位于画外。'
        plan, text = render_plan(data)
        self.assertIn('Unseen', plan['segments'][0]['cast'])
        self.assertIn('Unseen', text)
        self.assertTrue(all('Unseen' not in shot['visible_characters']
                            for shot in plan['segments'][0]['shots']))

    def test_visible_placement_cannot_be_omitted_from_shot_roster(self):
        data = passive_sample()
        data['segments'][0]['shots'][0]['visible_characters'].remove('Observer')
        with self.assertRaisesRegex(ValueError, 'placement visibility differs'):
            render_plan(data)

    def test_final_header_cannot_remove_a_passive_visible_person(self):
        plan, text = render_plan(passive_sample())
        text = text.replace('出镜人物：Mara、Elias、Observer', '出镜人物：Mara、Elias')
        self.assertTrue(any('final cast differs' in e
                            for e in validate_delivery(plan, text, require_review=False)))

    def test_final_position_cannot_be_deleted_while_retaining_cast_name(self):
        plan, text = render_plan(passive_sample())
        first = plan['segments'][0]['shots'][0]
        prose = relation_layout_prose(first)
        shortened = prose.replace('Observer位于画右后景门边，身体朝向门口。', '')
        text = text.replace(prose, shortened)
        self.assertTrue(any('spatial placements are absent' in e
                            for e in validate_delivery(plan, text, require_review=False)))

    def test_cast_order_is_first_appearance_not_speech_or_primary_subject(self):
        segment = dict(id='S', cast=['Speaker', 'Observer'],
                       shots=[dict(visible_characters=['Observer', 'Speaker']),
                              dict(visible_characters=['Speaker'])])
        self.assertEqual(derive_scene_cast(segment), ['Observer', 'Speaker'])

    def test_malformed_declared_cast_is_reported_instead_of_overwritten(self):
        for value in (None, 'Observer', ['Observer', 'Observer'], [['Observer']], ['']):
            data = passive_sample()
            data['segments'][0]['cast'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'authored cast'):
                render_plan(data)


if __name__ == '__main__':
    unittest.main()
