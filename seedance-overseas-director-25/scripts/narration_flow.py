"""Conservative narration-boundary checks, not a general prosody classifier.

Run only for narration in the two narrative modes. Real source quotations and
unpunctuated clause boundaries still need source-based human review.
"""
import re

EPS = 1e-7
CLOSERS = '\"\'”’）)]】》」』'
FRAGILE = {
    'en': {'a', 'an', 'the', 'my', 'your', 'our', 'their',
           'of', 'to', 'from', 'with', 'without', 'and', 'or', 'because'},
    'es': {'el', 'la', 'los', 'las', 'un', 'una', 'unos', 'unas',
           'de', 'del', 'al', 'con', 'sin', 'para', 'porque'},
    'pt': {'o', 'a', 'os', 'as', 'um', 'uma', 'uns', 'umas',
           'de', 'do', 'da', 'dos', 'das', 'com', 'sem', 'para', 'porque'},
}


def normalize(text):
    return ' '.join(text.split())


def marked_boundary(left, *, sentence=False):
    tail = left.rstrip().rstrip(CLOSERS).rstrip()
    # A title abbreviation does not finish a sentence (Dr. | Smith).
    if re.search(r'\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St)\.$', tail, re.I):
        return False
    return bool(re.search(r'[。！？.!?]$' if sentence else r'[，,。！？.!?；;：:、…—]$', tail))


def fragile_boundary(left, language):
    if marked_boundary(left):
        return False
    tail = left.rstrip().rstrip(CLOSERS).rstrip()
    if re.search(r'\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St)\.$', tail, re.I):
        return True
    words = re.findall(r'[^\W\d_]+', tail, re.UNICODE)
    return bool(words and words[-1].lower() in FRAGILE.get(language.lower().split('-')[0], set()))


def mechanical_pause(text, *, basis=False):
    """Detect explicit production-motivated pauses; keep negative instructions."""
    for clause in re.split(r'[，,；;。\n]|但是|但|而是|\bbut\b', text, flags=re.I):
        if re.search(r'不(?:会|应|要|得|必|需|因|为|随|让|在|停|断|加)|并非|而非|不是|无需|无关|禁止|避免|'
                     r'\b(?:without|never|do not|no pauses?|not for)\b', clause, re.I):
            continue
        production = re.search(r'切镜|换镜|转场|画面切换|分镜|反应镜头|给反应|凑时|补秒|取整|'
                               r'等(?:待)?(?:画面|镜头|反应)|\b(?:cuts?|shots?|frames?|transitions?)\b', clause, re.I)
        stopping = re.search(r'停顿|停声|停下|静音|收声|断句|断声|留白|缓冲|等待|凑|补秒|'
                             r'\b(?:pauses?|silence|stop speaking|hold)\b', clause, re.I)
        if production and (basis or stopping):
            return True
    return False


def check_narration_direction(text, label, errors):
    if mechanical_pause(text):
        errors.append(f'{label}: narration must not pause for camera cuts, frame changes or timing padding')


def boundary_record(record, left, right, kinds, label, errors):
    """Bind a narrowly scoped review to the actual two sides of this boundary."""
    if not isinstance(record, dict) or record.get('kind') not in kinds:
        errors.append(f'{label}: narration boundary needs boundary_before with kind {"/".join(sorted(kinds))}')
        return False
    for key, actual, compare in (('left', left, str.endswith), ('right', right, str.startswith)):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip() or not compare(normalize(actual), normalize(value)):
            errors.append(f'{label}: boundary_before.{key} must match the actual boundary text')
            return False
    basis = record.get('basis')
    if not isinstance(basis, str) or not basis.strip() or mechanical_pause(basis, basis=True):
        errors.append(f'{label}: narration boundary requires a linguistic/source basis, not a camera or padding reason')
        return False
    if record['kind'] in ('source_break', 'independent_unit'):
        evidence = record.get('source_evidence')
        if not isinstance(evidence, str) or not evidence.strip() or mechanical_pause(evidence, basis=True):
            errors.append(f'{label}: boundary_before needs actual source_evidence for the interruption or independent unit')
            return False
    return True


def check_source_units(plan, errors):
    inventory = {entry['source_id']: entry for entry in plan.get('narration_sources', [])
                 if entry.get('status') == 'keep' and 'source_id' in entry}
    for before, after in zip(plan['sources'], plan['sources'][1:]):
        if (before['kind'] != 'narration' or after['kind'] != 'narration'
                or before['episode'] != after['episode'] or before['speaker'] != after['speaker']):
            continue
        first, second = inventory.get(before['id']), inventory.get(after['id'])
        if first is None or second is None:
            continue  # Inventory validation reports missing mappings.
        left, right = first['original_text'], second['original_text']
        label = f'narration/{before["id"]}->{after["id"]}'
        record = second.get('boundary_before')
        if fragile_boundary(left, first['source_language']):
            errors.append(f'{label}: suspicious source-ID split inside a phrase; restore the complete original narration unit')
        elif record is not None or not marked_boundary(left, sentence=True):
            boundary_record(record, left, right, {'independent_unit'}, label, errors)


def check_narration_flow(plan, errors):
    from mode_rules import narrative_mode
    from speech_continuity import speech_context
    if not narrative_mode(plan):
        return
    check_source_units(plan, errors)
    sources = {s['id']: s for s in plan['sources']}
    context = speech_context(plan)
    locations = {id(item): key[0] for key, item in context.items()}
    for (segment_id, _), item in context.items():
        voice = item['voice']
        if voice['kind'] != 'narration':
            continue
        label = f'{segment_id}/{voice["source_id"]}/narration'
        check_narration_direction(voice.get('performance', ''), label, errors)
        pause = voice.get('pause_before', {})
        if isinstance(pause, dict) and mechanical_pause(str(pause.get('basis', '')), basis=True):
            errors.append(f'{label}: narration pause basis cannot be a camera cut, reaction wait or timing padding')
        prior = item['previous']
        if prior is None:
            if 'boundary_before' in voice:
                errors.append(f'{label}: boundary_before requires a previous fragment of the same source')
            continue
        left, right = prior['voice']['text'], voice['text']
        if (locations.get(id(prior)) == segment_id and abs(item['gap']) <= EPS
                and abs(prior['voice']['end'] - voice['start']) <= EPS):
            errors.append(f'{label}: continuous narration in one segment must use one speech record across frames')
        reason = pause.get('reason') if isinstance(pause, dict) else None
        record = voice.get('boundary_before')
        source_break = item['gap'] > EPS and reason in ('source_pause', 'story_break')
        if source_break:
            boundary_record(record, left, right, {'source_break'}, label, errors)
        else:
            language = sources.get(voice['source_id'], {}).get('language', '')
            if fragile_boundary(left, language):
                errors.append(f'{label}: unnatural narration split inside a phrase; move the boundary or restore one utterance')
            elif record is not None or not marked_boundary(left):
                boundary_record(record, left, right, {'clause'}, label, errors)
