"""Deterministic delivery checks. No network or file writes; semantic review remains human/model work."""

import hashlib
from appearance_review import check_appearance, scope_time
from content_lint import check_explicit_contradictions
from review_evidence import review_context, check_lighting_anchor, check_lighting_change
from sound_timing import sound_context, check_sound_binding
from segment_numbering import segment_labels
from mode_rules import overlap_allowed, parsed_voice_kind, voice_reference, check_narration_binding, narrative_mode
from mode_output_checks import check_final_presentation, check_final_voice
import re

from relation_layouts import check_layout_binding, scene_cast, check_scene_positions
from speech_continuity import speech_context, check_speech_carry_binding
from speech_output_guard import check_quoted_speech_copies, check_voice_execution, check_restart_prose
from narration_mouth import parse_display_speaker, check_isolation_binding, check_shot_mouth
from narration_editing import check_internal_cut, check_segment_claims, check_final_narration_editing
from narration_coverage import check_coverage_binding
from dialogue_reactions import check_dialogue_coverage, check_reception_binding
from action_continuity import action_context, check_action_binding
from cut_transitions import allow_jump_cut, jump_record_error, check_jump_binding
from shot_pacing import check_shot_pacing, check_static_binding, check_segment_duration
from closeup_pacing import check_closeup_binding
from camera_views import endpoint_view, view_error, physical_key, validate_views, check_view_binding
from cut_boundary_checks import boundary_context, check_boundary_binding

EPS = 1e-7
SCALES = {'远景', '全景', '中全景', '中景', '中近景', '近景', '面部特写', '手部特写', '动作特写', '道具特写'}
CONDITIONAL_CUTS = {('中近景', '中景'), ('中近景', '近景'), ('近景', '中近景'), ('中景', '中近景')}
SHOT_FIELDS = ('frame_id', 'start_scale', 'end_scale', 'start_camera', 'end_camera', 'visible_characters')
SUBJECT_FIELDS = ('start_subject', 'end_subject')
RELATION_MODES = {'same_scene', 'remote', 'solo', 'insert', 'empty'}
PREFIX_FIELDS = ['整体风格', '对话氛围', '画面规格', '声音限制', '画面限制', '场景', '环境光影', '出镜人物', '特殊道具', '人物位置关系']
HEADER = re.compile(r'^第\s*\S+?\s*段｜预估时长\s*([0-9]+(?:\.[0-9]+)?)\s*秒｜发声词数\s*(\d+)\s*$', re.M)
FRAME = re.compile(r'^(分镜[^（\n]+)（\s*([0-9]+(?:\.[0-9]+)?)\s*[–—-]\s*([0-9]+(?:\.[0-9]+)?)\s*秒）\s*$', re.M)
VOICE = re.compile(r'^（(?P<speaker>[^（）\r\n]+)）(?P<label>台词参考|VO 参考|OS 参考|旁白参考)'
                   r'（(?P<reference>[^（）\r\n]+)）音色：(?P<content>.*)$')
VOICE_KINDS = {'台词参考': 'dialogue', 'VO 参考': 'VO', 'OS 参考': 'OS'}
RANGE_PATTERN = r'(?P<start>-?\d+(?:\.\d+)?)\s*(?:秒\s*)?[–—-]\s*(?P<end>-?\d+(?:\.\d+)?)\s*秒'
TIME_RANGE = re.compile(RANGE_PATTERN)
VISUAL_RANGE = re.compile(r'(?:^|[；;。])\s*' + RANGE_PATTERN)


def normalized(text):
    return ' '.join(text.split())


def names(value, label):
    if not isinstance(value, list) or any(not isinstance(n, str) or not n.strip() for n in value):
        raise ValueError(f'{label}: expected a list of nonempty names')
    if len(value) != len(set(value)):
        raise ValueError(f'{label}: duplicate names')
    return set(value)


def frame_groups(segment):
    groups = []
    for shot in segment['shots']:
        fid = shot.get('frame_id')
        if not groups or groups[-1]['id'] != fid:
            groups.append(dict(id=fid, start=shot['start'], end=shot['end']))
        else:
            groups[-1]['end'] = shot['end']
    return groups


def validate_visual_plan(plan, errors, required=False):
    segments = {s['id']: s for s in plan['segments']}
    enabled = required or any(any(key in shot for key in SHOT_FIELDS + SUBJECT_FIELDS + ('jump_cut_before',))
                              for s in segments.values() for shot in s['shots'])
    if not enabled:
        return
    complete = True
    for sid, segment in segments.items():
        check_segment_duration(segment, sid, errors, required=required)
        visible = set()
        for shot in segment['shots']:
            check_shot_pacing(shot, f'{sid}/{shot["start"]}', errors, required=required)
            if 'jump_cut_before' in shot:
                issue = jump_record_error(shot['jump_cut_before'])
                if issue:
                    errors.append(f'{sid}: {issue}')
            missing = [field for field in SHOT_FIELDS if field not in shot]
            if missing:
                errors.append(f'{sid}: delivery shot fields missing: {", ".join(missing)}')
                complete = False
                continue
            for field in SHOT_FIELDS[:-1]:
                if not isinstance(shot[field], str) or not shot[field].strip():
                    raise ValueError(f'{sid}/{field}: expected a nonempty string')
            if shot['start_scale'] not in SCALES or shot['end_scale'] not in SCALES:
                errors.append(f'{sid}: unknown or prohibited shot scale')
            shot_visible = names(shot['visible_characters'], f'{sid}.visible_characters')
            visible |= shot_visible
            if required:
                validate_views(shot, f'{sid}/{shot["frame_id"]}', errors)
            for field in SUBJECT_FIELDS:
                if field not in shot:
                    continue
                if not isinstance(shot[field], str) or not shot[field].strip():
                    raise ValueError(f'{sid}/{field}: expected a nonempty primary-character name')
                if shot[field] not in shot_visible:
                    errors.append(f'{sid}/{field}: primary character is not in visible_characters')
        if 'cast' not in segment:
            errors.append(f'{sid}: delivery cast is required')
        elif names(segment['cast'], f'{sid}.cast') != set(scene_cast(segment)):
            errors.append(f'{sid}: cast differs from scene-character union')
        groups = frame_groups(segment)
        identifiers = [group['id'] for group in groups]
        if len(identifiers) != len(set(identifiers)):
            errors.append(f'{sid}: frame_id must form one continuous range')
        exceptions = segment.get('frame_speaker_exceptions', {})
        if not isinstance(exceptions, dict):
            errors.append(f'{sid}: frame_speaker_exceptions must be an object keyed by frame_id')
            exceptions = {}
        for fid in exceptions:
            if fid not in identifiers:
                errors.append(f'{sid}: speaker exception refers to an unknown frame_id')
        for group in groups:
            voices = [v for v in segment['speech']
                      if v['start'] < group['end'] - EPS and v['end'] > group['start'] + EPS]
            kinds = {v['kind'] for v in voices}
            cross_category = overlap_allowed(plan, segment, voices, group['id'])
            if len(kinds) > 1 and not cross_category:
                errors.append(f'{sid}/{group["id"]}: one template frame contains multiple voice kinds')
            speakers = {v['speaker'] for v in voices}
            justified = False
            if group['id'] in exceptions:
                exception = exceptions[group['id']]
                if not isinstance(exception, dict):
                    errors.append(f'{sid}/{group["id"]}: speaker exception must contain basis and source_ids')
                else:
                    basis = exception.get('basis')
                    source_ids = exception.get('source_ids')
                    valid_basis = isinstance(basis, str) and bool(basis.strip())
                    valid_ids = (isinstance(source_ids, list) and bool(source_ids) and
                                 all(isinstance(item, str) and item.strip() for item in source_ids) and
                                 len(source_ids) == len(set(source_ids)))
                    if not valid_basis or not valid_ids:
                        errors.append(f'{sid}/{group["id"]}: speaker exception needs a nonempty basis and unique source_ids')
                    elif set(source_ids) != {v['source_id'] for v in voices}:
                        errors.append(f'{sid}/{group["id"]}: speaker exception source_ids differ from actual frame speech')
                    elif len(speakers) <= 1:
                        errors.append(f'{sid}/{group["id"]}: speaker exception has no multiple-speaker case')
                    else:
                        justified = True
            if len(speakers) > 1 and not justified and not cross_category:
                errors.append(f'{sid}/{group["id"]}: multiple speakers in one template frame require a scoped exception')
    if not complete:
        return
    clips = plan.get('playback', [dict(segment_id=sid, start=0, end=s['duration'])
                                  for sid, s in segments.items()])
    previous = None
    for clip in clips:
        segment = segments.get(clip['segment_id'])
        if segment is None:
            continue
        for shot in segment['shots']:
            if shot['start'] < clip['start'] - EPS or shot['end'] > clip['end'] + EPS:
                continue
            jump_allowed = allow_jump_cut(segment, shot, previous, errors)
            if previous and previous[0] == segment['episode']:
                before = previous[1]
                pair = (before['end_scale'], shot['start_scale'])
                same_camera = before['end_camera'] == shot['start_camera']
                end_subject, start_subject = before.get('end_subject'), shot.get('start_subject')
                # The visible cast may be identical in reverse shots. Only explicit,
                # visible primary characters can establish a change of subject.
                changed_subject = (end_subject in before['visible_characters'] and
                                   start_subject in shot['visible_characters'] and
                                   end_subject != start_subject)
                if required and (pair in CONDITIONAL_CUTS or pair[0] == pair[1]):
                    left, right = endpoint_view(before, 'end'), endpoint_view(shot, 'start')
                    left_error = view_error(left, before['visible_characters'])
                    right_error = view_error(right, shot['visible_characters'])
                    if left_error or right_error:
                        errors.append(f'{previous[2]} -> {segment["id"]}: camera endpoint views required for cut '
                                      f'{pair[0]} -> {pair[1]}; camera IDs alone cannot authorize it')
                    else:
                        same_camera = physical_key(left) == physical_key(right)
                        changed_subject = (left['subject'] is not None and right['subject'] is not None
                                           and left['subject'] != right['subject'])
                if pair in CONDITIONAL_CUTS and same_camera and not changed_subject and not jump_allowed:
                    errors.append(f'{segment["id"]}: forbidden playback cut {pair[0]} -> {pair[1]}: '
                                  'same camera without a verified primary-character change')
                if previous[2] != segment['id'] and pair[0] == pair[1]:
                    errors.append(f'{previous[2]} -> {segment["id"]}: same scale across prompt boundary ({pair[0]})')
                if pair[0] == pair[1] and same_camera and not jump_allowed:
                    errors.append(f'{segment["id"]}: same scale and camera at adjacent playback cut')
            previous = (segment['episode'], shot, segment['id'])
    boundary_context(plan, errors, required=required)


def field_rows(text):
    return [(m.group(1), m.group(2).strip())
             for m in re.finditer(r'^([^\n：]{1,40})：(.*)$', text, re.M)]


def validate_relation(shot, visual_values, label, errors):
    """Check per-shot evidence binding, not the physical truth of a composition."""
    relation = shot.get('relation')
    if not isinstance(relation, dict):
        errors.append(f'{label}: per-shot relation record is required')
        return
    mode = relation.get('mode')
    if not isinstance(mode, str) or mode not in RELATION_MODES:
        errors.append(f'{label}: unknown relation mode')
        return
    characters = relation.get('characters')
    if (not isinstance(characters, list) or
            any(not isinstance(name, str) or not name.strip() for name in characters) or
            len(set(characters)) != len(characters)):
        errors.append(f'{label}: relation characters must be unique nonempty names')
        return
    characters = set(characters)
    visible = set(shot.get('visible_characters', []))
    if not visible <= characters:
        errors.append(f'{label}: relation characters omit visible characters')
    if mode == 'same_scene' and (len(characters) < 2 or not visible):
        errors.append(f'{label}: same_scene relation needs at least two characters and a visible participant')
    elif mode == 'remote' and (len(characters) < 2 or not visible):
        errors.append(f'{label}: remote relation needs both ends and a visible participant')
    elif mode == 'solo' and (len(characters) != 1 or visible != characters):
        errors.append(f'{label}: solo relation needs exactly one visible character')
    elif mode == 'empty' and (characters or visible):
        errors.append(f'{label}: empty relation cannot contain characters')
    evidence = relation.get('evidence')
    if not isinstance(evidence, str) or not evidence.strip():
        errors.append(f'{label}: relation evidence is required')
        return
    # Only this frame's visual fields count: names in the cast or dialogue do not.
    if not any(normalized(evidence) in normalized(value) for value in visual_values):
        errors.append(f'{label}: relation evidence is absent from final visual fields')
    if any(name not in evidence for name in characters):
        errors.append(f'{label}: relation evidence does not identify all recorded characters')


def visual_intervals(value, shots, label, errors):
    """Bind timed clauses to actual shots; a single shot can contain timed action phases."""
    markers = list(VISUAL_RANGE.finditer(value))
    ranges = [(float(m.group('start')), float(m.group('end'))) for m in markers]
    expected = [(s['start'], s['end']) for s in shots]
    if len(shots) > 1:
        if (len(ranges) != len(expected) or
                any(abs(a-c) > EPS or abs(b-d) > EPS for (a, b), (c, d) in zip(ranges, expected)) or
                (markers and value[:markers[0].start()].strip())):
            errors.append(f'{label}: final internal shot intervals missing or differ from plan')
    elif any(a < expected[0][0] - EPS or b > expected[0][1] + EPS or b <= a for a, b in ranges):
        errors.append(f'{label}: final visual time range is outside its shot')
    if len(shots) == 1:
        return [value]
    return [value[m.end():markers[i+1].start('start') if i+1 < len(markers) else len(value)].strip()
            for i, m in enumerate(markers)]


def validate_delivery(plan, text, *, require_review=True):
    """Bind numeric/cast/speech/frame records to the exact reviewed final text."""
    errors = []
    validate_visual_plan(plan, errors, required=True)
    narration_coverage = check_final_narration_editing(plan, errors)
    reactions = check_dialogue_coverage(plan, errors)
    voice_context = speech_context(plan, errors)
    from narration_flow import check_narration_flow
    check_narration_flow(plan, errors)
    actions = action_context(plan, errors, required=True)
    sound_errors = []
    sound_context(plan, sound_errors)
    errors.extend(sound_errors)
    review = plan.get('delivery_review')
    lighting_reviews = {}
    if require_review and not isinstance(review, dict):
        errors.append('delivery_review: final manual review record is required')
    elif require_review:
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        if review.get('prompt_sha256') != digest:
            errors.append('delivery_review: final prompt changed after review or hash is missing')
        lighting_reviews = review_context(plan, errors)
    headers = list(HEADER.finditer(text))
    if len(headers) != len(plan['segments']):
        errors.append('final prompt: segment count/header format differs from plan')
        return errors
    try:
        segment_numbers = segment_labels(plan)
    except ValueError as exc:
        return errors + [str(exc)]
    final_intervals = {}
    final_segments = {}
    for index, (header, segment) in enumerate(zip(headers, plan['segments'])):
        label = segment['id']
        printed_number = re.match(r'第\s*(.*?)\s*段', header.group(0)).group(1)
        if printed_number != str(segment_numbers[label]):
            errors.append(f'{label}: final segment numbering differs from episode order')
        if not header.group(1).isdigit():
            errors.append(f'{label}: printed segment duration must use whole seconds without decimals')
        if abs(float(header.group(1)) - segment['duration']) > EPS or int(header.group(2)) != segment['english_words']:
            errors.append(f'{label}: final duration or word count differs from plan')
        block = text[header.end():headers[index+1].start() if index+1 < len(headers) else len(text)]
        final_segments[label] = block
        frames = list(FRAME.finditer(block))
        prefix = block[:frames[0].start()] if frames else block
        fields = field_rows(prefix)
        check_quoted_speech_copies(plan, segment,
                                  [v for k, v in fields if k not in ('场景', '出镜人物')],
                                  label + '/prefix', errors)
        appearance_scopes = {'prefix:' + key: value for key, value in fields
                             if key not in ('场景', '出镜人物')}
        check_segment_claims(segment, [value for _, value in fields], label, errors)
        check_final_presentation(plan, segment, fields, errors)
        from prop_inventory import check_prop_binding
        check_prop_binding(segment, fields, errors, required=require_review and 'mode' in plan)
        check_lighting_anchor(lighting_reviews.get(label), fields, label, errors)
        actual = [key for key, value in fields]
        expected = [key for key in PREFIX_FIELDS if key != '特殊道具' or key in actual]
        if actual != expected or any(not value for _, value in fields):
            errors.append(f'{label}: final segment fields missing, empty, reordered or unexpected')
        cast_text = dict(fields).get('出镜人物', '')
        actual_cast = [] if cast_text == '无' else [n.strip() for n in re.split(r'[、，,]', cast_text)]
        if len(actual_cast) != len(set(actual_cast)) or set(actual_cast) != set(segment.get('cast', [])):
            errors.append(f'{label}: final cast differs from plan')
        check_scene_positions(segment, dict(fields).get('人物位置关系'), label, errors)
        groups = frame_groups(segment)
        if len(frames) != len(groups):
            errors.append(f'{label}: final frame count differs from plan')
            continue
        actual_speech = []
        expected_speech = []
        for fi, (frame, group) in enumerate(zip(frames, groups)):
            fid = frame.group(1).strip()
            if (fid != group['id'] or abs(float(frame.group(2)) - group['start']) > EPS or
                    abs(float(frame.group(3)) - group['end']) > EPS):
                errors.append(f'{label}: final frame name/time differs from plan')
            body = block[frame.end():frames[fi+1].start() if fi+1 < len(frames) else len(block)]
            # The optional external editing map and next episode title are not frame fields.
            body = re.split(r'^(?:剪辑顺序：|第\s*\S+?\s*集｜)', body, maxsplit=1, flags=re.M)[0]
            ordinary_fields = []
            order = []
            for line in body.splitlines():
                match = VOICE.match(line.strip())
                if match:
                    order.append('voice')
                    if not narrative_mode(plan) and match.group('speaker') != match.group('reference'):
                        errors.append(f'{label}/{fid}: final speech prefix and reference names differ')
                    quoted = re.search(r'“(.*)”\s*$', match.group('content'))
                    if quoted is None:
                        errors.append(f'{label}/{fid}: malformed final speech line')
                    else:
                        ranges = list(TIME_RANGE.finditer(match.group('content')[:quoted.start()]))
                        interval = (float(ranges[0].group('start')), float(ranges[0].group('end'))) if len(ranges) == 1 else None
                        if interval is None:
                            errors.append(f'{label}/{fid}: final speech needs exactly one time range outside quotation')
                        directions = match.group('content')[:quoted.start()]
                        check_quoted_speech_copies(plan, segment, [directions], f'{label}/{fid}/voice', errors)
                        if interval is not None:
                            scope = 'voice:' + scope_time(interval[0])
                            appearance_scopes[scope] = ' '.join(filter(None, (appearance_scopes.get(scope), directions)))
                        final_kind = parsed_voice_kind(match.group('label'), directions)
                        check_final_voice(plan, final_kind, directions, f'{label}/{fid}', errors)
                        actual_speaker = parse_display_speaker(match.group('speaker'), final_kind, f'{label}/{fid}', errors)
                        if interval is not None:
                            for voice in segment['speech']:
                                if (abs(voice['start'] - interval[0]) <= EPS
                                        and abs(voice['end'] - interval[1]) <= EPS
                                        and voice['speaker'] == actual_speaker
                                        and voice['kind'] == final_kind):
                                    check_voice_execution(segment, voice, directions, f'{label}/{fid}', errors)
                        actual_speech.append((fid, actual_speaker, match.group('reference'),
                                              final_kind, normalized(quoted.group(1)), interval))
                else:
                    rows = field_rows(line)
                    ordinary_fields.extend(rows)
                    order.extend(key for key, _ in rows)
            if not sound_errors:
                check_sound_binding(segment, group, ordinary_fields, errors)
            keys = [key for key, _ in ordinary_fields]
            check_quoted_speech_copies(plan, segment, [v for _, v in ordinary_fields], f'{label}/{fid}', errors)
            for key, value in ordinary_fields:
                if key in ('音效', '转场方式'):
                    appearance_scopes[f'frame:{fid}:{key}'] = value
            allowed = ['运镜', '画面内容', '音效', '转场方式']
            if (keys != [key for key in allowed if key in keys] or
                    '运镜' not in keys or '画面内容' not in keys or
                    any(not value for _, value in ordinary_fields)):
                errors.append(f'{label}/{fid}: final frame fields missing, reordered or unexpected')
            desired_order = ['运镜', '画面内容'] + ['voice'] * order.count('voice')
            desired_order += [key for key in ('音效', '转场方式') if key in keys]
            if order != desired_order:
                errors.append(f'{label}/{fid}: final voice/visual field order differs from template')
            if fi == len(frames)-1 and '转场方式' in keys:
                errors.append(f'{label}: final frame must omit transition field')
            visual_values = [value for key, value in ordinary_fields if key in ('运镜', '画面内容')]
            group_shots = [s for s in segment['shots'] if s.get('frame_id') == group['id']]
            blocks = [visual_intervals(value, group_shots, f'{label}/{fid}', errors) for value in visual_values]
            for si, shot in enumerate(group_shots):
                scoped_values = [parts[si] for parts in blocks if si < len(parts)]
                check_restart_prose(segment, shot, voice_context, scoped_values,
                                    f'{label}/{fid}/{shot["start"]}', errors)
                appearance_scopes['shot:' + scope_time(shot['start'])] = ' '.join(scoped_values)
                final_intervals[(label, shot['start'])] = scoped_values
                check_lighting_change(lighting_reviews.get(label), shot, scoped_values, label, errors)
                validate_relation(shot, scoped_values,
                                  f'{label}/{fid}/{shot["start"]}–{shot["end"]}', errors)
                check_layout_binding(shot, scoped_values,
                                     f'{label}/{fid}/{shot["start"]}–{shot["end"]}', errors)
                content_values = blocks[1][si:si+1] if len(blocks) > 1 else []
                check_action_binding(segment, shot, actions, content_values,
                                     f'{label}/{fid}/{shot["start"]}–{shot["end"]}', errors)
                check_speech_carry_binding(segment, shot, voice_context, content_values,
                                           f'{label}/{fid}/{shot["start"]}–{shot["end"]}', errors)
                check_narration_binding(shot, content_values, f'{label}/{fid}', errors)
                check_coverage_binding(segment, shot, narration_coverage, content_values, errors)
                check_isolation_binding(segment, shot, content_values, f'{label}/{fid}/{shot["start"]}', errors)
                check_shot_mouth(segment, shot, scoped_values, f'{label}/{fid}/{shot["start"]}', errors)
                check_static_binding(shot, content_values,
                                     f'{label}/{fid}/{shot["start"]}–{shot["end"]}', errors)
                camera_values = blocks[0][si:si+1] if blocks else []
                check_closeup_binding(shot, camera_values, content_values,
                                      f'{label}/{fid}/{shot["start"]}', errors)
                check_segment_claims(segment, camera_values, f'{label}/{fid}', errors)
                if si:
                    check_internal_cut(shot, camera_values, f'{label}/{fid}/{shot["start"]}', errors)
                check_reception_binding(segment, shot, reactions, camera_values, content_values, errors)
                check_jump_binding(shot, camera_values,
                                   f'{label}/{fid}/{shot["start"]}–{shot["end"]}', errors)
                check_explicit_contradictions(segment, shot, ' '.join(camera_values), ' '.join(content_values), errors)
                check_view_binding(shot, camera_values,
                                   f'{label}/{fid}/{shot["start"]}–{shot["end"]}', errors)
            for voice in segment['speech']:
                if group['start'] - EPS <= voice['start'] < group['end'] - EPS:
                    expected_speech.append((group['id'], voice['speaker'], voice_reference(voice), voice['kind'], normalized(voice['text']),
                                            (voice['start'], voice['end'])))
        if actual_speech != expected_speech:
            errors.append(f'{label}: final speech text, speaker, kind, order, frame or time range differs from plan')
        check_appearance(segment, appearance_scopes, errors)
    check_boundary_binding(plan, final_intervals, errors)
    if require_review and isinstance(review, dict):
        from timing_overlap_evidence import check_timing_overlap_evidence
        check_timing_overlap_evidence(plan, final_intervals, errors)
        from review_closure import check_review_closure
        check_review_closure(plan, final_intervals, errors, text=text, segment_texts=final_segments)
    if 'playback' in plan:
        expected_maps, actual_maps = {}, {}
        by_id = {s['id']: s for s in plan['segments']}
        for clip in plan['playback']:
            segment = by_id.get(clip['segment_id'])
            if segment:
                expected_maps.setdefault(segment['episode'], []).append(
                    (segment_numbers[segment['id']], float(clip['start']), float(clip['end'])))
        for mapping in re.finditer(r'^剪辑顺序：(.*)$', text, re.M):
            preceding = [i for i, h in enumerate(headers) if h.start() < mapping.start()]
            if not preceding:
                errors.append('final prompt: editing map must follow its episode prompts')
                continue
            eid = plan['segments'][preceding[-1]]['episode']
            ranges = re.findall(r'第\s*(\d+(?:-\d+)?)\s*段\s*([0-9.]+)\s*[–—-]\s*([0-9.]+)\s*秒', mapping.group(1).split('；')[0])
            actual_maps.setdefault(eid, []).extend((n, float(start), float(end)) for n, start, end in ranges)
        if actual_maps != expected_maps:
            errors.append('final prompt: external editing map differs from playback records')
    return errors
