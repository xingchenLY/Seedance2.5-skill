"""Offline checks for operation caches, shared queue reads and pending promotion."""
from pathlib import Path
import json
import subprocess
import sys
import unittest
from unittest.mock import patch
import asset_state as state
import finish_asset
import input_check
import pipeline_common as common
import receive_asset
import save_one_asset
import test_save_optimization as fixtures


class DeduplicatePipelineTests(unittest.TestCase):
    setUp = fixtures.SaveOptimizationTests.setUp
    asset = fixtures.SaveOptimizationTests.asset
    prepare = fixtures.SaveOptimizationTests.prepare
    init = fixtures.SaveOptimizationTests.init
    event = fixtures.SaveOptimizationTests.event
    picture = fixtures.SaveOptimizationTests.picture
    start = fixtures.SaveOptimizationTests.start
    receive = fixtures.SaveOptimizationTests.receive
    approve = fixtures.SaveOptimizationTests.approve

    def jumeng(self, count=1):
        self.raw.update(asset_workflow='jumeng', jumeng_project_name='Offline test',
                        jumeng_operation_mode='cli')
        return self.start(count=count)

    def test_finish_validates_request_once_but_commits_both_events(self):
        ident = self.start()[0]
        self.receive(ident, self.picture())
        revision = common.read_json(self.state_path)['revision']
        with patch('input_check.validate_request', wraps=input_check.validate_request) as validate:
            result = self.approve(ident)
        self.assertEqual(validate.call_count, 1)
        after = common.read_json(self.state_path)
        self.assertEqual(after['revision'], revision + 2)
        self.assertTrue(after['assets'][ident]['review']['approved'])
        self.assertTrue(Path(result['target_path']).is_file())

    def test_validation_cache_has_no_mutable_alias_and_expires(self):
        self.start()
        with patch('input_check.validate_request', wraps=input_check.validate_request) as validate:
            with common.io_session():
                first = input_check.load_and_validate(self.request_path)
                first['resolved_assets'].clear()
                self.assertTrue(input_check.load_and_validate(self.request_path)['resolved_assets'])
                self.assertEqual(validate.call_count, 1)
            input_check.load_and_validate(self.request_path)
            self.assertEqual(validate.call_count, 2)

    def test_validation_cache_rejects_changed_request_and_index(self):
        self.start()
        with common.io_session():
            input_check.load_and_validate(self.request_path)
            data = common.read_json(self.request_path)
            data['region_mode'] = 'invalid'
            common.atomic_json(self.request_path, data)
            with self.assertRaises(input_check.RequestError):
                input_check.load_and_validate(self.request_path)
            data['region_mode'] = 'overseas'
            common.atomic_json(self.request_path, data)
            input_check.load_and_validate(self.request_path)
            index = common.read_json(self.index_path)
            index['assets'][0]['name'] = 'Changed without reindex'
            common.atomic_json(self.index_path, index)
            with self.assertRaises(input_check.RequestError):
                input_check.load_and_validate(self.request_path)

    def test_validation_cache_rejects_changed_script_dependency(self):
        script = self.folder / 'script.txt'
        script.write_bytes(self.raw.pop('script_text').encode('utf-8'))
        self.raw['script_path'] = str(script)
        self.start()
        with common.io_session():
            input_check.load_and_validate(self.request_path)
            script.write_text(script.read_text(encoding='utf-8') + '\nChanged', encoding='utf-8')
            with self.assertRaises(input_check.RequestError):
                input_check.load_and_validate(self.request_path)

    def test_request_changed_just_after_read_cannot_poison_cache(self):
        self.start()
        original = common.read_json
        def changed(path):
            value = original(path)
            altered = dict(value, region_mode='invalid')
            common.atomic_json(path, altered)
            return value
        with common.io_session(), patch('input_check.read_json', side_effect=changed):
            with self.assertRaisesRegex(input_check.RequestError, '读取期间'):
                input_check.load_and_validate(self.request_path)

    def test_json_cache_isolated_and_invalidated_by_commit(self):
        path = self.folder / 'data.json'
        common.atomic_json(path, {'items': [1]})
        original = Path.read_text
        calls = []
        def read(p, *args, **kwargs):
            calls.append(p)
            return original(p, *args, **kwargs)
        with common.io_session(), patch.object(Path, 'read_text', read):
            first = common.read_json(path)
            first['items'].append(2)
            self.assertEqual(common.read_json(path), {'items': [1]})
            self.assertEqual(len(calls), 1)
            common.atomic_json(path, {'items': [3]})
            self.assertEqual(common.read_json(path), {'items': [3]})
            self.assertEqual(len(calls), 2)

    def test_two_lanes_have_independent_limits_and_legacy_api_still_works(self):
        ids = self.start(count=5)
        self.receive(ids[0], self.picture())
        both = state.snapshot(self.state_path, limit=1, summarize=False, lanes=True)
        self.assertEqual([x['id'] for x in both['queues']['postprocess']], [ids[0]])
        self.assertEqual([x['id'] for x in both['queues']['generate']], [ids[1]])
        self.assertFalse(both['counts_complete'])
        self.assertEqual(both['scanned_assets'], 2)
        self.assertNotIn('prompt', both['queues']['generate'][0])
        legacy = state.snapshot(self.state_path, actions=['generate'], limit=1, include_prompt=True)
        self.assertEqual(legacy['assets'][0]['id'], ids[1])
        self.assertIn('prompt', legacy['assets'][0])
        with self.assertRaises(ValueError):
            state.snapshot(self.state_path, lanes=True, include_prompt=True)

    def test_lanes_do_not_treat_an_empty_lane_as_a_full_queue(self):
        ids = self.start(count=5)
        both = state.snapshot(self.state_path, limit=1, summarize=False, lanes=True)
        self.assertEqual(both['queues']['postprocess'], [])
        self.assertEqual(len(both['queues']['generate']), 1)
        self.assertTrue(both['counts_complete'])
        self.assertEqual(both['scanned_assets'], len(ids))
        self.assertEqual(both['counts']['wait'], 1)

    def test_lanes_cli_reports_both_queues(self):
        self.start(count=2)
        result = subprocess.run([sys.executable, '-B', str(Path(state.__file__)),
            'next', str(self.state_path), '--lanes', '--limit', '1', '--no-summary'],
            capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(set(json.loads(result.stdout)['queues']), {'generate', 'postprocess'})

    def test_download_plan_creates_no_file_or_state_and_reconciles_existing(self):
        ident = self.jumeng()[0]
        before = self.state_path.read_bytes()
        planned = receive_asset.prepare_download(self.state_path, ident, 'a1')
        self.assertFalse(planned['received'])
        self.assertFalse(Path(planned['download_path']).exists())
        self.assertEqual(before, self.state_path.read_bytes())
        Path(planned['partial_path']).write_bytes(b'partial transfer')
        existing = receive_asset.prepare_download(self.state_path, ident, 'a1')
        self.assertEqual(existing['status'], 'reconcile_existing')
        with self.assertRaises(ValueError):
            receive_asset.prepare_download(self.state_path, ident, 'other-attempt')

    def test_direct_pending_receive_avoids_copy_and_repeat_download(self):
        ident = self.jumeng()[0]
        planned = receive_asset.prepare_download(self.state_path, ident, 'a1')
        target = Path(planned['download_path'])
        target.write_bytes(self.picture().read_bytes())
        with patch('receive_asset.copy_and_digest', side_effect=AssertionError('unnecessary copy')):
            received = self.receive(ident, target)
        self.assertEqual(received['status'], 'pending_reused')
        self.assertEqual(Path(received['target_path']), target)
        self.assertEqual(received['next_action'], 'inspect')
        again = receive_asset.prepare_download(self.state_path, ident, 'a1')
        self.assertEqual(again['status'], 'already_received')
        with self.assertRaises(ValueError):
            finish_asset.finish(self.state_path, ident, 'a1')

    def test_jumeng_promotes_pending_without_copy_and_cleans_after_commit(self):
        ident = self.jumeng()[0]
        original = self.picture()
        pending = Path(self.receive(ident, original)['target_path'])
        expected = pending.read_bytes()
        with patch('save_one_asset.copy_and_digest', side_effect=AssertionError('unnecessary copy')):
            finished = self.approve(ident)
        self.assertEqual(finished['storage_method'], 'linked_pending')
        self.assertEqual(finished['pending_cleanup'], 'removed_after_archive')
        self.assertFalse(pending.exists())
        self.assertTrue(original.exists())
        self.assertEqual(Path(finished['target_path']).read_bytes(), expected)
        original.unlink()  # Simulate the coordinator's verified download cleanup.
        item = common.read_json(self.state_path)['assets'][ident]
        self.assertEqual(state.image_source(item), finished['target_path'])

    def test_jumeng_link_failure_falls_back_to_verified_copy(self):
        ident = self.jumeng()[0]
        pending = Path(self.receive(ident, self.picture())['target_path'])
        expected = pending.read_bytes()
        with patch('save_one_asset.os.link', side_effect=OSError('unsupported filesystem')):
            finished = self.approve(ident)
        self.assertEqual(Path(finished['target_path']).read_bytes(), expected)
        self.assertFalse(pending.exists())
        self.assertNotEqual(finished.get('storage_method'), 'linked_pending')

    def test_failed_saved_commit_preserves_files_and_retry_reuses_same_target(self):
        ident = self.jumeng()[0]
        pending = Path(self.receive(ident, self.picture())['target_path'])
        record = state.record_event
        def fail_saved(path, event):
            if event['event'] == 'saved':
                raise OSError('simulated state write failure')
            return record(path, event)
        with patch('finish_asset.record_event', side_effect=fail_saved):
            with self.assertRaisesRegex(OSError, 'state write failure'):
                self.approve(ident)
        self.assertTrue(pending.is_file())
        before = list((self.folder / 'archive' / '人物').glob('*.png'))
        self.assertEqual(len(before), 1)
        result = finish_asset.finish(self.state_path, ident, 'a1')
        self.assertEqual(Path(result['target_path']), before[0])
        self.assertEqual(result['status'], 'reused')
        self.assertFalse(pending.exists())
        self.assertEqual(list((self.folder / 'archive' / '人物').glob('*.png')), before)

    def test_jumeng_preserves_rejected_pending(self):
        ident = self.jumeng()[0]
        pending = Path(self.receive(ident, self.picture())['target_path'])
        rejected = finish_asset.finish(self.state_path, ident, 'a1',
            {'approved': False, 'single_person': True, 'note': 'Synthetic rejection'})
        self.assertEqual(rejected['status'], 'rejected')
        self.assertTrue(pending.is_file())
        self.assertFalse((self.folder / 'archive' / '人物').exists())

    def test_jumeng_existing_formal_collision_keeps_old_image(self):
        ident = self.jumeng()[0]
        self.receive(ident, self.picture())
        current = common.read_json(self.state_path)
        folder = self.folder / 'archive' / '人物'
        folder.mkdir(parents=True)
        name = finish_asset.asset_name(current, current['assets'][ident])
        existing = folder / (name + '.png')
        old = self.picture(name='other.png', color='blue').read_bytes()
        existing.write_bytes(old)
        result = self.approve(ident)
        self.assertEqual(Path(result['target_path']).name, name + '-v2.png')
        self.assertEqual(existing.read_bytes(), old)

    def test_pending_cleanup_failure_does_not_undo_archive(self):
        ident = self.jumeng()[0]
        pending = Path(self.receive(ident, self.picture())['target_path'])
        unlink = Path.unlink
        def blocked(path, *args, **kwargs):
            if path == pending:
                raise PermissionError('pending file temporarily occupied')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', blocked):
            result = self.approve(ident)
        self.assertEqual(result['pending_cleanup'], 'pending')
        self.assertTrue(pending.is_file())
        self.assertTrue(Path(result['target_path']).is_file())
        retried = finish_asset.finish(self.state_path, ident, 'a1')
        self.assertEqual(retried['status'], 'reused')
        self.assertFalse(pending.exists())

    def test_link_change_is_rejected_and_only_new_target_is_removed(self):
        ident = self.jumeng()[0]
        pending = Path(self.receive(ident, self.picture())['target_path'])
        link = save_one_asset.os.link
        def changed(source, target):
            link(source, target)
            source.write_bytes(b'changed after review')
        with patch('save_one_asset.os.link', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '待审图片发生变化'):
                self.approve(ident)
        self.assertTrue(pending.is_file())
        self.assertEqual(list((self.folder / 'archive' / '人物').glob('*.png')), [])


if __name__ == '__main__':
    unittest.main()
