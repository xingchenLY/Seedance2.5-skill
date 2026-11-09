"""Render non-speech effects from timed events, never from copied frame notes.

Checks timing/identity/binding, not whether a sound exists in the screenplay.
Times are local to a segment; continuations are verified in playback order.
"""
import math
import re
from decimal import Decimal

EPS = 1e-7


def finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def line(value):
    return isinstance(value, str) and bool(value.strip()) and '\n' not in value and '\r' not in value


def fmt(value):
    result = format(Decimal(str(value)), 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def sound_context(plan, errors=None):
    errors = errors if errors is not None else []
    segments = {s['id']: s for s in plan['segments']}
    clips = plan.get('playback', [dict(segment_id=s['id'], start=0, end=s['duration'])
                                  for s in plan['segments']])
    placed, by_id, cursor = [], {}, 0
    for clip in clips:
        placed.append((clip, cursor))
        cursor += clip['end'] - clip['start']
    for segment in segments.values():
        notes = segment.get('frame_notes', {})
        if isinstance(notes, dict) and any(isinstance(n, dict) and 'sound_effects' in n for n in notes.values()):
            errors.append(f'{segment["id"]}: migrate frame_notes.sound_effects to sound_events')
        events = segment.get('sound_events', [])
        if not isinstance(events, list):
            errors.append(f'{segment["id"]}: sound_events must be an array')
            continue
        for event in events:
            label = f'{segment["id"]}/sound'
            if not isinstance(event, dict):
                errors.append(f'{label}: event must be an object')
                continue
            if any(not line(event.get(k)) for k in ('id', 'source', 'text', 'basis')):
                errors.append(f'{label}: event needs id, source, sound text and source basis')
                continue
            label += '/' + event['id']
            if event['id'] in by_id:
                errors.append(f'{label}: duplicate sound id; one trigger is recorded once')
                continue
            if event.get('basis_kind') not in ('source_action', 'source_voice', 'scene', 'confirmed_design'):
                errors.append(f'{label}: invalid sound basis_kind')
            if (any(word in event['text'] for word in ('吸气', '喘息', '惊叫'))
                    and event.get('basis_kind') not in ('source_voice', 'confirmed_design')):
                errors.append(f'{label}: gasp/breath/scream needs source voice or confirmed design')
            if any(re.search(r'第?\d+(?:\.\d+)?\s*秒', event.get(k, ''))
                   for k in ('source', 'text', 'sustain', 'end_reason') if isinstance(event.get(k), str)):
                errors.append(f'{label}: sound prose must not hide extra times; use start/end')
            kind, start, end = event.get('kind'), event.get('start'), event.get('end')
            if kind not in ('instant', 'continuous') or not finite(start) or not finite(end):
                errors.append(f'{label}: sound needs kind and finite start/end')
                continue
            if (start < 0 or end > segment['duration'] + EPS
                    or (kind == 'instant' and (start != end or start >= segment['duration']))
                    or (kind == 'continuous' and end <= start)):
                errors.append(f'{label}: sound is outside segment or has invalid range')
                continue
            if kind == 'continuous':
                if (type(event.get('continues')) is not bool
                        or not line(event.get('sustain')) or not line(event.get('end_reason'))):
                    errors.append(f'{label}: continuous sound needs sustain, continues and end_reason')
                    continue
            elif 'continuation_of' in event or event.get('continues'):
                errors.append(f'{label}: an instantaneous trigger cannot be a continuation')
            if 'continuation_of' in event and not line(event['continuation_of']):
                errors.append(f'{label}: continuation_of must name a preceding sound id')
                continue
            matches = [(clip, offset) for clip, offset in placed
                       if clip['segment_id'] == segment['id']
                       and start >= clip['start'] - EPS
                       and ((kind == 'instant' and start < clip['end'] - EPS)
                            or (kind == 'continuous' and end <= clip['end'] + EPS))]
            if len(matches) != 1:
                errors.append(f'{label}: sound must fit one playback clip; split at editing-map cuts')
                continue
            clip, offset = matches[0]
            if event.get('continues') and abs(end - clip['end']) > EPS:
                errors.append(f'{label}: a continuing sound must reach its playback clip end')
            by_id[event['id']] = dict(event=event, segment=segment,
                                      start=offset + start - clip['start'],
                                      end=offset + end - clip['start'])
    followers = set()
    for sid, item in by_id.items():
        event = item['event']
        previous = event.get('continuation_of')
        if previous is None:
            continue
        prior = by_id.get(previous)
        if (prior is None or prior is item or prior['event']['kind'] != 'continuous'
                or not prior['event'].get('continues')
                or prior['event']['source'] != event['source']
                or prior['segment']['episode'] != item['segment']['episode']
                or abs(prior['end'] - item['start']) > EPS):
            errors.append(f'{sid}: continuation must follow the same unfinished source without a gap')
        if previous in followers:
            errors.append(f'{sid}: one sound cannot branch into duplicate continuations')
        followers.add(previous)
    # A continuing source must be represented in the next clip of this episode.
    episode_ends = {}
    for clip, offset in placed:
        segment = segments.get(clip['segment_id'])
        if segment:
            episode_ends[segment['episode']] = offset + clip['end'] - clip['start']
    for sid, item in by_id.items():
        if (item['event'].get('continues') and sid not in followers
                and item['end'] < episode_ends[item['segment']['episode']] - EPS):
            errors.append(f'{sid}: unfinished sound needs next-clip continuation or an actual end')
    return by_id


def frame_sound_prose(segment, group):
    """Clip each event to each actual shot inside this template frame."""
    cues = []
    for shot in segment['shots']:
        if shot.get('frame_id') != group['id']:
            continue
        for event in sorted(segment.get('sound_events', []), key=lambda e: (e['start'], e['id'])):
            start, end = max(shot['start'], event['start']), min(shot['end'], event['end'])
            if event['kind'] == 'instant':
                if shot['start'] - EPS <= event['start'] < shot['end'] - EPS:
                    cues.append(f'第{fmt(event["start"])}秒，{event["source"]}{event["text"]}。')
                continue
            if end <= start + EPS:
                continue
            continuation = '承接' if event['start'] < shot['start'] - EPS or event.get('continuation_of') else ''
            tail = ('持续至本镜末，尚未结束'
                    if event['end'] > shot['end'] + EPS or event.get('continues')
                    else event['end_reason'].strip().rstrip('。'))
            cues.append(f'第{fmt(start)}至{fmt(end)}秒，{continuation}{event["source"]}'
                        f'{event["text"]}（{event["sustain"]}）；{tail}。')
    return ''.join(cues)


def check_sound_binding(segment, group, fields, errors):
    expected = frame_sound_prose(segment, group)
    actual = [value for key, value in fields if key == '音效']
    normalize = lambda value: ' '.join(value.split())
    mismatch = bool(actual) if not expected else (len(actual) != 1 or normalize(actual[0]) != normalize(expected))
    if mismatch:
        errors.append(f'{segment["id"]}/{group["id"]}: sound effects differ from timed events in this frame')
