"""Require authored, endpoint-bound visible changes at repeated-subject prompt cuts.

This checks evidence and its scope, not photographic geometry or generated video.
Legacy private fixtures without a production mode retain their former schema.
"""
from camera_views import canonical_text, endpoint_view, view_error

ASPECTS = {'view_angle', 'occlusion', 'spatial_relation', 'visible_information'}
FIELDS = {'from_segment_id', 'from_shot_start', 'aspect',
          'before_evidence', 'after_evidence', 'basis'}
EPS = 1e-7


def playback_shots(plan):
    segments = {s['id']: s for s in plan['segments']}
    clips = plan.get('playback', [dict(segment_id=s['id'], start=0, end=s['duration'])
                                  for s in plan['segments']])
    for clip in clips:
        segment = segments.get(clip['segment_id'])
        if segment is not None:
            for shot in segment['shots']:
                if shot['start'] >= clip['start'] - EPS and shot['end'] <= clip['end'] + EPS:
                    yield segment, shot


def text_present(evidence, values):
    normalize = lambda value: ' '.join(value.split())
    return any(normalize(evidence) in normalize(value) for value in values)


def concrete_evidence(value):
    # Reject bare labels/micro-move assurances. The semantic review still has to
    # confirm that a longer description actually shows a different composition.
    compact = canonical_text(value)
    generic = ('近景', '中近景', '中景', '全景', '特写', '机位不同', '构图不同',
               '画面不同', '略换角度', '错开半步', '固定取景', '主体与必要关系位置清楚')
    for phrase in generic:
        compact = compact.replace(phrase, '')
    return len(compact) >= 8


def boundary_context(plan, errors, *, required=False):
    """Return validated evidence pairs in playback order; never fabricate evidence."""
    pairs = []
    previous = None
    visited = set()
    for segment, shot in playback_shots(plan):
        key = (segment['id'], shot['start'])
        visited.add(key)
        record = shot.get('cut_change_before')
        label = f'{segment["id"]}/{shot["start"]}/cut_change_before'
        cross = (previous is not None and previous[0]['episode'] == segment['episode']
                 and previous[0]['id'] != segment['id'])
        if not cross:
            if 'cut_change_before' in shot:
                errors.append(f'{label}: record requires an adjacent cross-prompt cut in the same episode')
            previous = segment, shot
            continue
        before_segment, before = previous
        left, right = endpoint_view(before, 'end'), endpoint_view(shot, 'start')
        bad_view = (view_error(left, before.get('visible_characters', [])) or
                    view_error(right, shot.get('visible_characters', [])))
        enforced = required and 'mode' in plan
        if bad_view:
            if enforced or 'cut_change_before' in shot:
                errors.append(f'{label}: cross-prompt cut requires actual end/start views')
            previous = segment, shot
            continue
        repeated = (canonical_text(left['space']) == canonical_text(right['space'])
                    and left['subject'] == right['subject'])
        if record is None and 'cut_change_before' not in shot:
            if enforced and repeated:
                errors.append(f'{label}: same-scene same-subject prompt boundary needs visible composition '
                              'change evidence; scale labels or a half-step alone do not establish a new picture')
            previous = segment, shot
            continue
        if not isinstance(record, dict) or set(record) != FIELDS:
            errors.append(f'{label}: expected from_segment_id, from_shot_start, aspect, '
                          'before_evidence, after_evidence and basis')
            previous = segment, shot
            continue
        start = record['from_shot_start']
        if (record['from_segment_id'] != before_segment['id'] or isinstance(start, bool)
                or not isinstance(start, (int, float)) or start != before['start']):
            errors.append(f'{label}: predecessor differs from the actual playback cut')
        aspect = record['aspect']
        if not isinstance(aspect, str) or aspect not in ASPECTS:
            errors.append(f'{label}: aspect must describe angle, occlusion, spatial relation or visible information')
        strings = [record[field] for field in ('before_evidence', 'after_evidence', 'basis')]
        if any(not isinstance(v, str) or not v.strip() or '\n' in v or '\r' in v for v in strings):
            errors.append(f'{label}: evidence and narrative basis must be nonempty single-line text')
            previous = segment, shot
            continue
        before_text, after_text = strings[:2]
        if (canonical_text(before_text) == canonical_text(after_text)
                or not all(concrete_evidence(v) for v in strings[:2])):
            errors.append(f'{label}: describe distinct visible endpoint compositions, not generic change claims')
        for side, evidence, source in [('before', before_text, before), ('after', after_text, shot)]:
            values = [source.get(field, '') for field in ('camera_text', 'visual_text')]
            if not text_present(evidence, values):
                errors.append(f'{label}: {side} evidence is absent from the authored endpoint shot')
        pairs.append(((before_segment['id'], before['start']), key, record))
        previous = segment, shot
    for segment in plan['segments']:
        for shot in segment['shots']:
            if 'cut_change_before' in shot and (segment['id'], shot['start']) not in visited:
                errors.append(f'{segment["id"]}/{shot["start"]}: cut evidence refers to a shot outside playback')
    return pairs


def check_boundary_binding(plan, intervals, errors):
    # Structural findings are already reported by validate_visual_plan.
    pairs = boundary_context(plan, [], required=True)
    for before, after, record in pairs:
        for side, key in [('before', before), ('after', after)]:
            if not text_present(record[side + '_evidence'], intervals.get(key, [])):
                errors.append(f'{after[0]}/{after[1]}/cut_change_before: {side} visible-change evidence '
                              'is absent from the correct final shot interval')
