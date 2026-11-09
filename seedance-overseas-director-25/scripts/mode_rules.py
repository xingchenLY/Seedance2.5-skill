"""Four-mode routing and narrative additions to the shared live-action engine.

The public CLI requires mode. Internal functions still accept legacy regression
fixtures without mode; that compatibility is not a production mode default.
"""
from pathlib import Path
import math
import re
from language_policy import language_clauses, check_language_sources, check_language_presentation
from name_policy import check_source_name_adaptations

EPS = 1e-7
MODES = {
    '3D漫推文': {'animation': True, 'narrative': True},
    '3D漫真人剧': {'animation': True, 'narrative': False},
    '真人剧': {'animation': False, 'narrative': False},
    '推文': {'animation': False, 'narrative': True},
}


def require_mode(plan):
    value = plan.get('mode')
    if not isinstance(value, str) or value not in MODES:
        raise ValueError('mode is required: explicitly select 3D漫推文 / 3D漫真人剧 / 真人剧 / 推文')
    return MODES[value]


def narrative_mode(plan):
    return isinstance(plan.get('mode'), str) and MODES.get(plan['mode'], {}).get('narrative', False)


def presentation_defaults(plan):
    if 'mode' not in plan:
        return {}
    mode = require_mode(plan)
    form = '风格化3D电影动画' if mode['animation'] else '真人写实'
    sound = '人物对白、原文叙事旁白、内心声及必要非配乐音效' if mode['narrative'] else '人物台词、原有VO／OS及必要非配乐音效'
    result = {
        '画面规格': f'AI {form}竖屏短剧，9:16构图，8K分辨率。',
        '声音限制': f'全程无背景音乐；仅使用{sound}。',
    }
    clauses = language_clauses(plan)
    if clauses:
        result['声音限制'] += clauses['声音限制'] + '原文或用户明确要求的外语发声按已登记依据保留。'
        result['画面限制'] = ('全画面可见文字，包括背景、屏幕及道具文字，均遵循本段目标语种；'
                            + clauses['画面限制']
                            + '有原文或用户依据的外语文字、专名及代码按名称替换后的既定版本保留；'
                            '不出现品牌名称、标识或真实地名；'
                            '不添加非剧情所需的字幕、装饰文字、水印、LOGO、悬浮 UI 或片头片尾文字。')
    return result


def voice_reference(voice):
    return voice.get('voice_reference', voice['speaker'])


def voice_category(kind):
    from narration_mouth import ANCHOR
    label = {'narration': '叙事旁白', 'inner': '内心声'}.get(kind)
    return f'{label}，{ANCHOR[kind]}，' if label else ''


def parsed_voice_kind(label, directions):
    if label != '旁白参考':
        return {'台词参考': 'dialogue', 'VO 参考': 'VO', 'OS 参考': 'OS'}[label]
    categories = without_negated_terms(directions, ('叙事旁白', '内心声'))
    narration, inner = '叙事旁白' in categories, '内心声' in categories
    if narration == inner:
        return 'invalid-narrative-kind'
    return 'narration' if narration else 'inner'


def allowed_voice_kinds(plan):
    return ({'dialogue', 'narration', 'inner'} if require_mode(plan)['narrative']
            else {'dialogue', 'VO', 'OS'})


def without_negated_terms(text, terms):
    """Remove explicit prohibitions and their lists, not arbitrary negative clauses.

    This catches concrete instructions such as 不带愤怒或哽咽. It is deliberately
    not an NLP claim about every natural-language expression of intent.
    """
    words = '(?:' + '|'.join(re.escape(t) for t in sorted(terms, key=len, reverse=True)) + ')'
    negative = r'(?:不要|不得|不能|不应|不再|无需|没有|禁止|避免|不|无|非)'
    modifier = r'(?:(?:出现|使用|采用|加入|带有|包含|含有|表现|呈现|带入|添加|带|含|加|用|保持)|(?:任何|明显的?|强烈的?|人物的?|角色的?))*'
    listing = words + r'(?:\s*(?:、|或|和|及|与|/|／)\s*' + words + r')*'
    cleaned = re.sub(negative + r'\s*' + modifier + r'\s*' + listing, '', text)
    # A frequent instruction refers to the pictured character's emotion while
    # explicitly keeping the narrator independent of it.
    cleaned = re.sub(r'不随(?:画面)?(?:人物|角色)(?:的)?' + listing + r'(?:而)?(?:起伏|变化|波动)', '', cleaned)
    return cleaned


def check_narration_tone(performance, label, errors):
    neutral_terms = ('中性', '平稳')
    emotion_terms = ('哭腔', '怒气', '颤声', '惊讶', '愤怒', '哽咽', '情绪递进', '情绪起伏')
    positive_neutral = without_negated_terms(performance, neutral_terms)
    if not any(word in positive_neutral for word in neutral_terms):
        errors.append(f'{label}: narration performance must explicitly remain neutral/steady')
    positive_emotion = without_negated_terms(performance, emotion_terms)
    if any(word in positive_emotion for word in emotion_terms):
        errors.append(f'{label}: narration performance contains character emotion')


def selected_style(plan):
    from validate_styles import load_catalog, resolve_style
    choice = plan.get('style_id')
    if not isinstance(choice, str) or not choice.strip():
        raise ValueError('style_id: record the chosen/default 3D style after explicit mode selection')
    catalog, index = load_catalog(Path(__file__).resolve().parents[1] / 'styles' / 'catalog.json')
    return resolve_style(catalog, index, choice)


def check_visual_mode(plan, presentation, label, errors):
    """Check only production fields; source dialogue mentioning 3D is untouched."""
    mode = require_mode(plan)
    spec = re.sub(r'\s+', '', presentation.get('画面规格', ''))
    style_text = re.sub(r'\s+', '', presentation.get('整体风格', ''))
    terms = ('风格化3D电影动画', '3D电影动画', '3D动画', '三维动画', '真人实拍', '真人写实')
    active_spec = without_negated_terms(spec, terms)
    active_style = without_negated_terms(style_text, terms)
    animation = re.compile(r'(?:3D|三维)(?:风格化|电影)?(?:动画|动漫|卡通)', re.I)
    live = re.compile(r'真人(?:写实|实拍)')
    if mode['animation']:
        if live.search(active_spec + active_style):
            errors.append(f'{label}: 3D mode contains incompatible live-action presentation')
        if not animation.search(active_spec):
            errors.append(f'{label}: selected 3D mode requires an explicit 3D animation specification')
        try:
            style = selected_style(plan)
        except ValueError as exc:
            errors.append(str(exc))
            return
        match = re.search(r'风格标签[：:]([^；;。\n]+)', presentation.get('整体风格', ''))
        if match is None or match.group(1).strip() != style['label']:
            errors.append(f'{label}: 3D presentation is missing the selected style label {style["label"]}')
    else:
        if animation.search(active_spec + active_style) or '风格标签' in style_text:
            errors.append(f'{label}: live-action mode contains incompatible animation presentation')
        if not live.search(active_spec):
            errors.append(f'{label}: selected live-action mode requires an explicit live-action specification')


def overlap_allowed(plan, segment, voices, frame_id=None):
    """A scoped source-backed exception must cover every participating voice."""
    if not narrative_mode(plan) or not voices:
        return False
    records = segment.get('frame_voice_exceptions', {})
    if not isinstance(records, dict):
        return False
    candidates = [records.get(frame_id)] if frame_id else records.values()
    ids = {v['source_id'] for v in voices}
    for entry in candidates:
        if not isinstance(entry, dict) or not isinstance(entry.get('basis'), str) or not entry['basis'].strip():
            continue
        source_ids = entry.get('source_ids')
        if not isinstance(source_ids, list) or any(not isinstance(s, str) for s in source_ids) or len(source_ids) != len(set(source_ids)) or set(source_ids) != ids:
            continue
        lips = entry.get('lip_sync_speakers')
        if not isinstance(lips, list) or any(not isinstance(s, str) for s in lips) or set(lips) != {v['speaker'] for v in voices if v['kind'] == 'dialogue'}:
            continue
        if len({v['kind'] for v in voices}) < 2:
            continue
        if all(any(v['kind'] != other['kind'] and min(v['end'], other['end']) > max(v['start'], other['start']) + EPS
                   for other in voices) for v in voices):
            return True
    return False


def narration_seconds(segment, shot):
    ranges = sorted((max(shot['start'], v['start']), min(shot['end'], v['end']))
                    for v in segment['speech'] if v['kind'] == 'narration'
                    and min(shot['end'], v['end']) > max(shot['start'], v['start']) + EPS)
    total, right = 0, -math.inf
    for start, end in ranges:
        total += max(0, end - max(start, right))
        right = max(right, end)
    return total


def check_narrative_visual(shot, required, errors, label):
    entry = shot.get('narration_visual')
    if entry is None and not required:
        return
    if not isinstance(entry, dict) or entry.get('mode') != 'progression':
        errors.append(f'{label}: long narration needs narration_visual progression; a static hold cannot cover it')
        return
    if not isinstance(entry.get('basis'), str) or not entry['basis'].strip():
        errors.append(f'{label}: narration visual review needs a source-supported basis')
    beats = entry.get('beats')
    if not isinstance(beats, list) or len(beats) < 2:
        errors.append(f'{label}: narration visual progression needs at least two timed beats')
        return
    previous, phrases = shot['start'], []
    for beat in beats:
        if not isinstance(beat, dict):
            errors.append(f'{label}: narration beat must be an object')
            continue
        start, end = beat.get('start'), beat.get('end')
        if any(type(t) not in (int, float) or not math.isfinite(t) for t in (start, end)):
            errors.append(f'{label}: narration beat needs finite times')
            continue
        if abs(start - previous) > EPS or end <= start or end > shot['end'] + EPS:
            errors.append(f'{label}: narration beats must continuously cover the shot in order')
        previous = end
        phrase = beat.get('visual_evidence')
        if not isinstance(phrase, str) or not phrase.strip():
            errors.append(f'{label}: narration beat needs concrete visual evidence')
        else:
            phrases.append(phrase.strip())
    if abs(previous - shot['end']) > EPS or len(phrases) != len(set(phrases)):
        errors.append(f'{label}: narration visual beats miss the end or repeat evidence')
    from narration_editing import check_long_take
    check_long_take(shot, required, label, errors)


def narration_visual_prose(shot):
    entry = shot.get('narration_visual')
    if not isinstance(entry, dict):
        return ''
    return ''.join(f'第{b["start"]:g}秒至第{b["end"]:g}秒，{b["visual_evidence"]}。'
                   for b in entry.get('beats', []))


def check_narration_binding(shot, values, label, errors):
    prose = narration_visual_prose(shot)
    if prose and not any(prose in value for value in values):
        errors.append(f'{label}: narration visual progression missing from final timed picture')


def check_mode(plan, errors):
    check_source_name_adaptations(plan, errors)
    check_language_sources(plan, errors)
    if 'mode' not in plan:
        # Existing private regression helpers cover shared mechanics without routing.
        if any(s.get('kind') in ('narration', 'inner') for s in plan.get('sources', [])):
            errors.append('mode: narrative sources require an explicitly selected narrative mode')
        return
    mode = require_mode(plan)
    allowed = allowed_voice_kinds(plan)
    sources = {s['id']: s for s in plan['sources']}
    for source in sources.values():
        if source['kind'] not in allowed:
            errors.append(f'{source["id"]}: voice kind is incompatible with selected mode')
    if mode['narrative']:
        from narration_coverage import check_narration_coverage, long_narrated_shot
        check_narration_coverage(plan, errors)
        from narration_inventory import validate_narration
        validate_narration(plan, errors, required=True)
        from narration_flow import check_narration_flow
        check_narration_flow(plan, errors)
    elif plan.get('narration_sources'):
        errors.append('mode: drama cannot silently omit an existing independent narration inventory')

    for segment in plan['segments']:
        label = segment['id']
        presentation = {**presentation_defaults(plan), **segment.get('presentation', {})}
        check_language_presentation(plan, presentation, label, errors)
        check_visual_mode(plan, presentation, label, errors)
        if mode['narrative']:
            from narration_editing import check_segment_claims
            check_segment_claims(segment, list(presentation.values()) +
                                 [s.get('camera_text', '') for s in segment['shots']], label, errors)

        for voice in segment['speech']:
            source = sources.get(voice['source_id'], {})
            reference = voice_reference(voice)
            expected = source.get('voice_reference', voice['speaker'])
            if reference != expected:
                errors.append(f'{label}: voice reference differs from source record')
            if reference != voice['speaker'] and (not mode['narrative'] or not source.get('voice_reference_basis')):
                errors.append(f'{label}: borrowed voice needs narrative mode and an explicit source basis')
            if mode['narrative'] and voice['kind'] == 'narration':
                check_narration_tone(voice.get('performance', ''), label, errors)
            if mode['narrative']:
                from narration_mouth import check_voice_directions
                check_voice_directions(voice['kind'], voice.get('performance', ''), label, errors)
        exceptions = segment.get('frame_voice_exceptions', {})
        if not isinstance(exceptions, dict):
            errors.append(f'{label}: frame_voice_exceptions must be an object')
        else:
            from delivery_checks import frame_groups
            groups = {g['id']: g for g in frame_groups(segment)}
            for fid in exceptions:
                group = groups.get(fid)
                voices = [] if group is None else [v for v in segment['speech'] if min(v['end'], group['end']) > max(v['start'], group['start']) + EPS]
                if not overlap_allowed(plan, segment, voices, fid):
                    errors.append(f'{label}/{fid}: invalid or ungrounded cross-category overlap exception')
        for shot in segment['shots']:
            if mode['narrative']:
                from narration_mouth import check_shot_mouth
                check_shot_mouth(segment, shot, [shot.get('camera_text', ''), shot.get('visual_text', '')],
                                 f'{label}/{shot["start"]}', errors)
                short_basis = plan.get('narrative_short_shot_basis')
                short_authorized = isinstance(short_basis, str) and bool(short_basis.strip())
                if shot['end'] - shot['start'] < 1 - EPS and not short_authorized:
                    errors.append(f'{label}: narrative mode requires actual shots >= 1 second unless user explicitly changes it')
                check_narrative_visual(shot, long_narrated_shot(segment, shot), errors, label)
