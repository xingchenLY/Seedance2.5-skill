"""Require current, scoped review decisions; do not certify their semantic truth.

This gate never rewrites a plan or manufactures approvals. Source excerpts are
reviewer-supplied snapshots: checking their authenticity still requires the raw
screenplay, images and prior scene state, independently of the generated plan.
"""
import hashlib
import json

from prop_inventory import prop_declaration
from relation_layouts import name_list, scene_cast
from review_evidence import concrete_note, normalized
from review_report import collect_review_report
from shot_pacing import finite_number


def plan_digest(plan):
    content = {key: value for key, value in plan.items() if key != 'delivery_review'}
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def literal_text(value):
    """Original/literal text is data, not a reviewer's assertion of success."""
    return isinstance(value, str) and bool(value.strip())


def evidence_matches(value, intervals, location):
    return (literal_text(value) and any(normalized(value) in normalized(text)
            for text in intervals.get(location, [])))


def check_review_closure(plan, intervals, errors, *, text=None, segment_texts=None):
    review = plan.get('delivery_review', {})
    if not isinstance(review, dict):
        return  # Existing final-review check reports this.
    if review.get('plan_sha256') != plan_digest(plan):
        errors.append('delivery_review: plan changed or plan_sha256 is missing; review source/state dependencies again')
    report = collect_review_report(plan)
    expected = {item['key']: item for item in report['items']}
    rows = review.get('findings')
    if not isinstance(rows, list):
        errors.append('delivery_review.findings: decisions are required for all current REVIEW candidates; [] only if none')
        rows = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('key'), str):
            errors.append('delivery_review.findings: each decision needs its current finding key')
            continue
        key = row['key']
        if key not in expected or key in seen:
            errors.append(f'delivery_review.findings: stale, unknown or duplicate key {key}')
            continue
        seen.add(key)
        if row.get('decision') not in ('retain', 'false_positive') or not concrete_note(row.get('basis')):
            errors.append(f'{key}: unresolved REVIEW; correct actual errors, otherwise justify retain/false_positive')
        if expected[key]['code'] == 'shoulder_axis' and row.get('decision') == 'retain':
            errors.append(f'{key}: shoulder-axis inconsistency cannot be retained; repair standard reverse shots or substantiate a real false positive')
        evidence = row.get('evidence')
        if not isinstance(evidence, list):
            errors.append(f'{key}: REVIEW needs evidence at every affected shot')
            continue
        locations = set(expected[key]['locations'])
        covered = set()
        for entry in evidence:
            if (not isinstance(entry, dict) or not isinstance(entry.get('segment_id'), str)
                    or not finite_number(entry.get('shot_start'))):
                errors.append(f'{key}: invalid REVIEW evidence location')
                continue
            location = (entry['segment_id'], entry['shot_start'])
            if location not in locations or location in covered:
                errors.append(f'{key}: REVIEW evidence has an unrelated or duplicate shot')
                continue
            covered.add(location)
            if not evidence_matches(entry.get('text'), intervals, location):
                errors.append(f'{key}: REVIEW evidence is absent from its actual final shot')
        if covered != locations:
            errors.append(f'{key}: REVIEW evidence must cover every affected shot, including cross-shot endpoints')
    for key in expected.keys() - seen:
        errors.append(f'{key}: unresolved REVIEW; inspect, repair or record a justified current decision')
    check_source_audit(plan, intervals, review.get('source_audit'), errors)
    if text is not None:
        from editorial_review import check_editorial_review
        check_editorial_review(plan, text, intervals, report, errors, segment_texts=segment_texts)


def check_source_audit(plan, intervals, rows, errors):
    segments = {segment['id']: segment for segment in plan['segments']}
    if not isinstance(rows, list):
        errors.append('delivery_review.source_audit: independently reread the raw source for every segment')
        rows = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('segment_id'), str):
            errors.append('source_audit: each row needs a segment_id')
            continue
        sid = row['segment_id']
        if sid not in segments or sid in seen:
            errors.append(f'source_audit: unknown or duplicate segment {sid}')
            continue
        seen.add(sid)
        segment = segments[sid]
        for field in ('source_ref', 'source_excerpt', 'coverage_basis'):
            check = concrete_note if field == 'coverage_basis' else literal_text
            if not check(row.get(field)):
                errors.append(f'{sid}: source_audit needs concrete {field} from original materials, not generated prose')
        characters = row.get('characters')
        if not name_list(characters) or set(characters) != set(scene_cast(segment)):
            errors.append(f'{sid}: source_audit characters differ from scene roster; repair the actual omission/presence')
        _, props = prop_declaration(segment, errors, required=True)
        if not isinstance(row.get('props'), str) or normalized(row['props']) != normalized(props or '无'):
            errors.append(f'{sid}: source_audit props differ from the declared inventory; repair missing or unsupported props')
        checks = row.get('key_information')
        if not isinstance(checks, list):
            errors.append(f'{sid}: source_audit key_information must list required visual facts; [] needs actual coverage_basis')
            continue
        for check in checks:
            if not isinstance(check, dict) or not finite_number(check.get('shot_start')):
                errors.append(f'{sid}: key_information needs a real shot_start')
                continue
            quote = check.get('source_quote')
            excerpt = row.get('source_excerpt')
            if (not literal_text(quote) or not isinstance(excerpt, str)
                    or normalized(quote) not in normalized(excerpt)):
                errors.append(f'{sid}: key_information source_quote must occur in the independently copied source_excerpt')
            if not evidence_matches(check.get('evidence'), intervals, (sid, check['shot_start'])):
                errors.append(f'{sid}: source key information is absent from the referenced final shot')
    if seen != set(segments):
        errors.append('source_audit: original-source review must cover every delivered segment exactly once')
