"""Scoped temporal jump cuts; source justification still requires content review."""
import math

from camera_views import endpoint_view, physical_key, view_error

EPS = 1e-7
JUMP_FIELDS = {'from_shot_start', 'basis', 'omitted', 'cue'}


def jump_record_error(record):
    if not isinstance(record, dict) or set(record) != JUMP_FIELDS:
        return 'jump_cut_before needs from_shot_start, basis, omitted and cue'
    start = record['from_shot_start']
    if (isinstance(start, bool) or not isinstance(start, (int, float)) or
            not math.isfinite(start) or start < 0):
        return 'jump_cut_before.from_shot_start must be a finite nonnegative number'
    for field in ('basis', 'omitted', 'cue'):
        value = record[field]
        if not isinstance(value, str) or not value.strip() or '\n' in value or '\r' in value:
            return f'jump_cut_before.{field} must be concrete single-line text'
    return None


def allow_jump_cut(segment, shot, previous, errors):
    if 'jump_cut_before' not in shot:
        return False
    label = f'{segment["id"]}/{shot["start"]}'
    record = shot['jump_cut_before']
    error = jump_record_error(record)
    if error:
        errors.append(f'{label}: {error}')
        return False
    if not previous or previous[0] != segment['episode'] or previous[2] != segment['id']:
        errors.append(f'{label}: jump cut must follow a shot in the same episode and prompt segment')
        return False
    before = previous[1]
    if (abs(before['start'] - record['from_shot_start']) > EPS or
            abs(before['end'] - shot['start']) > EPS):
        errors.append(f'{label}: jump cut does not match the actual adjacent playback predecessor')
        return False
    left, right = endpoint_view(before, 'end'), endpoint_view(shot, 'start')
    if (view_error(left, before['visible_characters']) or
            view_error(right, shot['visible_characters'])):
        errors.append(f'{label}: jump cut requires valid camera endpoint views')
        return False
    if physical_key(left)[0] != physical_key(right)[0]:
        errors.append(f'{label}: jump cut exception is limited to the same scene space')
        return False
    return True


def jump_cut_prose(shot):
    if 'jump_cut_before' not in shot:
        return ''
    record = shot['jump_cut_before']
    error = jump_record_error(record)
    if error:
        raise ValueError(error)
    omitted = record['omitted'].strip().rstrip('。.!！；;，,')
    cue = record['cue'].strip().rstrip('。.!！；;')
    return f'跳切省略{omitted}；{cue}。'


def check_jump_binding(shot, camera_values, label, errors):
    try:
        evidence = jump_cut_prose(shot)
    except ValueError as exc:
        errors.append(f'{label}: {exc}')
        return
    if evidence and not any(' '.join(evidence.split()) in ' '.join(value.split())
                            for value in camera_values):
        errors.append(f'{label}: jump cut cue is absent from this final camera interval')
