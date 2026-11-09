"""Scene grouping regressions using a real indexed script and authoritative state."""
from pathlib import Path
import tempfile
import unittest

import asset_state
from pipeline_common import atomic_json, read_json, script_hash
from script_index import build_index
from input_check import load_and_validate
from workbook_data import build_payload, group_scene_rows


SCENES = [
    ('庄园', '客厅', '夜晚'), ('医院', '卧室', '白天'),
    ('庄园', '卧室', None), ('医院', '走廊', '夜晚'),
    ('庄园', '客厅', '白天'), ('庄园', '客厅', None),
    ('医院', '卧室', '夜晚'), ('庄园', '卧室', '夜晚'),
    ('剧院', None, None), ('剧院', None, '白天'), ('书店', '阅览室', '白天'),
]
ORDER = [0, 4, 5, 2, 7, 1, 6, 3, 8, 9, 10]


def create_fixture(folder, selected_assets=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    lines = ['EPISODE 1']
    scenes, assets = [], []
    for i, (place, room, time) in enumerate(SCENES):
        line = len(lines) + 1
        heading = ' '.join(v for v in (place, room, time) if v)
        lines.extend([heading, '人物离开此处。'])
        scenes.append({'id': f's{i}', 'start_line': line, 'end_line': line+1,
                       'episode': 1, 'label': f'1-{i+1}', 'location': place})
        variant = {k: v for k, v in {'space': room, 'time': time}.items() if v is not None}
        assets.append({'id': f'place-{i}', 'category': '场景', 'name': place, 'variant': variant,
                       'occurrences': [{'scene_id': f's{i}', 'evidence': [{'line': line, 'quote': heading}]}]})
    raw = {'asset_workflow': 'codex', 'region_mode': 'overseas', 'script_text': '\n'.join(lines), 'asset_types': ['场景'], 'output_mode': 'workbook',
           'output_root': str(folder / 'delivery')}
    if selected_assets is not None:
        raw['selected_assets'] = selected_assets
    annotations = {'source_sha256': script_hash(raw), 'scenes': scenes, 'assets': assets}
    index = folder / 'index.json'
    atomic_json(index, build_index(raw, annotations))
    raw['content_index'] = str(index)
    request = folder / 'request.json'
    atomic_json(request, raw)
    resolved = load_and_validate(request, require_content=True)['resolved_assets']
    plan = folder / 'plan.json'
    atomic_json(plan, {'assets': [{'id': item['id'], 'prompt': f"验证场景提示词：{item['id']}。固定布局，真实材质，无人物。"}
                                 for item in resolved]})
    state = folder / 'state.json'
    asset_state.initialize(state, request, plan)
    return state


class SceneWorkbookTests(unittest.TestCase):
    def test_scene_and_room_groups_preserve_chronology_and_row_content(self):
        with tempfile.TemporaryDirectory(prefix='scene-workbook-') as folder:
            state_path = create_fixture(folder)
            saved = read_json(state_path)
            data = build_payload(state_path)
            self.assertEqual([s['name'] for s in data['sheets']], ['场景资产'])
            sheet = data['sheets'][0]
            self.assertEqual([tuple(r[:3]) for r in sheet['rows']], [SCENES[i] for i in ORDER])
            for row, identifier in zip(sheet['rows'], sheet['asset_ids'], strict=True):
                item = saved['assets'][identifier]
                self.assertEqual(row[4], item['prompt'])
                scene_number = int(item['occurrences'][0]['scene_id'][1:]) + 1
                self.assertEqual(row[3], f'第1集·1-{scene_number}')
            self.assertEqual(build_payload(state_path, previous_payload=data)['content_hash'], data['content_hash'])
            self.assertEqual(read_json(state_path), saved)

    def test_empty_rows_and_mismatched_ids(self):
        self.assertEqual(group_scene_rows([], []), ([], []))
        with self.assertRaises(ValueError):
            group_scene_rows([['庄园', '客厅', None, '1-1', '提示词']], [])


if __name__ == '__main__':
    unittest.main()
