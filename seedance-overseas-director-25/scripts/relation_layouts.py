"""Bind source-based presence and authored spatial placements to each shot.

These are structural checks. The screenplay, framing choices and geometry still
require content review; neither names nor nonempty descriptions prove truth.
"""


def name_list(value):
    return (isinstance(value, list)
            and all(isinstance(n, str) and n.strip() for n in value)
            and len(value) == len(set(value)))


def nonempty_line(value):
    return (isinstance(value, str) and bool(value.strip())
            and '\n' not in value and '\r' not in value)


def scene_cast(segment):
    """Return visible people plus all people present in the pictured scenes.

    Keep actual shot visibility separate. Missing scene rosters in private
    timing-only fixtures fall back to visibility; production layout validation
    independently requires source-based scene_characters in every shot.
    """
    visible, present = [], []
    for shot in segment['shots']:
        names = shot.get('visible_characters', [])
        if not name_list(names):
            raise ValueError(f'{segment["id"]}: visible_characters must be unique nonempty names')
        visible.extend(name for name in names if name not in visible)
        relation = shot.get('relation')
        scene = relation.get('scene_characters', names) if isinstance(relation, dict) else names
        if not name_list(scene):
            raise ValueError(f'{segment["id"]}: scene_characters must be unique nonempty names')
        present.extend(name for name in scene if name not in present)
    return visible + [name for name in present if name not in visible]


def derive_scene_cast(segment):
    """Preserve declared people unless their actual scene presence is reconciled."""
    roster = scene_cast(segment)
    if 'cast' in segment:
        declared = segment['cast']
        if not name_list(declared):
            raise ValueError(f'{segment["id"]}: authored cast must be unique nonempty names')
        omitted = [name for name in declared if name not in roster]
        if omitted:
            raise ValueError(f'{segment["id"]}: refusing to silently remove declared cast: '
                             + '、'.join(omitted)
                             + '; reconcile actual scene presence and prior/source evidence before rendering')
    return roster


def check_scene_positions(segment, positions, label, errors):
    """Bind whole-segment offscreen people to the authored header positions.

    Names alone cannot prove a real location or correct timing. Source facts,
    entry/exit events, scene separation and camera coverage need content review.
    """
    roster = scene_cast(segment)
    visible = {name for shot in segment['shots'] for name in shot.get('visible_characters', [])}
    offscreen = [name for name in roster if name not in visible]
    if not offscreen:
        return
    if not nonempty_line(positions):
        errors.append(f'{label}: scene positions must retain all on-scene offscreen people')
        return
    missing = [name for name in offscreen if name not in positions]
    if missing:
        errors.append(f'{label}: scene positions omit on-scene offscreen people: ' + '、'.join(missing))
    authored = segment.get('presentation', {}).get('人物位置关系')
    if nonempty_line(authored) and ' '.join(authored.split()) != ' '.join(positions.split()):
        errors.append(f'{label}: final scene positions differ from authored positions; preserve offscreen anchors')


def layout_errors(shot):
    relation = shot.get('relation')
    if not isinstance(relation, dict):
        return []  # The existing relation validator reports a missing record.
    mode = relation.get('mode')
    if mode not in ('same_scene', 'remote', 'solo', 'insert', 'empty'):
        return []
    scene = relation.get('scene_characters')
    if not name_list(scene):
        return ['relation.scene_characters must list source-based on-scene characters before framing']
    visible = shot.get('visible_characters', [])
    characters = relation.get('characters')
    if not name_list(visible) or not name_list(characters):
        return ['relation characters and visible_characters must be unique nonempty names']
    errors = []
    if not set(visible) <= set(scene):
        errors.append('visible characters must belong to source-based scene_characters')
    if mode == 'solo' and (len(scene) != 1 or set(characters) != set(scene)):
        errors.append('solo cannot hide other on-scene characters')
    if mode != 'same_scene':
        return errors
    if not set(characters) <= set(scene):
        errors.append('same_scene relation characters must belong to scene_characters')
    placements = relation.get('placements')
    if not isinstance(placements, list) or not placements:
        return errors + ['same_scene needs placements, not just character names']
    recorded = []
    for item in placements:
        if (not isinstance(item, dict) or not nonempty_line(item.get('character'))
                or item.get('visibility') not in ('visible', 'offscreen')
                or not nonempty_line(item.get('placement'))):
            errors.append('each placement needs character, visibility and a spatial description')
            continue
        name = item['character']
        recorded.append(name)
        if (item['visibility'] == 'visible') != (name in visible):
            errors.append(f'{name}: placement visibility differs from visible_characters')
    if len(recorded) != len(set(recorded)) or set(recorded) != set(characters):
        errors.append('placements must cover each relation character exactly once')
    if len(visible) == 1 and not nonempty_line(relation.get('single_reason')):
        errors.append('same_scene single-person framing needs a concrete single_reason')
    return errors


def relation_layout_prose(shot):
    """Emit all authored visible placements and necessary offscreen relationships."""
    errors = layout_errors(shot)
    if errors:
        raise ValueError('\n'.join(errors))
    relation = shot.get('relation', {})
    if not isinstance(relation, dict) or relation.get('mode') != 'same_scene':
        return ''
    return ''.join(('画框外的' if item['visibility'] == 'offscreen' else '')
                   + item['character'] + item['placement'].strip().rstrip('。.!！；;') + '。'
                   for item in relation['placements'])


def check_layout_binding(shot, visual_values, label, errors):
    try:
        prose = relation_layout_prose(shot)
    except ValueError as exc:
        errors.append(f'{label}: {exc}')
        return
    normalize = lambda value: ' '.join(value.split())
    if prose and not any(normalize(prose) in normalize(value) for value in visual_values):
        errors.append(f'{label}: spatial placements are absent from this final visual interval')
