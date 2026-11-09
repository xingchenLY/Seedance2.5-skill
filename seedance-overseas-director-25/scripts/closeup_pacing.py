"""Check actual closeup intervals; necessity and framing still need content review."""
from decimal import Decimal
import math

from narration_editing import empty_hold_reason, nonempty

EPS = 1e-7
CLOSEUPS = {'面部特写', '手部特写', '动作特写', '道具特写'}
REASONS = {'reading', 'continuous_action', 'performance_turn', 'user_requested'}


def generic_reason(value):
    return empty_hold_reason(value) or value.strip().rstrip('。.!！；;') in {
        '情绪强烈', '保持压迫感', '没有听者', '参考图适合', '摄影机仍在移动', '微表情变化'}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def check_closeups(shot, label, errors):
    first = shot.get('start_scale') in CLOSEUPS
    last = shot.get('end_scale') in CLOSEUPS
    spans = shot.get('closeup_spans')
    if 'closeup_spans' not in shot:
        if first and last:
            if shot['end'] - shot['start'] > 2 + EPS:
                errors.append(f'{label}: closeup exceeds 2 seconds; shorten its actual interval or record concrete necessity in closeup_spans')
        elif (first or last) and shot['end'] - shot['start'] > 2 + EPS:
            errors.append(f'{label}: changing into/out of closeup needs explicit closeup_spans')
        return
    if not isinstance(spans, list):
        errors.append(f'{label}: closeup_spans must be a list')
        return
    if not spans:
        if first or last:
            errors.append(f'{label}: closeup endpoint cannot have empty closeup_spans')
        return
    valid = []
    previous = None
    for span in spans:
        if not isinstance(span, dict):
            errors.append(f'{label}: closeup span must be an object')
            continue
        start, end = span.get('start'), span.get('end')
        if not finite(start) or not finite(end):
            errors.append(f'{label}: closeup span times must be finite numbers')
            continue
        valid.append(span)
        if start < shot['start'] - EPS or end > shot['end'] + EPS or end <= start:
            errors.append(f'{label}: closeup span is outside its shot or has invalid duration')
        if any(abs(t * 10 - round(t * 10)) > EPS for t in (start, end)):
            errors.append(f'{label}: closeup boundaries must use 0.1-second or coarser units')
        if previous is not None and start <= previous + EPS:
            errors.append(f'{label}: closeup spans overlap, are unordered or touch; merge continuous closeup framing')
        previous = end
        for key in ('task', 'finish_evidence'):
            if not nonempty(span.get(key)):
                errors.append(f'{label}: closeup span needs concrete {key}')
        necessity = span.get('necessity')
        if end - start <= 2 + EPS:
            if necessity is not None:
                errors.append(f'{label}: closeup necessity applies only beyond 2 seconds')
            continue
        if not isinstance(necessity, dict):
            errors.append(f'{label}: closeup exceeds 2 seconds without concrete necessity')
            continue
        if not isinstance(necessity.get('reason'), str) or necessity['reason'] not in REASONS:
            errors.append(f'{label}: closeup necessity needs a supported reason')
        for key in ('basis', 'next_view', 'loss_if_cut'):
            if not nonempty(necessity.get(key)) or generic_reason(necessity[key]):
                errors.append(f'{label}: closeup necessity needs concrete {key}; speech continuity or generic tension is insufficient')
        at = necessity.get('cut_at')
        if not finite(at) or not start < at < end or at > start + 2 + EPS:
            errors.append(f'{label}: closeup necessity must compare an actual cut within the first 2 seconds')
        if necessity.get('reason') == 'user_requested' and not nonempty(necessity.get('user_basis')):
            errors.append(f'{label}: user-requested closeup extension needs actual user_basis')
    if len(valid) != len(spans):
        return
    starts_at_edge = abs(valid[0]['start'] - shot['start']) <= EPS
    ends_at_edge = abs(valid[-1]['end'] - shot['end']) <= EPS
    # A fixed closeup cannot claim only a short interior fragment. A real
    # departure/return needs separate spans and a visible wider phase in camera_text.
    if first != starts_at_edge or last != ends_at_edge:
        errors.append(f'{label}: closeup spans disagree with actual start/end scales')


def fmt(value):
    result = format(Decimal(str(value)), 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def closeup_prose(shot):
    errors = []
    check_closeups(shot, 'shot', errors)
    if errors:
        raise ValueError('\n'.join(errors))
    return ''.join(f'第{fmt(s["start"])}秒至第{fmt(s["end"])}秒为特写取景。'
                   for s in shot.get('closeup_spans', []))


def check_closeup_binding(shot, camera_values, content_values, label, errors):
    try:
        prose = closeup_prose(shot)
    except ValueError as exc:
        errors.append(f'{label}: {exc}')
        return
    normalize = lambda value: ' '.join(value.split())
    if prose and not any(normalize(prose) in normalize(value) for value in camera_values):
        errors.append(f'{label}: closeup intervals are absent from this final camera interval')
    for span in shot.get('closeup_spans', []):
        if not any(normalize(span['finish_evidence']) in normalize(value) for value in content_values):
            errors.append(f'{label}: closeup finish_evidence is absent from this final visual interval')
