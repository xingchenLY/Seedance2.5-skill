from copy import deepcopy
import unittest

from delivery_checks import validate_delivery
from relation_layouts import layout_errors, relation_layout_prose
from render_prompt import render_plan
from test_delivery_checks import review
from test_render_prompt import authored_sample


def distant_sample():
    plan = authored_sample()
    shot = plan['segments'][0]['shots'][0]
    shot['visible_characters'] = ['Mara']
    shot['view'].update(position='长桌北端 Mara 斜前方', foreground=[])
    shot['relation']['placements'] = [
        dict(character='Mara', visibility='visible', placement='在长桌北端，朝画右外的 Elias 讲话'),
        dict(character='Elias', visibility='offscreen', placement='在画右外的长桌南端，与 Mara 隔整张桌子相对'),
    ]
    shot['relation']['single_reason'] = '双方相隔整张长桌，近景不能将对方肩背挤到主拍身旁'
    shot['camera_text'] = '平视固定，保持真实距离。'
    shot['visual_text'] = 'Mara 对 Elias 说原句，口型对齐本人声音。'
    return plan


class RelationshipLayoutTests(unittest.TestCase):
    def test_names_only_visual_text_gets_actual_authored_positions(self):
        authored = distant_sample()
        original = deepcopy(authored)
        plan, text = render_plan(authored)
        self.assertEqual(authored, original)
        self.assertIn(relation_layout_prose(plan['segments'][0]['shots'][0]), text)
        self.assertIn('画右外的长桌南端', text)
        self.assertNotIn('scene_characters', text)
        self.assertNotIn('single_reason', text)

    def test_action_description_does_not_have_to_repeat_spatial_cast(self):
        authored = distant_sample()
        authored['segments'][0]['shots'][0]['visual_text'] = 'Mara 从直立姿态微微前倾，口型同步。'
        plan, text = render_plan(authored)
        shot = plan['segments'][0]['shots'][0]
        self.assertEqual(shot['relation']['evidence'], relation_layout_prose(shot))
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])

    def test_removing_positions_cannot_pass_by_rehashing_and_keeping_names(self):
        plan, text = render_plan(distant_sample())
        layout = relation_layout_prose(plan['segments'][0]['shots'][0])
        damaged = text.replace(layout, '')
        review(plan, damaged)
        self.assertTrue(any('spatial placements are absent' in e for e in validate_delivery(plan, damaged)))

    def test_positions_only_in_header_do_not_count(self):
        plan, text = render_plan(distant_sample())
        layout = relation_layout_prose(plan['segments'][0]['shots'][0])
        damaged = text.replace(layout, '').replace('人物位置关系：', '人物位置关系：' + layout)
        review(plan, damaged)
        self.assertTrue(any('spatial placements are absent' in e for e in validate_delivery(plan, damaged)))

    def test_missing_positions_do_not_fall_back_to_names(self):
        authored = authored_sample()
        del authored['segments'][0]['shots'][0]['relation']['placements']
        with self.assertRaisesRegex(ValueError, 'needs placements'):
            render_plan(authored)

    def test_solo_cannot_hide_an_on_scene_interlocutor(self):
        authored = distant_sample()
        shot = authored['segments'][0]['shots'][0]
        shot['relation'].update(mode='solo', characters=['Mara'])
        shot['visual_text'] = 'Mara 独自站在桌边，开口说原句。'
        with self.assertRaisesRegex(ValueError, 'solo cannot hide'):
            render_plan(authored)

    def test_missing_scene_roster_does_not_infer_solo_from_visible_cast(self):
        authored = distant_sample()
        shot = authored['segments'][0]['shots'][0]
        shot['relation'].update(mode='solo', characters=['Mara'])
        del shot['relation']['scene_characters']
        with self.assertRaisesRegex(ValueError, 'scene_characters'):
            render_plan(authored)

    def test_distant_single_does_not_force_a_shoulder_into_frame(self):
        plan, text = render_plan(distant_sample())
        self.assertEqual(plan['segments'][0]['shots'][0]['visible_characters'], ['Mara'])
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])

    def test_justified_distant_exchange_can_keep_both_shots_single(self):
        authored = distant_sample()
        second = authored['segments'][0]['shots'][1]
        second['visible_characters'] = ['Elias']
        second['view'].update(position='长桌南端 Elias 斜前方', foreground=[])
        second['relation']['placements'] = [
            dict(character='Elias', visibility='visible', placement='在长桌南端，朝画左外的 Mara 看去'),
            dict(character='Mara', visibility='offscreen', placement='在画左外的长桌北端，与 Elias 隔整张桌子相对'),
        ]
        second['relation']['single_reason'] = '原文距离使近景无法合理容纳对方肩背，承接长桌另一端的视线'
        second['camera_text'] = '反向平视固定。'
        second['visual_text'] = 'Elias 承接压力保持沉默。'
        plan, text = render_plan(authored)
        self.assertEqual([s['visible_characters'] for s in plan['segments'][0]['shots']], [['Mara'], ['Elias']])
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])

    def test_same_scene_single_needs_a_specific_reason(self):
        authored = distant_sample()
        del authored['segments'][0]['shots'][0]['relation']['single_reason']
        with self.assertRaisesRegex(ValueError, 'single_reason'):
            render_plan(authored)

    def test_on_scene_bystander_is_listed_without_becoming_visible(self):
        authored = authored_sample()
        for shot in authored['segments'][0]['shots']:
            shot['relation']['scene_characters'].append('Observer')
        authored['segments'][0]['presentation']['人物位置关系'] += 'Observer 在房间东侧门边，位于本段近景画外。'
        plan, text = render_plan(authored)
        self.assertIn('出镜人物：Mara、Elias、Observer', text)
        self.assertEqual(plan['segments'][0]['cast'], ['Mara', 'Elias', 'Observer'])
        self.assertTrue(all('Observer' not in shot['visible_characters']
                            for shot in plan['segments'][0]['shots']))

    def test_real_solo_phone_and_insert_do_not_require_shared_framing(self):
        for mode, scene, characters, visible in [
            ('solo', ['Mara'], ['Mara'], ['Mara']),
            ('remote', ['Mara'], ['Mara', 'Elias'], ['Mara']),
            ('insert', ['Mara', 'Elias'], ['Mara', 'Elias'], []),
            ('empty', [], [], []),
        ]:
            with self.subTest(mode=mode):
                shot = dict(visible_characters=visible,
                            relation=dict(mode=mode, scene_characters=scene, characters=characters))
                self.assertEqual(layout_errors(shot), [])
                self.assertEqual(relation_layout_prose(shot), '')

    def test_placements_cover_offscreen_people_and_match_visibility(self):
        for mutation in ('missing', 'duplicate', 'invisible', 'unknown'):
            with self.subTest(mutation=mutation):
                authored = distant_sample()
                placements = authored['segments'][0]['shots'][0]['relation']['placements']
                if mutation == 'missing':
                    placements.pop()
                elif mutation == 'duplicate':
                    placements.append(deepcopy(placements[0]))
                elif mutation == 'invisible':
                    placements[1]['visibility'] = 'visible'
                else:
                    placements[1]['character'] = 'Unknown'
                with self.assertRaises(ValueError):
                    render_plan(authored)

    def test_scene_roster_must_cover_visible_and_same_scene_people(self):
        for scene in ([], ['Mara'], ['Elias']):
            with self.subTest(scene=scene):
                authored = authored_sample()
                authored['segments'][0]['shots'][0]['relation']['scene_characters'] = scene
                with self.assertRaisesRegex(ValueError, 'scene_characters'):
                    render_plan(authored)

    def test_malformed_layouts_report_errors(self):
        for field, value in [('scene_characters', None), ('scene_characters', [['Mara']]),
                             ('scene_characters', ['Mara', 'Mara']), ('characters', [['Mara']]),
                             ('characters', None), ('placements', {}),
                             ('placements', [None]), ('placements', [dict(character='Mara',
                                visibility='visible', placement='\n')])]:
            with self.subTest(field=field, value=value):
                authored = authored_sample()
                authored['segments'][0]['shots'][0]['relation'][field] = value
                with self.assertRaises(ValueError):
                    render_plan(authored)


if __name__ == '__main__':
    unittest.main()
