"""Motion boundary mechanics; prose meaning still requires actual source review."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from action_continuity import action_context, check_action_binding
from delivery_checks import validate_delivery
from render_prompt import render_plan


def sample():
    origin = {'甲.position': '桌边', '甲.motion_phase': '直立', 'phone.owner': '甲右手'}
    starting = {**origin, '甲.motion_phase': '向门口迈出第一步'}
    arrived = {**origin, '甲.position': '门口', '甲.motion_phase': '落脚停稳'}
    plan = dict(segments=[dict(id='S1', episode='E1', shots=[dict(start=0, end=1), dict(start=1, end=2)])],
        action_tracks=[dict(id='walk', episode='E1', time_layer='present',
            basis='原文甲持手机从桌边走到门口，途中无新事件。', end_state=arrived,
            beats=[dict(segment_id='S1', shot_start=0, before=origin, after=starting,
                        action_text='甲从桌边起步，右手仍握手机，朝门口迈出第一步。'),
                   dict(segment_id='S1', shot_start=1, before=arrived, after=arrived,
                        action_text='甲在门口落脚停稳，转向仍在桌边的乙，手机仍在甲右手。',
                        break_before=dict(kind='motion_ellipsis',
                            basis='完整无信息路程拖慢下一剧情节点，按用户授权剪掉中段步行。',
                            omitted='甲在桌边到门口之间无新事件的中段步行',
                            motion_keys=['甲.position', '甲.motion_phase'],
                            start_evidence='朝门口迈出第一步', result_evidence='甲在门口落脚停稳',
                            resume_text='切入较后时点，甲到门口，乙仍在桌边，手机持有者未变'))])])
    return plan


def inspect(plan):
    errors = []
    context = action_context(plan, errors, required=True)
    return context, errors


class MotionEllipsisTests(unittest.TestCase):
    def test_rendered_time_ellipsis_keeps_only_displayed_time_and_bound_cue(self):
        path = Path(__file__).resolve().parent.parent/'assets/evaluation/23-walk-through-light.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        segment = data['segments'][0]
        first = segment['shots'][0]
        first['end'] = 1
        first['view'] = dict(space='大厅', position='南侧墙边', direction='北侧', height='Mara眼高',
            subject='Mara', foreground=[], background='东窗及室内西侧')
        second = deepcopy(first)
        second.update(start=1, end=2, frame_id='分镜2', visual_text='Mara 位于画左室内西侧较暗处。',
            jump_cut_before=dict(from_shot_start=0, basis='合成用例：省略无信息的中段步行。',
                omitted='无新事件的中段步行', cue='进入稍后的停步时点，机位与方向不变'))
        segment['shots'].append(second)
        old = data['action_tracks'][0]
        origin = old['beats'][0]['before']
        destination = old['end_state']
        started = {'Mara.position': '东窗旁刚迈步，面朝西'}
        old['beats'] = [dict(segment_id='S1', shot_start=0, before=origin, after=started,
            action_text='Mara 从东窗旁向西迈出第一步，双手自然垂下。'),
            dict(segment_id='S1', shot_start=1, before=destination, after=destination,
            action_text='Mara 在室内西侧较暗处落脚停稳，仍面朝西，双手自然垂下。',
            break_before=dict(kind='motion_ellipsis', basis='合成测试省略无信息路程，保留启动与到达。',
                omitted='无新事件的中段步行', motion_keys=['Mara.position'],
                start_evidence='向西迈出第一步', result_evidence='在室内西侧较暗处落脚停稳',
                resume_text='切入较后时点，Mara 已到室内西侧较暗处'))]
        original = deepcopy(data)
        plan, text = render_plan(data)
        self.assertEqual(data, original)
        self.assertEqual(plan['segments'][0]['duration'], 2)
        self.assertEqual(plan['segments'][0]['speech'], [])
        self.assertIn('跳切省略无新事件的中段步行', text)
        self.assertIn('剪辑省略无新事件的中段步行', text)
        self.assertEqual(validate_delivery(plan, text, require_review=False), [])
        broken = text.replace('剪辑省略无新事件的中段步行。', '')
        self.assertTrue(any('action bridge is absent' in e for e in
                            validate_delivery(plan, broken, require_review=False)))

    def test_real_cut_keeps_start_and_result_and_exposes_omission(self):
        plan = sample()
        before = deepcopy(plan)
        context, errors = inspect(plan)
        self.assertEqual(errors, [])
        self.assertIn('迈出第一步', context[('S1', 0)][0])
        self.assertIn('剪辑省略甲在桌边到门口之间无新事件的中段步行', context[('S1', 1)][0])
        self.assertIn('甲在门口落脚停稳', context[('S1', 1)][0])
        self.assertEqual(plan, before)

    def test_without_declared_ellipsis_same_gap_is_a_state_jump(self):
        plan = sample()
        del plan['action_tracks'][0]['beats'][1]['break_before']
        self.assertTrue(any('state jump' in e for e in inspect(plan)[1]))

    def test_same_shot_cannot_be_used_as_instantaneous_travel(self):
        plan = sample()
        plan['action_tracks'][0]['beats'][1]['shot_start'] = 0
        self.assertTrue(any('overlap or reverse' in e for e in inspect(plan)[1]))

    def test_adjacent_cut_is_not_real_elapsed_offscreen_time(self):
        plan = sample()
        plan['action_tracks'][0]['beats'][1]['break_before']['kind'] = 'offscreen_progress'
        self.assertTrue(any('actual intervening playback time' in e for e in inspect(plan)[1]))

    def test_missing_or_fabricated_endpoint_evidence_fails(self):
        for field in ('start_evidence', 'result_evidence'):
            for value in ('', '这个动作没有出现在对应镜头'):
                plan = sample()
                plan['action_tracks'][0]['beats'][1]['break_before'][field] = value
                self.assertTrue(any('motion_ellipsis' in e for e in inspect(plan)[1]))

    def test_malformed_action_prose_returns_errors_instead_of_crashing(self):
        plan = sample()
        plan['action_tracks'][0]['beats'][0]['action_text'] = None
        self.assertTrue(inspect(plan)[1])

    def test_owner_or_contact_change_cannot_hide_in_omitted_path(self):
        for key in ('phone.owner', '甲.contact'):
            plan = sample()
            track = plan['action_tracks'][0]
            for beat in track['beats']:
                for state in ('before', 'after'):
                    beat[state][key] = '起始状态'
            beat = track['beats'][1]
            beat['before'][key] = beat['after'][key] = '隐藏的归属或接触变化'
            track['end_state'][key] = beat['after'][key]
            beat['break_before']['motion_keys'].append(key)
            self.assertTrue(any('only declared movement keys' in e for e in inspect(plan)[1]))

    def test_state_change_list_is_exact_and_does_not_drop_stable_states(self):
        for value in ([], ['甲.position'], ['甲.position', '甲.motion_phase', 'phone.owner'], None):
            plan = sample()
            plan['action_tracks'][0]['beats'][1]['break_before']['motion_keys'] = value
            self.assertTrue(inspect(plan)[1])

    def test_final_visual_must_include_omission_and_current_result(self):
        plan = sample()
        context, errors = inspect(plan)
        segment = plan['segments'][0]
        shot = segment['shots'][1]
        prose = context[('S1', 1)][0]
        check_action_binding(segment, shot, context, [prose], 'S1/1', errors)
        self.assertEqual(errors, [])
        check_action_binding(segment, shot, context,
            [plan['action_tracks'][0]['beats'][1]['action_text']], 'S1/1', errors)
        self.assertTrue(any('action bridge is absent' in e for e in errors))


if __name__ == '__main__':
    unittest.main()
