"""Bind mode-sensitive production fields and check actual final voice directions.

The selected mode is authoritative; never infer it from final prose. Spoken text
inside quotes is not scanned as production instructions. Semantic acting and
reference-image fidelity still need the existing content review.
"""
from mode_rules import (require_mode, allowed_voice_kinds, presentation_defaults,
                        check_visual_mode, check_narration_tone)
from language_policy import language_clauses, check_language_presentation


def normalized(value):
    return ' '.join(value.split())


def check_final_presentation(plan, segment, field_rows, errors):
    if 'mode' not in plan:
        return  # Private legacy regression fixtures, never a public CLI default.
    require_mode(plan)
    actual = dict(field_rows)
    label = f'final/{segment["id"]}/{plan["mode"]}'
    expected = {**presentation_defaults(plan), **segment.get('presentation', {})}
    keys = ['整体风格', '画面规格', '声音限制']
    if language_clauses(plan):
        keys.append('画面限制')
    for key in keys:
        if key not in expected:
            errors.append(f'{label}: {key} needs an authored value for final mode binding')
        elif normalized(actual.get(key, '')) != normalized(expected[key]):
            errors.append(f'{label}: final {key} differs from the selected-mode plan; update the design and render again')
    check_visual_mode(plan, actual, label, errors)
    check_language_presentation(plan, actual, label, errors)


def check_final_voice(plan, kind, directions, label, errors):
    if 'mode' not in plan:
        return
    allowed = allowed_voice_kinds(plan)
    tag = f'final/{label}/{plan["mode"]}'
    if kind not in allowed:
        errors.append(f'{tag}: final voice kind {kind} is incompatible with selected mode')
        return
    # Characters in dialogue, VO, OS and inner voices keep their story emotion.
    # Only narrative narration in the two narrative modes must be neutral.
    from narration_mouth import check_voice_directions
    check_voice_directions(kind, directions, tag, errors, final=True)
    if kind == 'narration':
        check_narration_tone(directions, tag, errors)
        from narration_flow import check_narration_direction
        check_narration_direction(directions, tag, errors)
