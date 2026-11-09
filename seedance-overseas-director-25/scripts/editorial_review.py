"""Bind a separate editorial pass and implemented changes to the final version.

Records cannot prove reviewer independence or the truth of artistic judgments;
the execution agent must actually perform the documented review workflow.
"""
import hashlib

from relation_layouts import name_list
from review_evidence import concrete_note, normalized
from shot_pacing import finite_number


def check_editorial_review(plan, text, intervals, report, errors, *, segment_texts=None):
    from review_closure import evidence_matches, plan_digest
    record = plan.get('delivery_review', {}).get('editorial_review')
    if not isinstance(record, dict):
        errors.append('editorial_review: a separate final-text audit and implemented improvements are required')
        return
    method = record.get('method')
    if method not in ('independent_agent', 'separate_pass') or not concrete_note(record.get('reviewer_ref')):
        errors.append('editorial_review: record the actual review method and reviewer/run reference')
    if method == 'separate_pass' and not concrete_note(record.get('fallback_basis')):
        errors.append('editorial_review: separate_pass needs the actual reason an independent reviewer was unavailable')
    if (record.get('plan_sha256') != plan_digest(plan)
            or record.get('prompt_sha256') != hashlib.sha256(text.encode('utf-8')).hexdigest()):
        errors.append('editorial_review: reviewer must recheck the current design and final text after repairs')
    expected_segments = {s['id'] for s in plan['segments']}
    reviewed = record.get('reviewed_segments')
    if not name_list(reviewed) or set(reviewed) != expected_segments:
        errors.append('editorial_review: final audit must cover every delivered segment')
    keys = record.get('finding_keys')
    if not name_list(keys) or set(keys) != {i['key'] for i in report['items']}:
        errors.append('editorial_review: separate auditor must assess all current REVIEW decisions')
    if not concrete_note(record.get('scan_basis')):
        errors.append('editorial_review: describe the actual whole-text audit, including non-triggered rules')
    issues = record.get('issues')
    if not isinstance(issues, list):
        errors.append('editorial_review: issues must include every confirmed error and improvement; [] only after actual audit')
        return
    seen = set()
    for row in issues:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'].strip():
            errors.append('editorial_review: each confirmed issue needs an id')
            continue
        label = 'editorial_review/' + row['id']
        if row['id'] in seen:
            errors.append(label + ': duplicate issue')
        seen.add(row['id'])
        if row.get('kind') not in ('error', 'improvement') or row.get('status') != 'applied':
            errors.append(label + ': confirmed errors and improvements must be applied, not deferred or retained')
        for field in ('rule_or_goal', 'change', 'verification'):
            if not concrete_note(row.get(field)):
                errors.append(label + f': concrete {field} is required')
        before = row.get('before')
        if not isinstance(before, str) or not before.strip():
            errors.append(label + ': original problem excerpt is required')
        after = row.get('after')
        if not isinstance(after, list) or not after:
            errors.append(label + ': applied change needs actual final-text evidence')
            continue
        for entry in after:
            if not isinstance(entry, dict) or not isinstance(entry.get('segment_id'), str):
                errors.append(label + ': invalid final-text evidence location')
                continue
            value = entry.get('text')
            if entry.get('scope') == 'segment':
                block = (segment_texts or {}).get(entry['segment_id'], '')
                if (not isinstance(value, str) or not value.strip()
                        or not any(mark in value for mark in (':', '：'))
                        or normalized(value) not in normalized(block)):
                    errors.append(label + ': segment evidence must quote the changed field/voice with its label in this segment')
            elif entry.get('scope', 'shot') != 'shot' or not finite_number(entry.get('shot_start')):
                errors.append(label + ': invalid final-shot evidence location')
            elif not evidence_matches(value, intervals, (entry['segment_id'], entry['shot_start'])):
                errors.append(label + ': applied change evidence is absent from its final shot')
            if isinstance(before, str) and isinstance(value, str) and normalized(before) == normalized(value):
                errors.append(label + ': unchanged excerpt cannot demonstrate an applied improvement')
