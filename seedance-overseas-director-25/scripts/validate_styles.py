"""Validate and resolve the local style registry; read-only, standard library only."""

import argparse
import json
import re
from pathlib import Path


def normalize(value):
    return value.strip().casefold()


def load_catalog(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise ValueError('Expected registry schema_version 1')
    generic = data.get('generic')
    styles = data.get('styles')
    if not isinstance(generic, dict) or not isinstance(styles, list):
        raise ValueError('generic must be an object and styles must be an array')
    index, ids = {}, set()
    for entry in [generic, *styles]:
        if not isinstance(entry, dict):
            raise ValueError('Each style must be an object')
        for key in ('id', 'label'):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise ValueError(f'Missing non-empty {key}')
        style_id = entry['id']
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', style_id):
            raise ValueError(f'Invalid style id: {style_id}')
        if style_id in ids:
            raise ValueError(f'Duplicate style id: {style_id}')
        ids.add(style_id)
        aliases = entry.get('aliases')
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) or not alias.strip() for alias in aliases
        ):
            raise ValueError(f'{style_id}: aliases must be non-empty strings in an array')
        for alias in [style_id, entry['label'], *aliases]:
            key = normalize(alias)
            if key in index and index[key]['id'] != style_id:
                raise ValueError(f'Alias collision: {alias}')
            index[key] = entry
        if entry is generic:
            if entry.get('profile') is not None:
                raise ValueError('Generic mode must not load a work-specific profile')
            continue
        for key in ('profile', 'revision', 'summary'):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise ValueError(f'{style_id}: missing {key}')
        modes = entry.get('character_modes')
        if not isinstance(modes, list) or not modes or any(
            not isinstance(mode, str) or not mode.strip() for mode in modes
        ):
            raise ValueError(f'{style_id}: character_modes must contain strings')
        relative = Path(entry['profile'])
        profile = (path.parent / relative).resolve()
        if (relative.is_absolute() or not profile.is_relative_to(path.parent)
                or profile.suffix != '.md' or not profile.is_file()):
            raise ValueError(f'{style_id}: profile must be an existing .md inside styles/')
        if not profile.read_text(encoding='utf-8-sig').strip():
            raise ValueError(f'{style_id}: empty profile')
    default_id = data.get('default_style_id')
    if not isinstance(default_id, str) or default_id not in ids:
        raise ValueError('default_style_id must be a registered id')
    return data, index


def resolve_style(data, index, choice=None):
    if choice is None:
        choice = data['default_style_id']
    choice = re.sub(r'^\s*风格标签\s*[：:]\s*', '', choice)
    key = normalize(choice)
    if key not in index:
        raise ValueError(f'Unregistered style: {choice}')
    return index[key]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'styles' / 'catalog.json')
    parser.add_argument('--style', help='Resolve a registered id, label, or alias')
    args = parser.parse_args()
    try:
        data, index = load_catalog(args.catalog)
        if args.style is not None:
            print(json.dumps(resolve_style(data, index, args.style), ensure_ascii=False, indent=2))
        else:
            print(f"Style registry valid: {len(data['styles'])} profile(s), default={data['default_style_id']}")
            for entry in [*data['styles'], data['generic']]:
                print(f"{entry['id']} | {entry['label']}")
    except (OSError, ValueError, TypeError) as error:
        print(f'ERROR: {error}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
