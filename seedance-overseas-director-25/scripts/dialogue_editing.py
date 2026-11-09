"""Review actual dialogue-bearing long shots independently of reception coverage.

No automatic cuts or dialogue rewrites. Source truth, missing semantic beats and
whether an alternative cut really loses necessary information need content review.
"""
import math

from narration_editing import check_long_take, nonempty

EPS = 1e-7
LONG_SHOT_SECONDS = 6


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def has_dialogue(segment, shot):
    return any(v['kind'] == 'dialogue'
               and min(v['end'], shot['end']) - max(v['start'], shot['start']) > EPS
               for v in segment['speech'])


def check_dialogue_editing(plan, errors):
    """Bind timed long-shot stages to the same final picture as reception evidence."""
    bindings = {}
    for segment in plan['segments']:
        for shot in segment['shots']:
            label = f'{segment["id"]}/{shot["start"]}/dialogue_visual'
            spoken = has_dialogue(segment, shot)
            required = spoken and shot['end'] - shot['start'] > LONG_SHOT_SECONDS + EPS
            entry = shot.get('dialogue_visual')
            if entry is None and not required:
                continue
            if not spoken:
                errors.append(f'{label}: dialogue_visual requires actual dialogue in this shot')
                continue
            if not isinstance(entry, dict):
                errors.append(f'{label}: dialogue-bearing shot longer than six seconds needs '
                              'dialogue_visual or redesigned actual cuts; reception coverage alone is insufficient')
                continue
            if entry.get('mode') != 'progression' or not nonempty(entry.get('basis')):
                errors.append(f'{label}: long dialogue shot needs progression and a concrete source basis')
            beats = entry.get('beats')
            if not isinstance(beats, list) or len(beats) < 2:
                errors.append(f'{label}: long dialogue shot needs at least two timed visible stages')
                continue
            cursor, evidence_seen = shot['start'], set()
            for beat in beats:
                if not isinstance(beat, dict):
                    errors.append(f'{label}: expected a timed dialogue stage object')
                    continue
                start, end, evidence = beat.get('start'), beat.get('end'), beat.get('visual_evidence')
                if (not finite(start) or not finite(end) or not shot['start'] <= start < end <= shot['end']
                        or abs(start - cursor) > EPS):
                    errors.append(f'{label}: dialogue stages must continuously cover the whole actual shot in order')
                if finite(end):
                    cursor = end
                if not nonempty(evidence):
                    errors.append(f'{label}: dialogue stage needs concrete visible evidence')
                    continue
                key = ''.join(evidence.split())
                if key in evidence_seen:
                    errors.append(f'{label}: repeated evidence cannot establish dialogue progression')
                evidence_seen.add(key)
                if finite(start) and finite(end):
                    record = dict(start=start, end=end, evidence=evidence)
                    bindings.setdefault((segment['id'], shot['start']), []).append(('dialogue_beat', record))
            if abs(cursor - shot['end']) > EPS:
                errors.append(f'{label}: dialogue stages miss the actual shot endpoint')
            # Shared long-take criteria; the record stays separate from narration.
            check_long_take(shot, True, label, errors, field='dialogue_visual', audio='dialogue')
    return bindings
