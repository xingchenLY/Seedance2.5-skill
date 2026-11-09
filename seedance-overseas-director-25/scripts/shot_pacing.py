"""Check authored shot durations and static display spans, not visual semantics."""
from decimal import Decimal
import math

EPS = 1e-7


def finite_number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def check_segment_duration(segment, label, errors, required=False):
    duration = segment['duration']
    if required and duration != math.floor(duration):
        errors.append(f'{label}: final segment duration must be an integer; reconcile shot and voice ranges before delivery')
    feasibility = segment.get('timing_feasibility')
    if feasibility is not None:
        if (not isinstance(feasibility, dict)
                or not finite_number(feasibility.get('minimum_seconds'))
                or feasibility['minimum_seconds'] < 0
                or not isinstance(feasibility.get('basis'), str) or not feasibility['basis'].strip()):
            errors.append(f'{label}: timing_feasibility needs minimum_seconds and a concrete basis')
        elif feasibility['minimum_seconds'] > duration + EPS:
            errors.append(f'{label}: timing infeasible: irreducible content exceeds integer duration; regroup or report the conflict')
    for voice in segment.get('speech', []):
        measured = voice.get('measured_seconds')
        if measured is None:
            continue
        if (not finite_number(measured) or measured <= 0
                or not isinstance(voice.get('measurement_basis'), str) or not voice['measurement_basis'].strip()):
            errors.append(f'{label}: measured speech needs positive measured_seconds and measurement_basis')
        elif (measured > voice['end'] - voice['start'] + EPS
              or measured > voice['estimated_seconds'] + EPS):
            errors.append(f'{label}: measured audio cannot be shortened to fit its slot or estimate')
    if 'pre_round_duration' not in segment:
        return
    raw = segment['pre_round_duration']
    if not finite_number(raw) or raw < 1:
        errors.append(f'{label}: pre_round_duration must be a finite number of at least 1 second')
    elif duration != math.floor(raw):
        errors.append(f'{label}: duration must equal truncated pre_round_duration, without rounding up')


def check_shot_pacing(shot, label, errors, required=False):
    from closeup_pacing import check_closeups
    check_closeups(shot, label, errors)
    short = shot['end'] - shot['start'] < 1 - EPS
    exception = shot.get('short_shot')
    if short:
        if (not isinstance(exception, dict)
                or exception.get('reason') not in ('rapid_reply', 'brief_reaction', 'action_accent', 'flashback_insert')
                or any(not isinstance(exception.get(k), str) or not exception[k].strip()
                       for k in ('basis', 'readability'))):
            errors.append(f'{label}: actual shot is shorter than 1 second; needs a scoped short_shot reason, basis and readability')
    elif exception is not None:
        errors.append(f'{label}: short_shot applies only to a shot shorter than 1 second')
    if 'static_spans' not in shot:
        if required:
            errors.append(f'{label}: static_spans is required; use [] only after content review')
        return
    spans = shot['static_spans']
    if not isinstance(spans, list):
        errors.append(f'{label}: static_spans must be a list')
        return
    previous_end = None
    for span in spans:
        if not isinstance(span, dict) or set(span) != {'start', 'end', 'text'}:
            errors.append(f'{label}: static span needs start, end and text')
            continue
        start, end, text = span['start'], span['end'], span['text']
        if not all(finite_number(t) for t in (start, end)):
            errors.append(f'{label}: static span times must be finite numbers')
            continue
        if start < shot['start'] - EPS or end > shot['end'] + EPS or end <= start:
            errors.append(f'{label}: static span is outside its shot or has invalid duration')
        if any(abs(t * 10 - round(t * 10)) > EPS for t in (start, end)):
            errors.append(f'{label}: static span boundaries must use 0.1-second or coarser units')
        if end - start > 2 + EPS:
            errors.append(f'{label}: static display exceeds 2 seconds')
        if previous_end is not None and start <= previous_end + EPS:
            errors.append(f'{label}: static spans overlap, are unordered or touch; merge uninterrupted holds')
        previous_end = end
        if (not isinstance(text, str) or not text.strip() or '\n' in text or '\r' in text):
            errors.append(f'{label}: static span text must describe the visible hold on one line')


def fmt(value):
    result = format(Decimal(str(value)), 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def static_span_prose(shot):
    errors = []
    check_shot_pacing(shot, 'shot', errors, required=True)
    if errors:
        raise ValueError('\n'.join(errors))
    return ''.join(f'第{fmt(span["start"])}秒至第{fmt(span["end"])}秒，'
                   + span['text'].strip().rstrip('。.!！；;') + '。'
                   for span in shot['static_spans'])


def check_static_binding(shot, visual_values, label, errors):
    try:
        evidence = static_span_prose(shot)
    except ValueError as exc:
        errors.append(f'{label}: {exc}')
        return
    if evidence and not any(' '.join(evidence.split()) in ' '.join(value.split())
                            for value in visual_values):
        errors.append(f'{label}: static display text/times are absent from this final visual interval')
