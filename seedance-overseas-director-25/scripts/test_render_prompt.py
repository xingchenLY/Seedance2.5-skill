from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from delivery_checks import validate_delivery
from render_prompt import render_plan
from test_delivery_checks import delivery_sample, review
from validate_plan import validate


def authored_sample():
    plan, _ = delivery_sample()
    segment = plan['segments'][0]
    segment['presentation'] = {'整体风格': '真人写实，各镜头固定。', '对话氛围': '双方隔桌对视，Mara 低声说话。',
                               '场景': '大厅-白天', '环境光影': '右侧窗光。',
                               '人物位置关系': 'Mara 与 Elias 面对面站立。'}
    for i, shot in enumerate(segment['shots']):
        shot['camera_text'] = '平视过肩固定，主拍 ' + ('Mara' if i == 0 else 'Elias') + '。'
        shot['visual_text'] = shot['relation'].pop('evidence')
    segment['speech'][0]['performance'] = '音量偏低，咬字清楚，句尾音量保持'
    return plan


class RenderTests(unittest.TestCase):
    def test_derived_values_and_final_roundtrip(self):
        authored = authored_sample()
        authored['segments'][0].update(duration=100, english_words=999)
        original = deepcopy(authored)
        plan, text = render_plan(authored)
        self.assertEqual(authored, original)
        self.assertNotIn('delivery_review', plan)
        self.assertEqual(plan['segments'][0]['duration'], 4)
        self.assertEqual(plan['segments'][0]['english_words'], 3)
        self.assertEqual(plan['segments'][0]['cast'], ['Mara', 'Elias'])
        self.assertTrue(validate_delivery(plan, text))  # Manual review remains mandatory.
        review(plan, text)
        self.assertEqual(validate(plan) + validate_delivery(plan, text), [])

    def test_three_voice_kinds_and_chinese_names(self):
        for kind in ('dialogue', 'VO', 'OS'):
            plan = json.loads(json.dumps(dict(authored_sample(), mode='真人剧'), ensure_ascii=False).replace('Mara', '林晚').replace('Elias', '沈砚'))
            plan['sources'][0]['kind'] = plan['segments'][0]['speech'][0]['kind'] = kind
            derived, text = render_plan(plan)
            review(derived, text)
            self.assertEqual(validate_delivery(derived, text), [])
            self.assertIn('（林晚）', text)
            self.assertEqual(derived['segments'][0]['english_words'], 3)

    def test_internal_shots_and_cross_frame_speech(self):
        plan = authored_sample()
        segment = plan['segments'][0]
        segment['shots'][1]['frame_id'] = segment['shots'][0]['frame_id']
        segment['speech'][0]['end'] = 3.2
        derived, text = render_plan(plan)
        review(derived, text)
        self.assertEqual(validate_delivery(derived, text), [])
        self.assertEqual(text.count('“Put it down.”'), 1)
        self.assertIn('0.2–3.2 秒', text)

    def test_exact_voice_times_are_not_rounded(self):
        plan = authored_sample()
        plan['segments'][0]['speech'][0].update(start=0.123456789, end=1.723456789)
        derived, text = render_plan(plan)
        review(derived, text)
        self.assertEqual(validate_delivery(derived, text), [])

    def test_invalid_design_does_not_render(self):
        for field, value in [('visual_text', '0–9 秒错误区间'), ('camera_text', '换行\n音效：注入字段')]:
            plan = authored_sample()
            plan['segments'][0]['shots'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                render_plan(plan)
        plan = authored_sample()
        plan['segments'][0]['frame_notes'] = {'分镜2': {'transition': '硬切'}}
        with self.assertRaises(ValueError):
            render_plan(plan)

    def test_cli_renders_and_requires_different_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            authored, prompt, resolved = root/'authored.json', root/'final.txt', root/'resolved.json'
            authored.write_text(json.dumps(dict(authored_sample(), mode='真人剧'), ensure_ascii=False), encoding='utf-8')
            command = [sys.executable, '-X', 'utf8', '-B', str(Path(__file__).with_name('render_prompt.py')),
                       str(authored), '--output', str(prompt), '--plan-output', str(resolved)]
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('delivery_review', json.loads(resolved.read_text(encoding='utf-8')))
            before = authored.read_bytes()
            command[-1] = str(authored)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(authored.read_bytes(), before)

    def test_fixed_scenarios(self):
        fixtures = Path(__file__).resolve().parent.parent/'assets'/'evaluation'
        cases = sorted(fixtures.glob('*.json'))
        self.assertEqual(len(cases), 23)
        for path in cases:
            with self.subTest(case=path.stem):
                authored = json.loads(path.read_text(encoding='utf-8'))
                plan, text = render_plan(authored)
                self.assertEqual(validate(plan), [])
                review(plan, text)
                self.assertEqual(validate_delivery(plan, text), [])
                for voice in authored['sources']:
                    self.assertIn(voice['text'], text)

    def test_multiple_episodes_keep_titles_and_reset_segment_numbers(self):
        plan = authored_sample()
        plan['episodes'][0]['title'] = '第 2 集'
        plan['episodes'].append(dict(id='later', complete=False, title='第 4 集'))
        source = deepcopy(plan['sources'][0])
        source.update(id='L2', episode='later')
        plan['sources'].append(source)
        segment = deepcopy(plan['segments'][0])
        segment.update(id='S2', episode='later')
        segment['speech'][0]['source_id'] = 'L2'
        plan['segments'].append(segment)
        derived, text = render_plan(plan)
        review(derived, text)
        self.assertEqual(validate_delivery(derived, text), [])
        self.assertIn('第 2 集｜', text)
        self.assertIn('第 4 集｜', text)
        self.assertIn('第 2-1 段｜', text)
        self.assertIn('第 4-1 段｜', text)
        self.assertNotIn('第 1 段｜', text)

    def test_independent_flashback_playback_map(self):
        plan = authored_sample()
        plan['episodes'][0]['title'] = '第一集'
        plan['sources'] = []
        first = plan['segments'][0]
        first['speech'] = []
        memory = deepcopy(first)
        memory['id'] = 'memory'
        for i, shot in enumerate(memory['shots']):
            shot.update(start_scale='近景', end_scale='近景', start_camera=f'memory-{i}', end_camera=f'memory-{i}')
        plan['segments'].append(memory)
        plan['events'] = [dict(id=name, episode='current', segment_id=sid, start=a, end=b, text=name)
                          for name, sid, a, b in [('A', 'S1', 0, 2), ('B', 'memory', 0, 4), ('C', 'S1', 2, 4)]]
        plan['playback'] = [dict(segment_id=sid, start=a, end=b) for sid, a, b in
                            [('S1', 0, 2), ('memory', 0, 4), ('S1', 2, 4)]]
        derived, text = render_plan(plan)
        review(derived, text)
        self.assertEqual(validate_delivery(derived, text), [])
        self.assertIn('第 1-1 段｜', text)
        self.assertIn('第 1-2 段｜', text)
        self.assertIn('剪辑顺序：第 1-1 段 0–2 秒 → 第 1-2 段 0–4 秒 → 第 1-1 段 2–4 秒', text)
        changed = text.replace('剪辑顺序：第 1-1 段', '剪辑顺序：第 2-1 段')
        review(derived, changed)
        self.assertTrue(any('editing map differs' in e for e in validate_delivery(derived, changed)))

    def test_episode_segment_numbers_and_continuation(self):
        for title, prefix in [('第一集', '1'), ('第十二集', '12'), ('第 21 集', '21'), ('第零集', '0')]:
            with self.subTest(title=title):
                authored = authored_sample()
                authored['episodes'][0].update(title=title, segment_start=3)
                plan, text = render_plan(authored)
                self.assertIn(f'第 {prefix}-3 段｜', text)
                self.assertNotIn('segment_start', text)
                review(plan, text)
                self.assertEqual(validate_delivery(plan, text), [])
                for wrong in ('3', f'{prefix}-1', '99-3'):
                    changed = text.replace(f'第 {prefix}-3 段｜', f'第 {wrong} 段｜')
                    review(plan, changed)
                    self.assertTrue(any('numbering differs' in e for e in validate_delivery(plan, changed)))

    def test_unknown_episode_number_is_not_inferred_from_internal_id(self):
        authored = authored_sample()
        authored['episodes'][0]['id'] = 'E99'
        for item in authored['sources'] + authored['segments']:
            item['episode'] = 'E99'
        _, text = render_plan(authored)
        self.assertIn('第 1 段｜', text)
        self.assertNotIn('第 99-1 段｜', text)

    def test_invalid_numbering_metadata_is_rejected(self):
        for start in (0, -1, True, 1.5, '2'):
            authored = authored_sample()
            authored['episodes'][0]['segment_start'] = start
            with self.subTest(start=start), self.assertRaisesRegex(ValueError, 'segment_start'):
                render_plan(authored)

    def test_number_expansion_and_mixed_language_counts(self):
        plan = authored_sample()
        plan['sources'][0]['text'] = plan['segments'][0]['speech'][0]['text'] = 'Pay $20.'
        plan['segments'][0]['speech'][0]['number_expansions'] = [{'start': 4, 'end': 7, 'spoken': 'twenty dollars'}]
        derived, text = render_plan(plan)
        self.assertEqual(derived['segments'][0]['english_words'], 3)
        self.assertIn('“Pay $20.”', text)
        plan = authored_sample()
        plan['sources'][0].update(text='Hello. Hola.', language='mul')
        plan['segments'][0]['speech'][0].update(text='Hello. Hola.', language_parts=[
            {'language':'en', 'text':'Hello.'}, {'language':'es', 'text':'Hola.'}])
        derived, text = render_plan(plan)
        self.assertEqual(derived['segments'][0]['english_words'], 1)
        self.assertIn('“Hello. Hola.”', text)


if __name__ == '__main__':
    unittest.main()
