"""Require a bound predecessor snapshot for a batch starting after segment one.

This checks the supplied snapshot, not its authenticity; read the saved prior
delivery before copying it. Previous material is context, never re-rendered.
"""
from math import isfinite
from camera_views import endpoint_view, view_error, camera_view_prose, canonical_text


def previous_tails(plan, errors, *, required=False):
    result = {}
    current_ids = {s['id'] for s in plan.get('segments', [])}
    for episode in plan.get('episodes', []):
        number = episode.get('segment_start', 1)
        tail = episode.get('previous_tail')
        label = str(episode['id']) + '/previous_tail'
        if tail is None:
            if required and 'mode' in plan and type(number) is int and number > 1:
                errors.append(f'{label}: continuing batch requires the saved previous actual shot and final text')
            continue
        if type(number) is not int or number <= 1 or not isinstance(tail, dict):
            errors.append(f'{label}: snapshot requires segment_start > 1 and an object')
            continue
        if (type(tail.get('segment_number')) is not int or tail.get('segment_number') != number - 1 or not isinstance(tail.get('segment_id'), str)
                or not tail['segment_id'].strip() or tail['segment_id'] in current_ids):
            errors.append(f'{label}: predecessor number/ID is missing, stale or part of this delivery')
            continue
        shot = tail.get('shot')
        if not isinstance(shot, dict):
            errors.append(f'{label}: copy the previous final actual shot record')
            continue
        numbers = [shot.get('start'), shot.get('end')]
        if (any(type(n) not in (int, float) or not isfinite(n) for n in numbers)
                or not 0 <= numbers[0] < numbers[1]):
            errors.append(f'{label}: invalid previous actual-shot range')
            continue
        fields = ('start_camera', 'end_camera', 'start_scale', 'end_scale', 'camera_text', 'visual_text')
        if any(not isinstance(shot.get(f), str) or not shot[f].strip() for f in fields):
            errors.append(f'{label}: previous shot camera, scale and authored content are required')
            continue
        if any(view_error(endpoint_view(shot, side), shot.get('visible_characters', []))
               for side in ('start', 'end')):
            errors.append(f'{label}: previous shot needs real start/end views')
            continue
        final_camera, final_visual = tail.get('final_camera'), tail.get('final_visual')
        if any(not isinstance(v, str) or not v.strip() for v in (final_camera, final_visual)):
            errors.append(f'{label}: final_camera and final_visual must come from the delivered actual-shot interval')
            continue
        if not any(canonical_text(camera_view_prose(shot, style=style)) in canonical_text(final_camera)
                   for style in ('compact', 'expanded', 'legacy')):
            errors.append(f'{label}: previous view differs from saved final camera text')
            continue
        if (canonical_text(shot['camera_text']) not in canonical_text(final_camera)
                or canonical_text(shot['visual_text']) not in canonical_text(final_visual)):
            errors.append(f'{label}: previous authored content differs from saved final shot')
            continue
        scale = shot['start_scale']
        if scale != shot['end_scale']:
            scale += '连续变为' + shot['end_scale']
        from shot_pacing import fmt
        interval = f'{fmt(shot["start"])}–{fmt(shot["end"])} 秒 '
        if (canonical_text(interval + scale) not in canonical_text(final_camera)
                or canonical_text(interval) not in canonical_text(final_visual)):
            errors.append(f'{label}: saved final intervals must match previous actual-shot range and scale')
            continue
        result[episode['id']] = tail
    return result
