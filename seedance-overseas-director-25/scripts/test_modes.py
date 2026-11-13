"""Observable mode routing, narration coverage, and inherited camera behavior."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mode_rules import MODES, require_mode, check_mode
from render_prompt import render_plan
from validate_plan import validate
from delivery_checks import validate_delivery
from test_render_prompt import authored_sample
from test_delivery_checks import review


def mode_sample(mode='推文'):
    plan = authored_sample()
    plan['mode'] = mode
    if MODES[mode]['animation']:
        plan['style_id'] = 'zootopia'
        plan['segments'][0]['presentation']['整体风格'] = '风格标签：风格化都市3D；风格化3D电影动画，形体清楚，表情与身体同步。'
    if MODES[mode]['narrative']:
        source = plan['sources'][0]
        voice = plan['segments'][0]['speech'][0]
        source['kind'] = voice['kind'] = 'narration'
        voice['performance'] = '清晰平稳中性，画内人物不对口型'
        plan['narration_sources'] = [dict(id='N1', episode=source['episode'], source_ref='测试源文第1句',
                                        original_text=source['text'], source_language=source['language'],
                                        status='keep', source_id=source['id'])]
    return plan


class ModeTests(unittest.TestCase):
    def test_missing_ambiguous_or_unknown_mode_has_no_default(self):
        for value in (None, '', '3D', '动画', '真人', '疯狂动物城', [], {}):
            with self.subTest(mode=value), self.assertRaisesRegex(ValueError, 'explicitly select'):
                require_mode({'mode': value})
        with self.assertRaises(ValueError):
            require_mode({})

    def test_four_modes_render_and_validate_one_shared_engine(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                plan, text = render_plan(mode_sample(mode))
                review(plan, text)
                self.assertEqual(validate(plan) + validate_delivery(plan, text), [])
                self.assertEqual('旁白参考' in text, MODES[mode]['narrative'])
                self.assertEqual('3D电影动画' in text, MODES[mode]['animation'])
                self.assertNotIn('VO 参考', text)

    def test_public_cli_requires_mode_even_for_timing_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'plan.json'
            path.write_text(json.dumps(authored_sample()), encoding='utf-8')
            for args in ([], ['--timing-only']):
                result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).with_name('validate_plan.py')),
                                         str(path), *args], capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(result.returncode, 2)
                self.assertIn('mode is required', result.stderr)
            result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).with_name('render_prompt.py')),
                                     str(path), '--output', str(Path(folder)/'final.txt'),
                                     '--plan-output', str(Path(folder)/'derived.json')],
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 2)
            self.assertFalse((Path(folder)/'final.txt').exists())

    def test_drama_rejects_narration_instead_of_dropping_it(self):
        for mode in ('真人剧', '3D漫真人剧'):
            data = mode_sample('3D漫推文' if mode.startswith('3D') else '推文')
            data['mode'] = mode
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'incompatible|independent narration'):
                render_plan(data)

    def test_narration_inventory_is_required_and_cannot_lose_retained_text(self):
        data = mode_sample()
        data.pop('narration_sources')
        with self.assertRaisesRegex(ValueError, 'inventory missing'):
            render_plan(data)
        data = mode_sample()
        data['narration_sources'][0]['original_text'] = 'An essential fact was omitted.'
        with self.assertRaisesRegex(ValueError, 'same-language narration changed'):
            render_plan(data)

    def test_authorized_omission_remains_in_inventory(self):
        data = mode_sample('真人剧')
        data['mode'] = '推文'
        data['narration_sources'] = [dict(id='N1', episode=data['sources'][0]['episode'], source_ref='测试源文引导语',
                                        original_text='She said.', source_language='en', status='omit',
                                        omission_basis='本技能已授权删除纯话语引导，实际对白保留')]
        plan, text = render_plan(data)
        self.assertNotIn('旁白参考', text)
        self.assertEqual(plan['english_words'] if 'english_words' in plan else plan['segments'][0]['english_words'], 3)
        data['narration_sources'][0]['source_id'] = data['sources'][0]['id']
        with self.assertRaisesRegex(ValueError, 'omitted narration must not reference'):
            render_plan(data)

    def test_inner_voice_uses_correct_label_without_neutral_narrator(self):
        data = mode_sample()
        data['sources'][0]['kind'] = data['segments'][0]['speech'][0]['kind'] = 'inner'
        data['narration_sources'] = []
        data['narration_absence_basis'] = '原文只有人物内心声，没有叙事旁白'
        data['segments'][0]['speech'][0]['performance'] = '压低内心声，情绪发紧，不对口型'
        plan, text = render_plan(data)
        self.assertIn('旁白参考', text)
        self.assertIn('内心声', text)
        self.assertNotIn('叙事旁白，', text)
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])

    def test_borrowed_voice_keeps_speaker_and_reference_separate(self):
        data = mode_sample()
        data['sources'][0].update(voice_reference='Elias', voice_reference_basis='用户指定旁白借用Elias音色')
        data['segments'][0]['speech'][0]['voice_reference'] = 'Elias'
        plan, text = render_plan(data)
        self.assertIn('（画外叙事｜Mara）旁白参考（Elias）音色', text)
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])
        changed = text.replace('（画外叙事｜Mara）旁白参考（Elias）', '（画外叙事｜Elias）旁白参考（Elias）')
        review(plan, changed)
        self.assertTrue(any('final speech' in e for e in validate_delivery(plan, changed)))

    def test_narrator_emotion_is_rejected(self):
        data = mode_sample()
        data['segments'][0]['speech'][0]['performance'] = '中性但带明显哭腔'
        with self.assertRaisesRegex(ValueError, 'character emotion'):
            render_plan(data)

    def test_complete_episode_minimum_is_mode_specific(self):
        for mode in MODES:
            data = mode_sample(mode)
            data['episodes'][0]['complete'] = True
            if MODES[mode]['narrative']:
                with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'episode minimum'):
                    render_plan(data)
            else:
                render_plan(data)

    def test_3d_mode_requires_style_and_rejects_live_spec(self):
        data = mode_sample('3D漫真人剧')
        data.pop('style_id')
        with self.assertRaisesRegex(ValueError, 'style_id'):
            render_plan(data)
        data = mode_sample('3D漫推文')
        data['segments'][0]['presentation']['画面规格'] = '真人实拍'
        with self.assertRaisesRegex(ValueError, 'incompatible live-action'):
            render_plan(data)

    def test_generic_3d_does_not_require_a_film_style(self):
        data = mode_sample('3D漫推文')
        data['style_id'] = 'generic-3d'
        data['segments'][0]['presentation']['整体风格'] = '风格标签：通用3D；清楚的形体，接触处有重量反馈。'
        _, text = render_plan(data)
        self.assertNotIn('疯狂动物城', text)

    def test_narrative_short_shot_does_not_inherit_drama_exception(self):
        data = mode_sample()
        data['segments'][0]['shots'][0]['end'] = .5
        errors = []
        check_mode(data, errors)
        self.assertTrue(any('>= 1 second' in e for e in errors))
        data['mode'] = '真人剧'
        errors = []
        check_mode(data, errors)
        self.assertFalse(any('>= 1 second' in e for e in errors))

    def test_all_modes_keep_live_action_cut_rules(self):
        for mode in MODES:
            data = mode_sample(mode)
            first, second = data['segments'][0]['shots']
            second['start_scale'] = second['end_scale'] = first['end_scale']
            second['start_camera'] = second['end_camera'] = first['end_camera']
            second['view'] = deepcopy(first['view'])
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'same scale and camera'):
                render_plan(data)

    def test_long_narration_needs_timed_progression_and_final_binding(self):
        data = mode_sample()
        segment = data['segments'][0]
        segment['shots'] = [segment['shots'][0]]
        shot = segment['shots'][0]
        shot['end'] = 8
        segment['speech'][0].update(start=0, end=8, estimated_seconds=8)
        data['action_tracks'] = []
        with self.assertRaisesRegex(ValueError, 'narration_visual'):
            render_plan(data)
        shot['narration_visual'] = dict(mode='progression', basis='测试来源中原有两段连续可见行为',
            long_take=dict(reason='continuous_action', why_uncut='持续保留人物与门口的距离变化及接触衔接',
                           finish_evidence='右手从身侧抬起握住原门把',
                           cut_review=dict(at=4, next_view='切至门把与手的接触',
                               loss_if_cut='合成测试要求完整观察人物从走廊抵达此门，切开会失去门口与原通道的连续空间揭示')), beats=[
            dict(start=0, end=4, change='reveal', new_information='出口逐渐进入右侧画面',
                 visual_evidence='Mara沿原走廊朝门口走，出口逐渐进入右侧画面'),
            dict(start=4, end=8, change='action_progress', new_information='右手从身侧抬起握住原门把',
                 visual_evidence='Mara走到门前停稳，右手从身侧抬起握住原门把')])
        segment['narration_coverage'] = [dict(source_id=data['sources'][0]['id'],
            text=segment['speech'][0]['text'], task='action', focus='合成原有行走与门把接触',
            views=[dict(shot_start=0, evidence='右手从身侧抬起握住原门把')])]
        with self.assertRaisesRegex(ValueError, 'strictly < 3'):
            render_plan(data)

    def test_source_backed_cross_category_overlap_only_in_narrative(self):
        data = mode_sample()
        segment = data['segments'][0]
        narrator = segment['speech'][0]
        # Both voices occupy the first formal frame; the second frame only continues visuals.
        narrator.update(start=0, end=2, estimated_seconds=2)
        source = deepcopy(data['sources'][0])
        source.update(id='D2', kind='dialogue', speaker='Elias', text='Wait.')
        data['sources'].append(source)
        segment['speech'].append(dict(source_id='D2', kind='dialogue', speaker='Elias', text='Wait.',
                                      start=1, end=2, estimated_seconds=1, performance='短促喊出'))
        segment['overlap_basis'] = '原文明示Elias在叙述尚未结束时喊话'
        fid = segment['shots'][0]['frame_id']
        segment['frame_voice_exceptions'] = {fid: dict(basis=segment['overlap_basis'],
            source_ids=[narrator['source_id'], 'D2'], lip_sync_speakers=['Elias'])}
        plan, text = render_plan(data)
        review(plan, text)
        self.assertEqual(validate(plan) + validate_delivery(plan, text), [])
        segment['speech'][-1].update(start=2, end=3)
        with self.assertRaisesRegex(ValueError, 'exception|multiple voice kinds'):
            render_plan(data)


if __name__ == '__main__':
    unittest.main()
