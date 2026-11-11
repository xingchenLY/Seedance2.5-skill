"""Offline production regressions for shared repair budgets and reference cleanup."""
from pathlib import Path
from copy import deepcopy
import unittest
import asset_state as state
import cleanup_pending
import finish_asset
import receive_asset
from pipeline_common import atomic_json, read_json
import test_pipeline as fixtures


class RepairExecutionTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    asset = fixtures.PipelineTests.asset
    prepare = fixtures.PipelineTests.prepare
    init = fixtures.PipelineTests.init
    event = fixtures.PipelineTests.event
    picture = fixtures.PipelineTests.picture
    actions = fixtures.PipelineTests.actions

    def one(self, **changes):
        return self.init(selected_assets={'人物': [{'name': '艾玛', 'count': 1}]}, **changes)[0]['id']

    def reject(self, ident, number):
        self.event(ident, 'generating', attempt_id=f'a{number}', handle=f'offline-{number}')
        if number % 2:
            self.event(ident, 'failed', stage='generation', terminal=True,
                       code='service', message='Synthetic generation failure')
        else:
            self.event(ident, 'generated', source_path=str(self.picture(f'a{number}.png')))
            self.event(ident, 'checked', approved=False, code='detail', note='Synthetic visual defect')

    def test_alternating_stages_share_six_attempt_limit_and_do_not_reset_on_init(self):
        ident = self.one()
        for n in range(1, 7):
            self.reject(ident, n)
            item = read_json(self.state_path)['assets'][ident]
            self.assertEqual(state.repair_failure_count(item), n)
        self.assertEqual(self.actions()[ident], 'paused')
        with self.assertRaises(ValueError):
            self.event(ident, 'generating', attempt_id='a7', handle='must-not-submit')
        plan = read_json(self.plan_path)
        plan['assets'][0]['prompt'] += ' corrected composition'
        atomic_json(self.plan_path, plan)
        state.initialize(self.state_path, self.request_path, self.plan_path)
        self.assertEqual(self.actions()[ident], 'paused')

    def test_retry_requires_user_authorization_and_preserves_checkpoint(self):
        ident = self.one()
        for n in range(1, 7):
            self.reject(ident, n)
        with self.assertRaisesRegex(ValueError, '用户明确授权'):
            self.event(ident, 'retry', note='Agent changed the wording')
        self.event(ident, 'retry', note='User explicitly approved another repair cycle', user_authorized=True)
        plan = read_json(self.plan_path)
        plan['assets'][0]['prompt'] += ' approved correction'
        atomic_json(self.plan_path, plan)
        state.initialize(self.state_path, self.request_path, self.plan_path)
        self.reject(ident, 7)
        item = read_json(self.state_path)['assets'][ident]
        self.assertEqual(state.repair_failure_count(item), 1)
        self.assertEqual(len(item['attempts']), 7)
        self.assertEqual(item['retry_checkpoint']['attempt_count'], 6)

    def test_success_resets_cycle_and_save_failure_does_not_use_repairs(self):
        ident = self.one(output_root=str(self.folder / 'archive'))
        self.reject(ident, 1)
        self.event(ident, 'generating', attempt_id='a2', handle='success')
        self.event(ident, 'generated', source_path=str(self.picture()))
        self.event(ident, 'checked', approved=True, single_person=True, note='Synthetic accepted image')
        self.event(ident, 'failed', stage='save', code='disk', message='Synthetic save failure')
        self.assertEqual(state.repair_failure_count(read_json(self.state_path)['assets'][ident]), 0)
        plan = read_json(self.plan_path)
        plan['assets'][0]['prompt'] += ' user requested local adjustment'
        atomic_json(self.plan_path, plan)
        state.initialize(self.state_path, self.request_path, self.plan_path)
        self.reject(ident, 3)
        self.assertEqual(state.repair_failure_count(read_json(self.state_path)['assets'][ident]), 1)

    def test_legacy_alternating_history_recovers_budget_and_one_attempt_counts_once(self):
        ident = self.one()
        for n in range(1, 7):
            self.reject(ident, n)
        self.event(ident, 'failed', stage='quality', code='second_defect', message='Same returned image')
        data = read_json(self.state_path)
        item = data['assets'][ident]
        self.assertEqual(state.repair_failure_count(item), 6)
        item.pop('repair_failures')
        item['failure']['count'] = 1  # Historical per-stage implementation.
        atomic_json(self.state_path, data)
        self.assertEqual(self.actions()[ident], 'paused')

    def scene(self):
        return self.init(selected_assets={'场景': ['医院']}, output_root=str(self.folder / 'archive'),
                         asset_workflow='jumeng', jumeng_project_name='Offline fixture',
                         jumeng_operation_mode='cli')[0]['id']

    def scene_result(self, ident, attempt, color='red'):
        self.event(ident, 'generating', attempt_id=attempt, handle='offline-' + attempt)
        source = self.picture(attempt + '.png', size=(1024, 576), color=color)
        return Path(receive_asset.receive(self.state_path, ident, attempt, str(source))['target_path'])

    def edited_scene(self):
        ident = self.scene()
        original = self.scene_result(ident, 'a1')
        finish_asset.finish(self.state_path, ident, 'a1', {'approved': False, 'note': 'Only a small extra object'})
        plan = read_json(self.plan_path)
        plan['assets'][0].update(prompt='Full corrected scene design', reference_paths=[str(original)])
        atomic_json(self.plan_path, plan)
        state.initialize(self.state_path, self.request_path, self.plan_path)
        self.scene_result(ident, 'a2', 'blue')
        result = finish_asset.finish(self.state_path, ident, 'a2',
                                    {'approved': True, 'note': 'Synthetic edit reviewed'}, displayed=True)
        return ident, original, result

    def test_history_cleanup_keeps_edit_reference_and_formal_result(self):
        ident, original, result = self.edited_scene()
        cleaned = cleanup_pending.cleanup_pending(self.state_path, ident)
        self.assertTrue(original.is_file())
        self.assertTrue(Path(result['target_path']).is_file())
        self.assertIn({'path': str(original), 'reason': 'protected_reference'}, cleaned['kept'])
        self.assertEqual(self.actions()[ident], 'complete')

    def test_missing_historical_input_does_not_block_archived_result_or_unchanged_init(self):
        ident, original, _ = self.edited_scene()
        self.assertTrue(original.resolve().is_relative_to(self.folder.resolve()))
        original.unlink()  # Simulate external deletion, not approved cleanup.
        self.assertEqual(self.actions()[ident], 'complete')
        state.initialize(self.state_path, self.request_path, self.plan_path)
        self.assertEqual(self.actions()[ident], 'complete')
        plan = read_json(self.plan_path)
        plan['assets'][0]['prompt'] += ' a new requested edit'
        atomic_json(self.plan_path, plan)
        with self.assertRaisesRegex(ValueError, '参考图已缺失'):
            state.initialize(self.state_path, self.request_path, self.plan_path)

    def test_changed_reference_is_not_excused_as_missing_history(self):
        ident, original, _ = self.edited_scene()
        original.write_bytes(b'changed external reference')
        self.assertEqual(self.actions()[ident], 'wait_for_reference')

    def test_cleanup_rejects_unarchived_or_changed_history_and_outside_paths(self):
        ident = self.scene()
        original = self.scene_result(ident, 'a1')
        with self.assertRaises(ValueError):
            cleanup_pending.cleanup_pending(self.state_path, ident)
        finish_asset.finish(self.state_path, ident, 'a1', {'approved': False, 'note': 'Synthetic rejection'})
        self.scene_result(ident, 'a2', 'blue')
        finished = finish_asset.finish(self.state_path, ident, 'a2', {'approved': True, 'note': 'Accepted'})
        original.write_bytes(b'modified pending contents')
        outside = self.picture('outside.png')
        data = read_json(self.state_path)
        record = deepcopy(data['assets'][ident]['attempts'][0]['pending'])
        record['path'] = str(outside)
        data['assets'][ident]['attempts'].append({'id': 'legacy-outside', 'pending': record})
        atomic_json(self.state_path, data)
        cleaned = cleanup_pending.cleanup_pending(self.state_path, ident)
        self.assertEqual({x['reason'] for x in cleaned['kept']}, {'missing', 'hash_changed', 'outside_pending_or_formal'})
        self.assertTrue(original.is_file())
        self.assertTrue(outside.is_file())
        self.assertTrue(Path(finished['target_path']).is_file())

    def test_automatic_cleanup_protects_another_current_reference(self):
        ident = self.scene()
        pending = self.scene_result(ident, 'a1')
        data = read_json(self.state_path)
        data['assets']['other'] = {'reference_paths': [deepcopy(data['assets'][ident]['pending'])]}
        atomic_json(self.state_path, data)
        result = finish_asset.finish(self.state_path, ident, 'a1', {'approved': True, 'note': 'Accepted'})
        self.assertEqual(result['pending_cleanup'], 'protected_reference')
        self.assertTrue(pending.exists())


if __name__ == '__main__':
    unittest.main()
