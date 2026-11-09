"""Regression checks for pending copies and operation-local I/O reuse."""
from pathlib import Path
import unittest
from unittest.mock import patch
from PIL import Image
import asset_state as state
import finish_asset
import image_check
import pipeline_common as common
import receive_asset
import save_one_asset
import test_pipeline as fixtures


class SaveOptimizationTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    asset = fixtures.PipelineTests.asset
    prepare = fixtures.PipelineTests.prepare
    init = fixtures.PipelineTests.init
    event = fixtures.PipelineTests.event
    picture = fixtures.PipelineTests.picture

    def start(self, save=True, count=1):
        changes = {'selected_assets': {'人物': [{'name': '艾玛', 'count': count}]}}
        if save:
            changes['output_root'] = str(self.folder / 'archive')
        ids = [x['id'] for x in self.init(**changes)]
        self.event(ids[0], 'generating', attempt_id='a1', handle='test-only')
        return ids

    def receive(self, ident, path=None):
        return receive_asset.receive(self.state_path, ident, 'a1', str(path) if path else None)

    def approve(self, ident):
        return finish_asset.finish(self.state_path, ident, 'a1',
            {'approved': True, 'single_person': True, 'note': 'Synthetic test fixture'})

    def test_pending_is_saved_before_review_and_not_an_approved_asset(self):
        ident = self.start()[0]
        result = self.receive(ident, self.picture())
        self.assertEqual(result['status'], 'pending_saved')
        self.assertTrue(Path(result['target_path']).is_relative_to(self.folder / 'archive' / '待审'))
        item = common.read_json(self.state_path)['assets'][ident]
        self.assertNotIn('saved', item)
        self.assertNotIn('review', item)
        self.assertEqual(result['next_action'], 'inspect')
        with self.assertRaises(ValueError):
            finish_asset.finish(self.state_path, ident, 'a1')
        self.approve(ident)
        with self.assertRaisesRegex(ValueError, '待审副本'):
            self.event(ident, 'saved', attempt_id='a1', target_path=result['target_path'])
        timing = state.metrics(self.state_path)['timings'][0]
        for key in ('pending_copy_seconds', 'archive_copy_seconds', 'geometry_seconds'):
            self.assertGreaterEqual(timing[key], 0)

    def test_duplicate_receive_reuses_one_copy_and_rejects_wrong_attempt(self):
        ident = self.start()[0]
        source = self.picture()
        first = self.receive(ident, source)
        second = self.receive(ident, source)
        self.assertEqual(first['target_path'], second['target_path'])
        self.assertEqual(second['status'], 'pending_reused')
        self.assertEqual(len(list((self.folder / 'archive' / '待审').rglob('*.png'))), 1)
        with self.assertRaises(ValueError):
            receive_asset.receive(self.state_path, ident, 'wrong-attempt', str(source))

    def test_staged_source_survives_original_disappearance(self):
        ident = self.start()[0]
        source = self.picture()
        pending = self.receive(ident, source)
        source.unlink()
        finished = self.approve(ident)
        self.assertEqual(Path(finished['target_path']).read_bytes(), Path(pending['target_path']).read_bytes())
        self.assertEqual(finished['next_action'], 'deliver')

    def test_failed_staging_can_retry_without_new_generation(self):
        ident = self.start()[0]
        with patch('receive_asset.stage_source', side_effect=PermissionError('test denial')):
            failed = self.receive(ident, self.picture())
        self.assertFalse(failed['ok'])
        self.assertTrue(failed['received'])
        self.assertEqual(failed['status'], 'pending_save_failed')
        self.assertTrue(self.receive(ident)['ok'])
        item = common.read_json(self.state_path)['assets'][ident]
        self.assertEqual(len(item['attempts']), 1)
        self.assertNotIn('failure', item)

    def test_no_path_creates_no_pending_directory(self):
        ident = self.start(save=False)[0]
        result = self.receive(ident, self.picture())
        self.assertEqual(result['status'], 'received_not_archived')
        self.assertEqual(list(self.folder.rglob('待审')), [])

    def test_corrupted_pending_is_preserved_and_replaced_with_new_version(self):
        ident = self.start()[0]
        source = self.picture()
        first = self.receive(ident, source)
        old_target = Path(first['target_path'])
        old_target.write_bytes(b'changed by another writer')
        second = self.receive(ident)
        self.assertNotEqual(first['target_path'], second['target_path'])
        self.assertEqual(old_target.read_bytes(), b'changed by another writer')
        self.assertEqual(Path(second['target_path']).read_bytes(), source.read_bytes())

    def test_changed_destination_requires_reinitialization_before_staging(self):
        ident = self.start()[0]
        raw = common.read_json(self.request_path)
        raw['output_root'] = str(self.folder / 'new-archive')
        common.atomic_json(self.request_path, raw)
        result = self.receive(ident, self.picture())
        self.assertEqual(result['status'], 'pending_save_failed')
        self.assertTrue(result['received'])
        self.assertFalse((self.folder / 'archive').exists())
        state.initialize(self.state_path, self.request_path, self.plan_path)
        retry = self.receive(ident)
        self.assertTrue(Path(retry['target_path']).is_relative_to(self.folder / 'new-archive'))

    def test_pending_wrong_geometry_is_preserved_but_not_approved(self):
        ident = self.start()[0]
        pending = self.receive(ident, self.picture(size=(512, 512)))
        self.assertTrue(Path(pending['target_path']).is_file())
        with self.assertRaisesRegex(ValueError, '比例不符'):
            self.approve(ident)
        self.assertNotIn('saved', common.read_json(self.state_path)['assets'][ident])

    def test_pending_cannot_unlock_a_dependent_image(self):
        ids = self.start(count=2)
        self.receive(ids[0], self.picture())
        plan = common.read_json(self.plan_path)
        plan['assets'][1]['reference_ids'] = [ids[0]]
        common.atomic_json(self.plan_path, plan)
        state.initialize(self.state_path, self.request_path, self.plan_path)
        current = common.read_json(self.state_path)
        self.assertEqual(state.next_action(current, current['assets'][ids[1]]), 'wait_for_reference')

    def test_hash_cache_reuses_reads_but_detects_changes_and_expires(self):
        path = self.folder / 'data.bin'
        path.write_bytes(b'first')
        with patch('pipeline_common._read_file_hash', wraps=common._read_file_hash) as reads:
            with common.io_session():
                old = common.file_hash(path)
                self.assertEqual(common.file_hash(path), old)
                self.assertEqual(reads.call_count, 1)
                path.write_bytes(b'new different bytes')
                self.assertNotEqual(common.file_hash(path), old)
                self.assertEqual(reads.call_count, 2)
            with common.io_session():
                common.file_hash(path)
            self.assertEqual(reads.call_count, 3)

    def test_geometry_cache_invalidates_on_file_and_profile_changes(self):
        path = self.picture()
        with common.io_session():
            good = image_check.inspect_image(path, '人物')
            self.assertEqual(image_check.inspect_image(path, '人物'), good)
            with self.assertRaisesRegex(ValueError, '尺寸不足'):
                image_check.inspect_image(path, '人物', min_short_side=2048)
            Image.new('RGB', (512, 512)).save(path)
            with self.assertRaisesRegex(ValueError, '比例不符'):
                image_check.inspect_image(path, '人物')

    def test_changed_source_during_copy_is_rejected_even_with_cache(self):
        ident = self.start()[0]
        path = self.picture()
        self.event(ident, 'generated', attempt_id='a1', source_path=str(path))
        original_copy = save_one_asset.copy_and_digest
        def change_after_copy(stream, output):
            value = original_copy(stream, output)
            path.write_bytes(path.read_bytes() + b'changed')
            return value
        with patch('save_one_asset.copy_and_digest', side_effect=change_after_copy):
            with self.assertRaisesRegex(ValueError, '源图片发生变化'):
                self.approve(ident)
        self.assertEqual(list((self.folder / 'archive').rglob('*.png')), [])

    def test_partial_queue_does_not_claim_global_counts(self):
        ids = self.start(count=5)
        result = state.snapshot(self.state_path, limit=1, summarize=False)
        self.assertEqual(result['scanned_assets'], 1)
        self.assertFalse(result['counts_complete'])
        full = state.snapshot(self.state_path, limit=1)
        self.assertEqual(full['scanned_assets'], len(ids))
        self.assertTrue(full['counts_complete'])


if __name__ == '__main__':
    unittest.main()
