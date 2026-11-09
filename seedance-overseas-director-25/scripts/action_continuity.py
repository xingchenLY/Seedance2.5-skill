"""Bind authored action bridges and compare state snapshots in playback order.

This checks explicit records, not the physical meaning of arbitrary prose.
Source coverage, visibility and whether a bridge is sufficient need review.
"""
import math
import re

EPS = 1e-7
BREAK_KINDS = {'offscreen_progress', 'source_time_jump', 'motion_ellipsis'}


def single_line(value):
    return (isinstance(value, str) and bool(value.strip())
            and '\n' not in value and '\r' not in value)


def state(value):
    return (isinstance(value, dict) and bool(value)
            and all(single_line(k) and single_line(v) for k, v in value.items()))


def motion_ellipsis_errors(resume, previous_beat, beat):
    """Permit only declared movement states; preserve source and critical events."""
    errors = []
    for field in ('omitted', 'start_evidence', 'result_evidence'):
        if not single_line(resume.get(field)):
            errors.append(f'motion_ellipsis needs concrete {field}')
    keys = resume.get('motion_keys')
    valid_keys = (isinstance(keys, list) and bool(keys) and all(isinstance(k, str) for k in keys)
                  and len(keys) == len(set(keys)))
    changed = {key for key in beat['before'] if beat['before'][key] != previous_beat['after'].get(key)}
    if (not valid_keys or set(keys) != changed or any(
            not re.search(r'\.(?:position|location|orientation|posture|motion_phase|位置|朝向|姿态|动作阶段)$', key)
            for key in keys)):
        errors.append('motion_ellipsis may change only declared movement keys; ownership/contact/other state changes need visible action')
    for field, value in (('start_evidence', previous_beat.get('action_text', '')),
                         ('result_evidence', beat.get('action_text', ''))):
        evidence = resume.get(field)
        if isinstance(evidence, str) and evidence.strip() and (not isinstance(value, str) or evidence not in value):
            errors.append(f'motion_ellipsis {field} must occur in the corresponding actual action_text')
    return errors


def shot_key(segment, shot):
    return segment['id'], shot['start']


def playback_positions(plan):
    """Locate actual shots; other validators check clip validity and coverage."""
    segments = {s['id']: s for s in plan['segments']}
    clips = plan.get('playback', [dict(segment_id=s['id'], start=0, end=s['shots'][-1]['end'])
                                  for s in plan['segments']])
    positions, cursor = {}, 0
    for clip in clips:
        segment = segments.get(clip['segment_id'])
        if segment is None or clip['end'] <= clip['start']:
            continue
        for shot in segment['shots']:
            if shot['start'] >= clip['start'] - EPS and shot['end'] <= clip['end'] + EPS:
                positions[shot_key(segment, shot)] = (
                    cursor + shot['start'] - clip['start'],
                    cursor + shot['end'] - clip['start'])
        cursor += clip['end'] - clip['start']
    return positions


def action_context(plan, errors, *, required=False):
    """Return per-shot authored action prose, validating explicit state chains."""
    if 'action_tracks' not in plan:
        if required:
            errors.append('action_tracks: source-based action continuity records are required; '
                          'use [] only when no tracked action/state changes are needed')
        return {}
    tracks = plan['action_tracks']
    if not isinstance(tracks, list):
        errors.append('action_tracks must be a list')
        return {}
    segments = {s['id']: s for s in plan['segments']}
    shots = {shot_key(s, shot): shot for s in plan['segments'] for shot in s['shots']}
    positions = playback_positions(plan)
    context, identifiers = {}, set()
    # The same state variable in one episode/time layer has one owner. Related
    # actions use one chain, avoiding two independent records that reset state.
    owners = {}
    for ti, track in enumerate(tracks):
        label = f'action_tracks[{ti}]'
        if not isinstance(track, dict):
            errors.append(f'{label}: expected an object')
            continue
        if any(not single_line(track.get(k)) for k in ('id', 'episode', 'time_layer', 'basis')):
            errors.append(f'{label}: id, episode, time_layer and source-based basis are required')
            continue
        label = track['id']
        if label in identifiers:
            errors.append(f'{label}: duplicate action track id')
        identifiers.add(label)
        beats = track.get('beats')
        if not isinstance(beats, list) or not beats:
            errors.append(f'{label}: nonempty action beats are required')
            continue
        target_state = track.get('end_state')
        if not state(target_state):
            errors.append(f'{label}: source-based end_state is required')
        previous = None
        previous_beat = None
        keys = None
        for bi, beat in enumerate(beats):
            location = f'{label}/beat[{bi}]'
            if not isinstance(beat, dict):
                errors.append(f'{location}: expected an object')
                continue
            sid, start = beat.get('segment_id'), beat.get('shot_start')
            if (not single_line(sid) or isinstance(start, bool) or not isinstance(start, (int, float))
                    or not math.isfinite(start)):
                errors.append(f'{location}: segment_id and finite shot_start are required')
                continue
            key = (sid, start)
            if key not in shots or key not in positions:
                errors.append(f'{location}: action beat must refer to an actual playback shot start')
                continue
            if segments[sid]['episode'] != track['episode']:
                errors.append(f'{location}: action track belongs to another episode')
            before, after = beat.get('before'), beat.get('after')
            prose = beat.get('action_text')
            if not state(before) or not state(after) or set(before) != set(after):
                errors.append(f'{location}: before/after need the same nonempty state keys and descriptions')
                continue
            if keys is None:
                keys = set(before)
                for variable in keys:
                    owner_key = (track['episode'], track['time_layer'], variable)
                    if owner_key in owners:
                        errors.append(f'{location}: state {variable} already belongs to another action track '
                                      'in this time layer; connect related actions instead of resetting them')
                    owners[owner_key] = label
            elif set(before) != keys:
                errors.append(f'{location}: tracked state keys changed within the action chain')
            if not single_line(prose):
                errors.append(f'{location}: concrete action_text bridge is required')
                prose = ''
            current_start, current_end = positions[key]
            resume = beat.get('break_before')
            if previous is None:
                if 'break_before' in beat:
                    errors.append(f'{location}: first action beat cannot have break_before')
            else:
                previous_state, previous_end = previous
                if current_start < previous_end - EPS:
                    errors.append(f'{location}: action beats overlap or reverse playback order; '
                                  'combine consecutive actions in the same shot into one beat')
                changed = before != previous_state
                if changed and resume is None:
                    errors.append(f'{location}: action state jump: before does not match previous after')
                if 'break_before' in beat:
                    valid = (isinstance(resume, dict) and isinstance(resume.get('kind'), str)
                             and resume['kind'] in BREAK_KINDS
                             and single_line(resume.get('basis')) and single_line(resume.get('resume_text')))
                    if not valid:
                        errors.append(f'{location}: break_before needs a supported kind, concrete basis and resume_text')
                    elif not changed:
                        errors.append(f'{location}: stale break_before without a state discontinuity')
                    elif resume['kind'] == 'offscreen_progress' and current_start <= previous_end + EPS:
                        errors.append(f'{location}: offscreen_progress needs actual intervening playback time')
                    else:
                        prefix = ''
                        if resume['kind'] == 'motion_ellipsis':
                            errors.extend(f'{location}: {error}' for error in motion_ellipsis_errors(resume, previous_beat, beat))
                            if single_line(resume.get('omitted')):
                                prefix = '剪辑省略' + resume['omitted'].strip().rstrip('。.!！；;') + '。'
                        prose = prefix + resume['resume_text'].strip().rstrip('。.!！；;') + '。' + prose
            if prose:
                context.setdefault(key, []).append(prose.strip().rstrip('。.!！；;') + '。')
            previous = (after, current_end)
            previous_beat = beat
        if previous is not None and state(target_state) and previous[0] != target_state:
            errors.append(f'{label}: final action state does not reach the source-based end_state')
    return context


def action_prose(segment, shot, context):
    return ''.join(context.get(shot_key(segment, shot), []))


def check_action_binding(segment, shot, context, visual_values, label, errors):
    normalize = lambda value: ' '.join(value.split())
    for prose in context.get(shot_key(segment, shot), []):
        if not any(normalize(prose) in normalize(value) for value in visual_values):
            errors.append(f'{label}: action bridge is absent from this final visual interval')
