"""Verify wardrobe metadata through indexing, state and the actual workbook payload."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import asset_state
from input_check import load_and_validate
from pipeline_common import atomic_json, read_json, script_hash
from script_index import build_index, IndexErrors
from workbook_data import build_payload
import test_pipeline as fixtures


def wardrobe_fixture():
    lines = ['EPISODE 1', '婚礼大厅', '侍者为来宾引路。', '父亲等待仪式开始。',
             '记者在门口记录采访。', '艾玛作为新娘走入婚礼大厅。', '诺亚迎接新娘。',
             '咖啡馆', '诺亚结束婚礼后回到自己经营的咖啡馆。']
    raw = {'region_mode': 'domestic', 'script_text': '\n'.join(lines),
           'asset_types': ['人物'], 'generate_turnarounds': True, 'output_mode': 'workbook'}
    definitions = [
        ('waiter', '侍者', 4, '工作制服', 'other', 3, False, 'group'),
        ('father-formal', '父亲', 2, '婚礼西装', 'occasion', 4, False, 'original'),
        ('reporter', '记者', 3, '职业便装', 'other', 5, False, 'original'),
        ('emma-formal', '艾玛', 1, '婚礼礼服', 'occasion', 6, False, 'original'),
        ('noah-formal', '诺亚', 1, '婚礼三件套', 'occasion', 7, False, 'original'),
        ('noah-daily', '诺亚', 1, '主要常服·针织衫', 'main_daily', 9, False, 'original'),
        ('emma-daily', '艾玛', 1, '主要常服·衬衫长裤', 'main_daily', 6, True, 'original'),
        ('father-daily', '父亲', 2, '主要常服·夹克', 'main_daily', 4, True, 'original'),
    ]
    assets = []
    for ident, name, priority, outfit, role, line, design_only, kind in definitions:
        assets.append({'id': ident, 'category': '人物', 'name': name, 'kind': kind,
                       'role_priority': priority, 'outfit_role': role, 'design_only': design_only,
                       'variant': {'outfit': outfit}, 'occurrences': [{
                           'scene_id': 'daily' if line == 9 else 'wedding',
                           'evidence': [{'line': line, 'quote': lines[line-1]}]}]})
    annotations = {'source_sha256': script_hash(raw), 'assets': assets, 'scenes': [
        {'id': 'wedding', 'start_line': 2, 'end_line': 7, 'label': '1-1', 'location': '婚礼大厅'},
        {'id': 'daily', 'start_line': 8, 'end_line': 9, 'label': '1-2', 'location': '咖啡馆'}]}
    return raw, annotations


class WardrobePriorityTests(unittest.TestCase):
    def make_state(self, folder, selected=None):
        raw, annotations = wardrobe_fixture()
        if selected is not None:
            raw['selected_assets'] = selected
        root = Path(folder)
        index_path, request_path = root/'index.json', root/'request.json'
        atomic_json(index_path, build_index(raw, annotations))
        raw['content_index'] = str(index_path)
        atomic_json(request_path, raw)
        resolved = load_and_validate(request_path, require_content=True)['resolved_assets']
        plan_path, state_path = root/'plan.json', root/'state.json'
        atomic_json(plan_path, {'assets': [{'id': x['id'], 'prompt': x['name']+'穿'+x['variant']['outfit'],
            'preflight': dict(body_basis='正文场合与身份已核对；服装细节为美术补充',
                             continuity='同一角色身份', framing='半身正面', exclusions='无额外人物')}
            for x in resolved]})
        asset_state.initialize(state_path, request_path, plan_path)
        return state_path

    def test_important_roles_and_daily_outfits_first_without_false_occurrences(self):
        with tempfile.TemporaryDirectory() as folder:
            state_path = self.make_state(folder)
            saved = read_json(state_path)
            payload = build_payload(state_path)
            sheet = payload['sheets'][0]
            self.assertEqual([saved['assets'][i]['catalog_id'] for i in sheet['asset_ids']],
                             ['emma-daily', 'emma-formal', 'noah-daily', 'noah-formal',
                              'father-daily', 'father-formal', 'reporter', 'waiter'])
            self.assertEqual(len(sheet['headers']), 5)
            self.assertEqual(sheet['rows'][0][2:4], ['基础定装（美术设计）', ''])
            self.assertEqual(sheet['rows'][1][2:4], ['第1集·1-1', '婚礼大厅'])
            self.assertEqual(sheet['rows'][2][2:4], ['第1集·1-2', '咖啡馆'])
            self.assertTrue(all(saved['assets'][i]['kind'] != 'turnaround' for i in sheet['asset_ids']))
            for item in saved['assets'].values():
                if item['kind'] == 'turnaround':
                    parent = saved['assets'][item['parent_id']]
                    for key in ('role_priority', 'outfit_role', 'design_only'):
                        self.assertEqual(item[key], parent[key])
            self.assertEqual(build_payload(state_path, previous_payload=payload)['content_hash'], payload['content_hash'])

    def test_explicit_occasion_selection_does_not_add_daily_outfit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_state(folder, {'人物': [{'name': '艾玛', 'outfit': '婚礼礼服'}]})
            sheet = build_payload(path)['sheets'][0]
            self.assertEqual(len(sheet['rows']), 1)
            self.assertEqual(sheet['rows'][0][:2], ['艾玛', '婚礼礼服'])

    def test_reject_invalid_or_conflicting_metadata(self):
        raw, original = wardrobe_fixture()
        for field, value in [('role_priority', True), ('role_priority', 5),
                             ('role_priority', '1'), ('outfit_role', 'unknown'),
                             ('design_only', 'false'), ('design_only', True)]:
            annotations = deepcopy(original)
            annotations['assets'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(IndexErrors):
                build_index(raw, annotations)
        annotations = deepcopy(original)
        annotations['assets'][3]['role_priority'] = 2
        with self.assertRaisesRegex(IndexErrors, 'role_priority 不一致'):
            build_index(raw, annotations)
        annotations = deepcopy(original)
        annotations['assets'][3]['outfit_role'] = 'main_daily'
        with self.assertRaisesRegex(IndexErrors, '只能有一套主要常服'):
            build_index(raw, annotations)

    def test_same_daily_outfit_states_remain_grouped(self):
        raw, annotations = wardrobe_fixture()
        wet = deepcopy(annotations['assets'][5])
        wet['id'] = 'noah-daily-wet'
        wet['variant']['state'] = '淋湿'
        annotations['assets'].append(wet)
        index = build_index(raw, annotations)
        states = [x for x in index['assets'] if x['name'] == '诺亚' and x['outfit_role'] == 'main_daily']
        self.assertEqual(len(states), 2)
        self.assertEqual(len({x['variant']['outfit'] for x in states}), 1)
        annotations['assets'][-1]['outfit_role'] = 'other'
        with self.assertRaisesRegex(IndexErrors, '同套服装的用途'):
            build_index(raw, annotations)

    def test_sort_metadata_update_preserves_ids_and_approved_image(self):
        case = fixtures.PipelineTests()
        case.setUp()
        try:
            resolved = case.init()
            ident = resolved[0]['id']
            case.complete_source(ident)
            before = read_json(case.state_path)['assets'][ident]
            for item in case.annotations['assets']:
                if item['name'] == '艾玛':
                    item['role_priority'] = 1
                    item['outfit_role'] = 'main_daily' if item['id'] == 'emma-red' else 'occasion'
            updated = case.init()
            after = read_json(case.state_path)['assets'][ident]
            self.assertEqual([x['id'] for x in resolved], [x['id'] for x in updated])
            for key in ('design_hash', 'source', 'review', 'accepted_attempt_id', 'attempts'):
                self.assertEqual(after[key], before[key])
            self.assertEqual(after['role_priority'], 1)
            self.assertEqual(after['outfit_role'], 'main_daily')
        finally:
            case.doCleanups()


if __name__ == '__main__':
    unittest.main()
