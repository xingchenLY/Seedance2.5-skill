"""Character grouping regressions using indexed script evidence and real state."""
from pathlib import Path
import tempfile
import unittest

import asset_state
from input_check import load_and_validate
from pipeline_common import atomic_json, read_json, script_hash
from script_index import build_index
from workbook_data import build_payload


CHARACTERS = [
    ('艾玛', '红色大衣', None), ('诺亚', '蓝色外套', None),
    ('艾玛', '蓝色外套', None), ('诺亚', '黑色西装', None),
    ('艾玛', '红色大衣', '淋湿'),
]
SELECTION = {'人物': [{'name': '艾玛', 'count': 4}, {'name': '诺亚', 'count': 3}]}


def create_fixture(folder, selected_assets=None, turnarounds=True, design_turnarounds=True):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    lines, scenes, assets = ['EPISODE 1'], [], []
    for i, (name, outfit, state) in enumerate(CHARACTERS):
        line = len(lines) + 1
        action = f'{name}穿着{outfit}' + (f'，衣服{state}' if state else '') + '。'
        lines.extend([f'房间{i+1}', action])
        scenes.append({'id': f's{i}', 'start_line': line, 'end_line': line+1,
                       'episode': 1, 'label': f'1-{i+1}', 'location': f'房间{i+1}'})
        variant = {'outfit': outfit}
        if state:
            variant['state'] = state
        assets.append({'id': f'character-{i}', 'category': '人物', 'name': name,
                       'variant': variant, 'occurrences': [{'scene_id': f's{i}',
                       'evidence': [{'line': line+1, 'quote': action}]}]})
    raw = {'asset_workflow': 'codex', 'region_mode': 'overseas', 'script_text': '\n'.join(lines), 'asset_types': ['人物'],
           'output_mode': 'workbook' if design_turnarounds else 'assets',
           'generate_turnarounds': turnarounds, 'output_root': str(folder / 'delivery'),
           'selected_assets': SELECTION if selected_assets is None else selected_assets}
    index = folder / 'index.json'
    atomic_json(index, build_index(raw, {'source_sha256': script_hash(raw), 'scenes': scenes, 'assets': assets}))
    raw['content_index'] = str(index)
    request = folder / 'request.json'
    atomic_json(request, raw)
    resolved = load_and_validate(request, require_content=True)['resolved_assets']
    plan = folder / 'plan.json'
    atomic_json(plan, {'assets': [{'id': item['id'],
                  'prompt': f"{item['name']}，{item['variant']['outfit']}，"
                            f"{item['variant'].get('state', '正常状态')}，"
                            f"{'三视图' if item['kind'] == 'turnaround' else '半身原照'}，"
                            f"第{item['sample']}种候选造型，真实材质，五官清晰。"}
                 for item in resolved if design_turnarounds or item['kind'] != 'turnaround']})
    state = folder / 'state.json'
    asset_state.initialize(state, request, plan, partial=not design_turnarounds)
    return state


class CharacterWorkbookTests(unittest.TestCase):
    def test_role_outfit_state_order_preserves_originals_and_excludes_turnarounds(self):
        with tempfile.TemporaryDirectory(prefix='character-workbook-') as folder:
            state_path = create_fixture(folder)
            saved = read_json(state_path)
            data = build_payload(state_path)
            self.assertEqual([s['name'] for s in data['sheets']], ['人物资产'])
            sheet = data['sheets'][0]
            expected = [('character-0', 'original', 1), ('character-0', 'original', 2),
                        ('character-4', 'original', 1), ('character-2', 'original', 1),
                        ('character-1', 'original', 1), ('character-1', 'original', 2),
                        ('character-3', 'original', 1)]
            self.assertEqual([(saved['assets'][i]['catalog_id'], saved['assets'][i]['kind'],
                               saved['assets'][i]['sample']) for i in sheet['asset_ids']], expected)
            self.assertEqual(set(sheet['asset_ids']),
                             {i for i, item in saved['assets'].items() if item['kind'] != 'turnaround'})
            self.assertEqual(sum(item['kind'] == 'turnaround' for item in saved['assets'].values()), 7)
            for row, identifier in zip(sheet['rows'], sheet['asset_ids'], strict=True):
                item = saved['assets'][identifier]
                self.assertEqual(row[4], item['prompt'])
                scene_number = int(item['occurrences'][0]['scene_id'][1:])+1
                self.assertEqual(row[2:4], [f'第1集·1-{scene_number}', f'房间{scene_number}'])
                self.assertFalse(any('三视图' in str(value) for value in row))
                if item['variant'].get('state'):
                    self.assertIn(item['variant']['state'], row[1])
            self.assertEqual(build_payload(state_path, previous_payload=data)['content_hash'], data['content_hash'])
            self.assertEqual(read_json(state_path), saved)

    def test_pending_turnarounds_do_not_block_table_or_disappear_from_image_progress(self):
        with tempfile.TemporaryDirectory(prefix='character-workbook-') as folder:
            state_path = create_fixture(folder, design_turnarounds=False)
            saved = read_json(state_path)
            turns = {i for i, item in saved['assets'].items() if item['kind'] == 'turnaround'}
            self.assertTrue(turns)
            self.assertTrue(all(saved['assets'][i]['prompt'] is None for i in turns))
            data = build_payload(state_path)
            self.assertEqual(len(data['sheets'][0]['rows']), 7)
            self.assertTrue(turns.isdisjoint(data['sheets'][0]['asset_ids']))
            self.assertEqual({x['id'] for x in data['unresolved_images']}, set(saved['assets']))
            self.assertEqual(data['legacy_prompt_verification_required'], [])
            self.assertEqual(read_json(state_path), saved)

    def test_selection_without_turnarounds_keeps_only_requested_outfit(self):
        with tempfile.TemporaryDirectory(prefix='character-workbook-') as folder:
            state = create_fixture(folder, {'人物': [{'name': '艾玛', 'outfit': '蓝色外套', 'count': 2}]}, False)
            sheet = build_payload(state)['sheets'][0]
            self.assertEqual([row[:2] for row in sheet['rows']], [['艾玛', '蓝色外套']]*2)
            self.assertEqual(len(sheet['asset_ids']), 2)


if __name__ == '__main__':
    unittest.main()
