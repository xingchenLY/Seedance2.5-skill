"""Validate explicit name-only source edits, not brand/place recognition or legality.

Raw sources remain in the existing source inventory. This helper never rewrites
the authored plan, invents aliases, or certifies the semantic scope of an edit.
"""


def adapted_name_text(source):
    record = source.get('name_adaptation')
    if not isinstance(record, dict):
        raise ValueError('name_adaptation must be an object')
    before = record.get('before_text')
    if not isinstance(before, str) or not before.strip():
        raise ValueError('name_adaptation.before_text must preserve the complete pre-edit text')
    edits = record.get('replacements')
    if not isinstance(edits, list) or not edits:
        raise ValueError('name_adaptation.replacements must be a nonempty list')
    cursor, parts = 0, []
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError('name_adaptation replacement must be an object')
        start, end = edit.get('start'), edit.get('end')
        if (type(start) is not int or type(end) is not int
                or not 0 <= cursor <= start < end <= len(before)):
            raise ValueError('name_adaptation ranges must be ordered, nonoverlapping character spans')
        original, replacement = edit.get('original'), edit.get('replacement')
        if not isinstance(original, str) or not original.strip() or original != before[start:end]:
            raise ValueError('name_adaptation original differs from the specified source span')
        if not isinstance(replacement, str) or not replacement.strip() or replacement == original:
            raise ValueError('name_adaptation replacement must be nonempty and change the name')
        if edit.get('kind') not in ('brand', 'real_place'):
            raise ValueError('name_adaptation kind must be brand or real_place')
        if not isinstance(edit.get('basis'), str) or not edit['basis'].strip():
            raise ValueError('name_adaptation needs the semantic basis for the bounded replacement')
        parts.extend((before[cursor:start], replacement))
        cursor = end
    parts.append(before[cursor:])
    return ''.join(parts)


def check_source_name_adaptations(plan, errors):
    for source in plan.get('sources', []):
        if 'name_adaptation' not in source:
            continue
        label = source.get('id', 'source')
        try:
            expected = adapted_name_text(source)
        except ValueError as exc:
            errors.append(f'{label}: {exc}')
            continue
        if source.get('text') != expected:
            errors.append(f'{label}: source text differs from the bounded name adaptation')
