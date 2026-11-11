"""Locate on-scene people with no authored visibility in a continuous scope.

A candidate is not a geometric judgment or an instruction to widen, add a shot,
move a person, or increase duration. Raw sources and reference images still need
review. Event boundaries conservatively prevent flashback/real-time conflation.
"""
from camera_views import canonical_text, endpoint_view
from cut_boundary_checks import playback_shots
from relation_layouts import name_list


def review_scene_presence(plan):
    active, candidates = {}, []
    previous = None
    events_by_segment = {}
    for event in plan.get('events', []):
        events_by_segment.setdefault(event['segment_id'], []).append(event)

    def finish(name):
        record = active.pop(name)
        if not record['seen']:
            candidates.append(dict(code='scene_presence', characters=[name],
                                   space=record['space'], shots=record['shots']))

    for segment, shot in playback_shots(plan):
        view = endpoint_view(shot, 'start')
        end_view = endpoint_view(shot, 'end')
        present = shot.get('relation', {}).get('scene_characters')
        visible = shot.get('visible_characters')
        if (not isinstance(view, dict) or not isinstance(view.get('space'), str)
                or not name_list(present) or not name_list(visible)):
            for name in list(active): finish(name)
            previous = None
            continue
        events = tuple(event['id'] for event in events_by_segment.get(segment['id'], [])
                       if min(event['end'], shot['end']) > max(event['start'], shot['start']))
        key = (segment.get('episode'), canonical_text(view['space']), events)
        if key != previous:
            for name in list(active): finish(name)
        for name in list(active):
            if name not in present: finish(name)
        for name in present:
            record = active.setdefault(name, dict(space=view['space'], seen=False, shots=[]))
            record['seen'] |= name in visible
            location = dict(segment_id=segment['id'], start=shot['start'], end=shot['end'])
            if location not in record['shots']:
                record['shots'].append(location)
        # Unknown/moving space endpoints are not proof of a continuous room.
        previous = key if (isinstance(end_view, dict) and
                           canonical_text(end_view['space']) == key[1]) else None
    for name in list(active): finish(name)

    # Several passive people in the same scope share one review item.
    grouped = {}
    for item in candidates:
        key = (item['space'], tuple((s['segment_id'], s['start'], s['end']) for s in item['shots']))
        if key in grouped:
            grouped[key]['characters'].extend(n for n in item['characters'] if n not in grouped[key]['characters'])
        else:
            grouped[key] = item
    return list(grouped.values())
