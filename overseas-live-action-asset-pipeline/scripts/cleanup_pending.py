"""Remove verified redundant pending files while protecting actual references."""
from __future__ import annotations
import argparse
from pathlib import Path
from asset_state import (current_attempt, locked, read_json, require_current_request, verified_file)
from pipeline_common import emit, file_hash, io_operation


def path_key(path):
    return str(Path(path).resolve()).casefold()


def protected_reference_paths(state):
    protected = set()
    for item in state['assets'].values():
        records = list(item.get('reference_paths', []))
        if item.get('reference_image'):
            records.append(item['reference_image'])
        if item.get('generation'):
            records.extend((current_attempt(item) or {}).get('references', []))
        protected.update(path_key(r['path']) for r in records if r.get('path'))
    return protected


@io_operation
def cleanup_pending(state_path, identifier, include_history=True):
    result = {'ok': True, 'id': identifier, 'removed': [], 'kept': [], 'errors': []}
    # Same lock as init/record: no reference can be added during this deletion.
    with locked(state_path) as path:
        state = read_json(path)
        require_current_request(state)
        item = state['assets'][identifier]
        saved = item.get('saved', {})
        if (state.get('asset_workflow') != 'jumeng' or not state['save_assets']
                or item.get('generation') or item.get('review', {}).get('approved') is not True
                or not verified_file(saved) or item['review'].get('sha256') != saved.get('sha256')):
            raise ValueError('仅剧梦已审核且正式归档核验成功的资产可清理待审副本')
        root = Path(state['output_root']).resolve()
        pending_root = (root / '待审').resolve()
        if not pending_root.is_relative_to(root) or pending_root == root:
            raise ValueError('待审目录解析后不在本次主路径内')
        protected = protected_reference_paths(state)
        # Never remove a file serving as another asset's current source/output.
        for other_id, other in state['assets'].items():
            if other_id != identifier:
                protected.update(path_key(other[k]['path']) for k in ('source', 'pending', 'saved')
                                 if other.get(k, {}).get('path'))
        records = [item['pending']] if item.get('pending') else []
        if include_history:
            records.extend(a['pending'] for a in item.get('attempts', []) if a.get('pending'))
        grouped = {}
        for record in records:
            key = path_key(record['path'])
            grouped.setdefault(key, []).append(record)
        for key, records_for_path in grouped.items():
            raw = Path(records_for_path[0]['path'])
            candidate = raw.resolve()
            entry = {'path': str(raw)}
            reason = None
            if (not raw.is_absolute() or raw.is_symlink() or candidate == Path(saved['path']).resolve()
                    or not candidate.is_relative_to(pending_root) or candidate == pending_root):
                reason = 'outside_pending_or_formal'
            elif key in protected:
                reason = 'protected_reference'
            elif not candidate.is_file():
                reason = 'missing'
            elif len({r['sha256'] for r in records_for_path}) != 1:
                reason = 'conflicting_history'
            else:
                try:
                    if file_hash(candidate) != records_for_path[0]['sha256']:
                        reason = 'hash_changed'
                    else:
                        raw.unlink()
                        result['removed'].append(str(raw))
                except (OSError, ValueError) as exc:
                    result['errors'].append({**entry, 'reason': str(exc)})
            if reason:
                result['kept'].append({**entry, 'reason': reason})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state')
    parser.add_argument('--id')
    parser.add_argument('--check-reference', help='Read-only protection check; does not authorize deletion')
    args = parser.parse_args()
    try:
        if args.check_reference:
            with locked(args.state) as path:
                state = read_json(path)
                emit({'ok': True, 'path': str(Path(args.check_reference).resolve()),
                      'protected': path_key(args.check_reference) in protected_reference_paths(state),
                      'deletion_authorized': False})
        else:
            if not args.id:
                raise ValueError('清理须指定 --id')
            emit(cleanup_pending(args.state, args.id))
        return 0
    except Exception as exc:
        emit({'ok': False, 'errors': [str(exc)]})
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
