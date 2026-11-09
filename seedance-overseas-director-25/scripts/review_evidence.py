"""Validate review scope and evidence binding, not lighting physics or reviewer truth."""
import re
from shot_pacing import finite_number

GENERIC = {
    '已检查', '已复核', '检查通过', '全部通过', '通过', '无', '无冲突', '无问题',
    '已逐项检查', '已逐项复核', '全部已检查', '全部检查通过', '光影全部通过',
    'ok', 'pass', 'passed', 'checked', 'reviewed', 'none', 'noconflicts', 'allpassed',
}


def normalized(text):
    return ' '.join(text.split())


def concrete_note(value):
    if not isinstance(value, str) or not value.strip():
        return False
    compact = re.sub(r'[\W_]+', '', value).lower()
    # Reject compositional sign-offs as well as the exact legacy phrases.
    generic = re.fullmatch(r'(?:(?:本次|此次|本段|本镜|全文|全稿|逐项|全部|已经|已|均|都|经|的|已对|内容|规则|校验|检查|复核|审阅|审查|完成|通过|完毕|没有|未发现|不存在|未见|无|任何|异常|问题|错误|冲突|合格|正常|符合要求))+', compact)
    return compact not in GENERIC and generic is None


def review_context(plan, errors):
    """Require one lightweight lighting receipt per generated segment at final review."""
    review = plan.get('delivery_review', {})
    if not isinstance(review, dict):
        return {}
    for key in ('source_coverage', 'timing', 'visual_continuity', 'conflicts'):
        if not concrete_note(review.get(key)):
            errors.append(f'delivery_review: concrete {key} review notes are required; boilerplate is not a review')
    rows = review.get('lighting')
    if not isinstance(rows, list):
        errors.append('delivery_review.lighting: final review needs segment-scoped lighting evidence; review the rendered text')
        return {}
    segments = {s['id']: s for s in plan['segments']}
    indexed = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('segment_id'), str):
            errors.append('delivery_review.lighting: each record needs a segment_id')
            continue
        sid = row['segment_id']
        if sid not in segments or sid in indexed:
            errors.append(f'delivery_review.lighting: unknown or repeated segment {sid}')
            continue
        indexed[sid] = row
        for key in ('anchor', 'basis', 'continuity'):
            if not concrete_note(row.get(key)):
                errors.append(f'{sid}: lighting review needs concrete {key}')
        checked = row.get('checked_shots')
        expected = [s['start'] for s in segments[sid]['shots']]
        if (not isinstance(checked, list) or any(not finite_number(n) for n in checked)
                or checked != expected):
            errors.append(f'{sid}: lighting checked_shots must cover all actual shot starts in order')
        changes = row.get('changes')
        if not isinstance(changes, list):
            errors.append(f'{sid}: lighting changes must be a list; [] only for reviewed stable lighting')
            continue
        seen = set()
        for change in changes:
            if not isinstance(change, dict):
                errors.append(f'{sid}: lighting change must be an object')
                continue
            start = change.get('shot_start')
            if not finite_number(start) or start not in expected or start in seen:
                errors.append(f'{sid}: lighting change needs a unique actual shot_start')
            else:
                seen.add(start)
            for key in ('evidence', 'basis'):
                if not concrete_note(change.get(key)):
                    errors.append(f'{sid}: lighting change needs concrete {key}')
    if set(indexed) != set(segments):
        errors.append('delivery_review.lighting: evidence must cover every generated segment exactly once')
    return indexed


def check_lighting_anchor(row, prefix_fields, sid, errors):
    if row is None or not isinstance(row.get('anchor'), str):
        return
    actual = dict(prefix_fields).get('环境光影', '')
    if normalized(row['anchor']) != normalized(actual):
        errors.append(f'{sid}: lighting review anchor differs from final 环境光影; review the actual text')


def check_lighting_change(row, shot, scoped_visual_values, sid, errors):
    if row is None or not isinstance(row.get('changes'), list):
        return
    for change in row['changes']:
        if not isinstance(change, dict) or change.get('shot_start') != shot['start']:
            continue
        evidence = change.get('evidence')
        if isinstance(evidence, str) and evidence.strip() and not any(
                normalized(evidence) in normalized(value) for value in scoped_visual_values):
            errors.append(f'{sid}/{shot["start"]}: lighting change evidence is absent from its actual shot')
