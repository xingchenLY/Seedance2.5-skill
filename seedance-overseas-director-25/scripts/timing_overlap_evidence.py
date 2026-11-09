"""Bind live-drama independent-time reviews to actual gaps and final pictures.

This enforces coverage and references, not the truth of a causal explanation.
Only final delivery needs these conditional receipts; rendering never retimes.
"""
import re
from shot_pacing import EPS, finite_number
from timing_overlap_review import review_timing_overlap
from review_evidence import concrete_note, normalized

CONSTRAINTS = {'source_order', 'physical', 'readability', 'performance',
               'scene_boundary', 'delivery_pause'}

# Reject only a whole explanation made of known non-reasons. Concrete reasons
# may contain these words; detecting every paraphrase would require semantics.
BARE_REASONS = {
    '需要完整呈现情绪变化', '完整呈现情绪变化', '为了完整呈现情绪变化',
    '需要完整展示动作', '完整展示动作', '为了完整展示动作',
    '给反应留时间', '需要给反应留时间', '需要独立反应时间',
    '情绪落稳', '等待情绪落稳', '情绪需要落稳',
    '完整表演', '需要完整表演', '为了完整表演', '需要保证动作完整性',
    '等台词说完', '台词结束后再反应',
    'completeemotionalperformance', 'allowtimeforreaction', 'showthecompleteaction',
}


def constraint_basis(value):
    return (concrete_note(value)
            and re.sub(r'[\W_]+', '', value).lower() not in BARE_REASONS)


def required_overlap_gaps(plan):
    if plan.get('mode') != '真人剧':
        return []
    return [dict(segment_id=item['segment_id'], **gap)
            for item in review_timing_overlap(plan) for gap in item['uncovered_ranges']
            if gap['speech_candidates']]


def check_timing_overlap_evidence(plan, final_intervals, errors):
    if plan.get('mode') != '真人剧':
        return
    expected = required_overlap_gaps(plan)
    rows = plan.get('delivery_review', {}).get('timing_overlap', [])
    if not isinstance(rows, list):
        errors.append('delivery_review.timing_overlap: must be a list of scoped gap reviews')
        return
    seen = set()
    for row in rows:
        label = 'delivery_review.timing_overlap'
        if (not isinstance(row, dict) or not isinstance(row.get('segment_id'), str)
                or not all(finite_number(row.get(k)) for k in ('start', 'end'))):
            errors.append(label + ': each record needs segment_id and finite start/end')
            continue
        matches = [i for i, gap in enumerate(expected)
                   if gap['segment_id'] == row['segment_id']
                   and abs(gap['start'] - row['start']) <= EPS
                   and abs(gap['end'] - row['end']) <= EPS]
        if len(matches) != 1 or matches[0] in seen:
            errors.append(label + ': unknown, stale or duplicate uncovered range')
            continue
        seen.add(matches[0])
        gap = expected[matches[0]]
        label += f"/{row['segment_id']}@{row['start']}–{row['end']}"
        shot_start, evidence = row.get('shot_start'), row.get('task_evidence')
        if not finite_number(shot_start) or shot_start not in gap['shot_starts']:
            errors.append(label + ': task must reference an actual shot intersecting this gap')
        elif (not concrete_note(evidence) or not any(
                normalized(evidence) in normalized(value)
                for value in final_intervals.get((row['segment_id'], shot_start), []))):
            errors.append(label + ': task_evidence must occur in that final shot')
        comparisons = row.get('comparisons')
        if not isinstance(comparisons, list):
            errors.append(label + ': compare every located adjacent speech window before retaining time')
            continue
        wanted = {(v['segment_id'], v['speech_index']) for v in gap['speech_candidates']}
        actual = set()
        for comparison in comparisons:
            if (not isinstance(comparison, dict)
                    or not isinstance(comparison.get('segment_id'), str)
                    or type(comparison.get('speech_index')) is not int):
                errors.append(label + ': comparison needs segment_id and integer speech_index')
                continue
            key = (comparison['segment_id'], comparison['speech_index'])
            if key not in wanted or key in actual:
                errors.append(label + ': comparison references unknown or repeated adjacent speech')
            actual.add(key)
            if (not isinstance(comparison.get('constraint'), str)
                    or comparison['constraint'] not in CONSTRAINTS
                    or not constraint_basis(comparison.get('basis'))):
                errors.append(label + ': each comparison needs a constraint and concrete basis')
        if actual != wanted:
            errors.append(label + ': comparisons must cover every located adjacent speech window exactly once')
    if seen != set(range(len(expected))):
        missing = ', '.join(f"{gap['segment_id']}@{gap['start']}–{gap['end']}"
                            for i, gap in enumerate(expected) if i not in seen)
        errors.append('delivery_review.timing_overlap: review every live-drama uncovered range '
                      'with adjacent speech; generic timing/after_basis notes do not cover these ranges: ' + missing)
