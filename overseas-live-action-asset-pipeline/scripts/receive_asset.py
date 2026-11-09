"""Bind one returned image and preserve an unreviewed local copy immediately."""
from __future__ import annotations
import argparse
import itertools
import os
from pathlib import Path
from time import perf_counter

from asset_state import (next_action, read_json, record_event, require_current_request,
                         verified_file, image_enabled)
from finish_asset import asset_name
from pipeline_common import emit, file_hash, io_operation, object_hash
from save_one_asset import (EXTENSIONS, copy_and_digest, inside, require_unchanged,
                            safe_stem, source_stamp)


def stage_source(state, item):
    """Copy bytes, not visual approval or geometry validation; never overwrite."""
    started = perf_counter()
    source = Path(item['source']['path'])
    stamp = source_stamp(source)
    expected = item['source']['sha256']
    root = Path(state['output_root']).resolve()
    pending_root = inside(root / '待审', root)
    folder = inside(pending_root / item['category'], root)
    folder.mkdir(parents=True, exist_ok=True)
    folder = inside(folder, root)
    suffix = source.suffix.lower()
    if suffix not in set(EXTENSIONS.values()) | {'.jpeg', '.tiff'}:
        suffix = '.bin'
    identity = object_hash([item['id'], item.get('current_attempt_id')])[:16]
    stem = safe_stem(asset_name(state, item)) + '-' + identity
    for version in itertools.count(1):
        ending = '' if version == 1 else f'-v{version}'
        target = inside(folder / (stem + ending + suffix), root)
        if target.exists():
            if target.is_file() and file_hash(target) == expected:
                return {'status': 'pending_reused', 'target_path': str(target), 'sha256': expected,
                        'timing': {'copy_seconds': 0.0, 'verify_seconds': 0.0,
                                   'save_seconds': perf_counter() - started}}
            continue
        try:
            fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        except FileExistsError:
            continue
        try:
            copy_started = perf_counter()
            with os.fdopen(fd, 'wb') as output, source.open('rb') as input_stream:
                copied_hash = copy_and_digest(input_stream, output)
            copy_seconds = perf_counter() - copy_started
            verify_started = perf_counter()
            require_unchanged(source, stamp)
            if copied_hash != expected or file_hash(target, refresh=True) != expected:
                raise ValueError('待审复制内容与本次返回源图不一致')
            return {'status': 'pending_saved', 'target_path': str(target), 'sha256': expected,
                    'timing': {'copy_seconds': copy_seconds,
                               'verify_seconds': perf_counter() - verify_started,
                               'save_seconds': perf_counter() - started}}
        except Exception:
            own_target = inside(target, root)
            if own_target.is_file():
                own_target.unlink()
            raise


@io_operation
def receive(state_path, identifier, attempt_id, source_path=None):
    base = {'id': identifier, 'attempt_id': attempt_id}
    if source_path is not None:
        # Persist tool provenance before attempting any target-directory writes.
        record_event(state_path, {**base, 'event': 'generated', 'source_path': str(source_path)})
    state = read_json(state_path)
    item = state['assets'][identifier]
    if item.get('current_attempt_id') != attempt_id or not item.get('source'):
        raise ValueError('只能保存当前已接收尝试的待审副本')
    result = {**base, 'ok': True, 'received': True, 'source_path': item['source']['path']}
    if not state['save_assets'] or not image_enabled(state, item):
        return {**result, 'status': 'received_not_archived', 'next_action': next_action(state, item)}
    try:
        require_current_request(state)
        pending = verified_file(item.get('pending'))
        if pending:
            return {**result, 'status': 'pending_reused', 'target_path': pending,
                    'next_action': next_action(state, item)}
        staged = stage_source(state, item)
        record = record_event(state_path, {**base, 'event': 'staged',
            'target_path': staged['target_path'], 'timing': staged['timing']})
        return {**result, **staged, 'next_action': record['next_action'], 'visual_approved': False}
    except Exception as exc:
        # The returned source remains bound; retry this operation, never regenerate.
        return {**result, 'ok': False, 'status': 'pending_save_failed', 'errors': [str(exc)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state')
    parser.add_argument('--id', required=True)
    parser.add_argument('--attempt-id', required=True)
    parser.add_argument('--source', help='Actual tool result path; omit only to retry staging')
    args = parser.parse_args()
    try:
        result = receive(args.state, args.id, args.attempt_id, args.source)
        emit(result)
        return 0 if result['ok'] else 2
    except Exception as exc:
        emit({'ok': False, 'errors': [str(exc)]})
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
