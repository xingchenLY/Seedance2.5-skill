"""Preserve declared segment props without guessing objects from prose.

An explicit [] records a reviewed absence. Legacy nonempty presentation text
remains a declaration. Source relevance and completeness require content review.
"""
FIELD = '特殊道具'
EMPTY_MARKERS = {'无', '没有', '暂无', '无特殊道具', 'none', 'null', 'n/a'}


def normalized(value):
    return ' '.join(value.split())


def valid_item(value):
    return (isinstance(value, str) and bool(value.strip())
            and '\n' not in value and '\r' not in value
            and normalized(value).lower() not in EMPTY_MARKERS)


def prop_declaration(segment, errors, *, required=False):
    """Return (declared, text). None text means an explicitly empty inventory."""
    label = str(segment.get('id', 'segment')) + '/props'
    presentation = segment.get('presentation', {})
    legacy = presentation.get(FIELD) if isinstance(presentation, dict) else None
    if 'props' in segment:
        items = segment['props']
        if not isinstance(items, list) or any(not valid_item(item) for item in items):
            errors.append(label + ': use a list of concrete single-line prop names; [] only after reviewing no relevant props')
            return True, None
        names = [normalized(item) for item in items]
        if len(names) != len(set(names)):
            errors.append(label + ': repeated prop entries; keep one entry per actual prop')
        text = '、'.join(item.strip() for item in items) if items else None
        if legacy not in (None, ''):
            if not isinstance(legacy, str) or normalized(legacy) != normalized(text or ''):
                errors.append(label + ': props and presentation.特殊道具 disagree; resolve the actual inventory before rendering')
        return True, text
    if legacy not in (None, ''):
        if not valid_item(legacy):
            errors.append(label + ': legacy 特殊道具 must name actual props; reviewed absence uses props: []')
            return True, None
        return True, legacy.strip()
    if required:
        errors.append(label + ': final delivery needs an explicit prop declaration; '
                      'list relevant props or use [] after source review, never infer absence from a missing field')
    return False, None


def derive_prop_field(segment, errors):
    """Derive only a declared list; preserve legacy text and never infer absence."""
    declared, text = prop_declaration(segment, errors)
    if declared and 'props' in segment and isinstance(segment.get('presentation'), dict):
        if text is None:
            # A conflicting nonempty legacy value is an error, never silently removed.
            if not segment['presentation'].get(FIELD):
                segment['presentation'].pop(FIELD, None)
        else:
            segment['presentation'][FIELD] = text


def check_prop_binding(segment, field_rows, errors, *, required=False):
    declared, expected = prop_declaration(segment, errors, required=required)
    actual = dict(field_rows)
    label = str(segment.get('id', 'segment')) + '/props'
    if declared and expected is not None:
        if FIELD not in actual or normalized(actual[FIELD]) != normalized(expected):
            errors.append(label + ': final 特殊道具 is missing or differs from the declared inventory')
    elif FIELD in actual:
        errors.append(label + ': final 特殊道具 is undeclared or contradicts the explicitly empty inventory')
