"""Bind narrated clauses to their authored visual tasks, without inventing shots.

Source-clause completeness and final evidence placement are checkable. Whether
the chosen visuals fully communicate the source remains a content judgment.
"""
import math
from collections import defaultdict

from narration_editing import nonempty, information_key
from dialogue_reactions import positive_evidence

EPS = 1e-7
TASKS = {'context', 'relationship', 'action', 'detail', 'reaction', 'result'}


def union_seconds(ranges):
    total, right = 0, -math.inf
    for start, end in sorted(ranges):
        total += max(0, end - max(start, right))
        right = max(right, end)
    return total


def long_narrated_shot(segment, shot):
    """Count the whole actual shot, including silent or other-voice portions."""
    return shot['end'] - shot['start'] > 6 + EPS and any(
        v['kind'] == 'narration' and min(v['end'], shot['end']) > max(v['start'], shot['start']) + EPS
        for v in segment['speech'])


def coverage_required(segment):
    voices = [v for v in segment['speech'] if v['kind'] == 'narration']
    return union_seconds([(v['start'], v['end']) for v in voices]) > 6 + EPS or any(
        long_narrated_shot(segment, s) for s in segment['shots'])


def check_narration_coverage(plan, errors):
    from mode_rules import narrative_mode
    from validate_plan import source_parts_match
    if not narrative_mode(plan):
        return {}
    bindings = defaultdict(list)
    for segment in plan['segments']:
        label = segment['id'] + '/narration_coverage'
        entry = segment.get('narration_coverage')
        # Timing-only records are not a completed visual design.
        designed = any('frame_id' in shot for shot in segment['shots'])
        if entry is None and (not designed or not coverage_required(segment)):
            continue
        if not isinstance(entry, list) or not entry:
            errors.append(f'{label}: long narration needs a nonempty source-to-picture task list')
            continue
        spoken = defaultdict(list)
        for voice in segment['speech']:
            if voice['kind'] == 'narration':
                spoken[voice['source_id']].append(voice['text'])
        assigned = defaultdict(list)
        seen_evidence = set()
        for index, unit in enumerate(entry):
            ul = f'{label}[{index}]'
            if not isinstance(unit, dict):
                errors.append(f'{ul}: expected a source-linked visual task')
                continue
            sid, text = unit.get('source_id'), unit.get('text')
            if not isinstance(sid, str) or sid not in spoken or not nonempty(text):
                errors.append(f'{ul}: link a narration source spoken in this segment and its exact text')
                continue
            assigned[sid].append(text)
            if not isinstance(unit.get('task'), str) or unit['task'] not in TASKS or not nonempty(unit.get('focus')):
                errors.append(f'{ul}: select a concrete context/relationship/action/detail/reaction/result focus')
            views = unit.get('views')
            if not isinstance(views, list) or not views:
                errors.append(f'{ul}: each narrated unit needs at least one actual visual placement')
                continue
            for view in views:
                if not isinstance(view, dict):
                    errors.append(f'{ul}: expected a visual placement object')
                    continue
                start, evidence = view.get('shot_start'), view.get('evidence')
                if type(start) not in (int, float) or not math.isfinite(start) or not nonempty(evidence):
                    errors.append(f'{ul}: placement needs finite shot_start and concrete evidence')
                    continue
                shots = [s for s in segment['shots'] if abs(s['start'] - start) <= EPS]
                if len(shots) != 1:
                    errors.append(f'{ul}: placement must refer to one actual shot in this segment')
                    continue
                key = information_key(evidence)
                if key in seen_evidence:
                    errors.append(f'{ul}: repeated visual evidence cannot cover another information task')
                seen_evidence.add(key)
                bindings[(segment['id'], start)].append(evidence)
        for sid, parts in spoken.items():
            # Internal clause mapping must not skip words or create new spoken lines.
            original = ' '.join(parts)
            if not assigned[sid] or not source_parts_match(original, assigned[sid]):
                errors.append(f'{label}/{sid}: visual task texts must cover the full narrated text once in source order')
    return dict(bindings)


def check_coverage_binding(segment, shot, bindings, content_values, errors):
    for evidence in bindings.get((segment['id'], shot['start']), []):
        if not any(positive_evidence(value, evidence) for value in content_values):
            errors.append(f'{segment["id"]}/{shot["start"]}: narration coverage evidence is absent from its final visual interval')
