"""Reconcile original narration with retained sources; semantic review remains required."""

from validate_plan import require_list, require_object, string, normalized
from name_policy import adapted_name_text

def validate_narration(plan, errors, required=False):
    """Reconcile a pre-edit narration inventory with the retained speech sources."""
    if not required and 'narration_sources' not in plan:
        return
    if 'narration_sources' not in plan:
        errors.append('narration_sources: original narration inventory missing')
        return
    entries = require_list(plan['narration_sources'], 'narration_sources')
    if not entries:
        string(plan, 'narration_absence_basis')
    sources = {source['id']: source for source in plan['sources']}
    episodes = {episode['id'] for episode in plan['episodes']}
    ids, mapped = set(), set()
    retained = 0
    for entry in entries:
        require_object(entry, 'narration source')
        nid, eid = string(entry, 'id'), string(entry, 'episode')
        string(entry, 'source_ref')
        original = string(entry, 'original_text')
        language = string(entry, 'source_language').lower().split('-')[0]
        if len(language) != 2 or not language.isascii() or not language.isalpha():
            raise ValueError(f'{nid}: source_language must use a two-letter code')
        status = string(entry, 'status')
        if nid in ids:
            errors.append(f'{nid}: duplicate narration inventory id')
        ids.add(nid)
        if eid not in episodes:
            errors.append(f'{nid}: narration outside selected episodes')
        if status == 'omit':
            string(entry, 'omission_basis')
            if 'source_id' in entry:
                errors.append(f'{nid}: omitted narration must not reference a retained source')
            continue
        if status != 'keep':
            errors.append(f'{nid}: narration status must be keep or omit')
            continue
        retained += 1
        if 'omission_basis' in entry:
            errors.append(f'{nid}: retained narration cannot also declare omission')
        sid = string(entry, 'source_id')
        if sid in mapped:
            errors.append(f'{nid}: narration source mapped more than once')
        mapped.add(sid)
        if sid not in sources:
            errors.append(f'{nid}: retained narration source missing')
            continue
        source = sources[sid]
        if source['episode'] != eid:
            errors.append(f'{nid}: narration maps to another episode')
        if source['kind'] != 'narration':
            errors.append(f'{nid}: original narration must retain narration kind')
        if language == source['language'].lower().split('-')[0]:
            expected = original
            if 'name_adaptation' in source:
                try:
                    expected = adapted_name_text(source)
                    if normalized(source['name_adaptation']['before_text']) != normalized(original):
                        errors.append(f'{nid}: name adaptation does not match the original narration inventory')
                except ValueError as exc:
                    errors.append(f'{nid}: {exc}')
                    continue
            if normalized(expected) != normalized(source['text']):
                errors.append(f'{nid}: same-language narration changed before speech planning')
    for sid, source in sources.items():
        if source['kind'] == 'narration' and sid not in mapped:
            errors.append(f'{sid}: narration source missing from original inventory')
    if retained and not any(speech['kind'] == 'narration'
                            for segment in plan['segments'] for speech in segment['speech']):
        errors.append('original narration retained but final narration speech is zero')
