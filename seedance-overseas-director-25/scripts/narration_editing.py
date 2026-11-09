"""Audit deliberate long takes and expose actual edits beneath continuous audio.

Small text filters reject known empty progression claims. They do not establish
story truth, photographic merit, or successful cuts in generated video.
"""
import re

from camera_views import canonical_text

LONG_REASONS = {'continuous_action', 'gradual_reveal', 'relationship_transition',
                'performance_arc', 'user_requested'}
CHANGE_TYPES = {'action_progress', 'reveal', 'relationship_change', 'performance_turn'}
DEFAULT_CUT = '内部硬切至本镜。'
TRANSITIONS = re.compile(r'硬切|跳切|切至|切到|切入|切回|切换到|叠化|叠切|淡入|淡出|划像|匹配剪辑|甩镜转场')


def nonempty(value):
    return isinstance(value, str) and bool(value.strip()) and '\n' not in value and '\r' not in value


def positive_transitions(text):
    term = r'(?:内部)?(?:' + TRANSITIONS.pattern + ')'
    listing = term + r'(?:\s*(?:、|或|和|及|与|/|／)\s*' + term + ')*'
    return re.sub(r'(?:不是|不要|不得|禁止|避免|无需|不能|不)(?:使用|采用|做)?\s*' + listing, '', text)


def internal_cut_prose(shot):
    """Expose subsequent actual shots inside one template frame in all four modes."""
    if 'jump_cut_before' in shot or TRANSITIONS.search(positive_transitions(shot.get('camera_text', ''))):
        return ''  # Preserve an authored technique; jump-cut prose comes from its own record.
    if re.search(r'(?:不要|不得|禁止|避免|不能|不)(?:使用|采用)?(?:内部)?硬切|与前镜连续|接前镜连续',
                 shot.get('camera_text', '')):
        return ''  # Require the author to resolve the conflicting shot boundary.
    return DEFAULT_CUT


def check_internal_cut(shot, camera_values, label, errors):
    expected = internal_cut_prose(shot)
    if expected:
        if not any(TRANSITIONS.search(positive_transitions(value)) for value in camera_values):
            errors.append(f'{label}: explicit internal cut is missing from this final camera interval')
    else:
        authored = set(TRANSITIONS.findall(positive_transitions(shot.get('camera_text', ''))))
        if 'jump_cut_before' in shot:
            authored.add('跳切')
        if not any(authored & set(TRANSITIONS.findall(positive_transitions(value))) for value in camera_values):
            errors.append(f'{label}: authored internal transition is missing from this final camera interval')


def cosmetic_only(value, visible):
    """Recognize a bounded set of expression/idle-motion-only filler phrases."""
    text = canonical_text(value)
    for name in sorted(visible, key=len, reverse=True):
        text = text.replace(canonical_text(name), '')
    tokens = ('画内人物', '人物', '角色', '摄影机', '镜头', '继续', '仍然', '依然', '保持', '逐渐',
              '更加', '轻微', '微微', '慢慢', '缓慢', '缓缓', '持续', '抬眼', '眨眼', '皱眉', '抿唇',
              '看向', '看着', '望向', '望着', '注视', '眉心', '眉间', '表情', '神情', '眼神', '目光',
              '收紧', '紧绷', '不变', '呼吸', '慢推', '推进', '耳朵', '尾巴', '毛发', '轻摆', '晃动',
              '起伏', '飘动', '微表情', '细微变化', '的', '地', '着', '仍', '并', '又', '再', '向')
    for token in sorted(tokens, key=len, reverse=True):
        text = text.replace(token, '')
    return not text


def information_key(value):
    return re.sub(r'继续|仍然|依然|保持|逐渐|更加|仍', '', canonical_text(value))


def empty_hold_reason(value):
    text = canonical_text(value)
    phrases = ('旁白还没讲完', '旁白没有讲完', '旁白尚未结束', '保持旁白连续', '保证旁白连续',
               '台词还没说完', '台词未说完', '保持台词连续', '保证台词连续', '同一说话人',
               '一分镜一人', '保持完整表演', '完整表演', '持续讲话', '声音连续',
               '避免旁白断句', '避免断句', '保证不对口型', '人物闭口', '同一叙述者', '同一段旁白',
               '保持张力', '动作连贯', '微表情递进', '减少切镜', '未满30秒', '不满30秒',
               '一镜到底', '不切镜', '所以', '为了', '必须', '需要', '本镜', '持续', '因为')
    for phrase in sorted(phrases, key=len, reverse=True):
        text = text.replace(phrase, '')
    return not text


def check_long_take(shot, required, label, errors, *, field='narration_visual', audio='narration'):
    if not required:
        return
    entry = shot.get(field)
    if not isinstance(entry, dict):
        return  # Base progression validator reports the missing record.
    hold = entry.get('long_take')
    valid_hold = isinstance(hold, dict) and {'reason', 'why_uncut', 'finish_evidence', 'cut_review'} <= set(hold)
    if not valid_hold:
        errors.append(f'{label}: {field}.long_take needs reason, why_uncut, finish_evidence and cut_review; '
                      f'continuous {audio} alone does not justify one take')
    else:
        reason = hold['reason']
        if not isinstance(reason, str) or reason not in LONG_REASONS:
            errors.append(f'{label}: long-take reason must identify a continuous story task or explicit user request')
        if not nonempty(hold['why_uncut']) or empty_hold_reason(hold['why_uncut']):
            errors.append(f'{label}: explain the concrete value of not cutting; audio continuity or generic tension is insufficient')
        if not nonempty(hold['finish_evidence']):
            errors.append(f'{label}: long take needs a visible natural endpoint')
        cut = hold['cut_review']
        if not isinstance(cut, dict):
            errors.append(f'{label}: cut_review must examine an actual alternative edit')
        else:
            import math
            point = cut.get('at')
            if (type(point) not in (int, float) or not math.isfinite(point)
                    or not shot['start'] < point < shot['end']):
                errors.append(f'{label}: alternative cut point must be inside the actual long shot')
            for field in ('next_view', 'loss_if_cut'):
                if not nonempty(cut.get(field)) or empty_hold_reason(cut[field]):
                    errors.append(f'{label}: cut_review needs a concrete next_view and loss_if_cut; movement alone is insufficient')
            if reason == 'user_requested' and not nonempty(cut.get('user_basis')):
                errors.append(f'{label}: user-requested long take needs the actual user_basis')
    beats = entry.get('beats')
    if not isinstance(beats, list) or len(beats) < 2 or any(not isinstance(b, dict) for b in beats):
        return
    information, substantive = [], 0
    for beat in beats:
        change, new = beat.get('change'), beat.get('new_information')
        if not isinstance(change, str) or change not in CHANGE_TYPES:
            errors.append(f'{label}: long {audio} beat needs an actual information-change type')
        if not nonempty(new):
            errors.append(f'{label}: long {audio} beat needs concrete new_information')
            continue
        evidence = beat.get('visual_evidence')
        if not isinstance(evidence, str) or canonical_text(new) not in canonical_text(evidence):
            errors.append(f'{label}: new_information must be visible in this beat visual_evidence')
        information.append(information_key(new))
        if cosmetic_only(new, shot.get('visible_characters', [])):
            errors.append(f'{label}: expression/idle-motion-only phrasing is not sufficient new information for a long take')
        else:
            substantive += 1
    if len(information) != len(set(information)):
        errors.append(f'{label}: repeated held information cannot establish long-take progression')
    if substantive < 2:
        errors.append(f'{label}: long take needs at least two distinct substantive stages; reconsider actual shots')
    if valid_hold and nonempty(hold['finish_evidence']):
        last = beats[-1].get('visual_evidence', '')
        if not isinstance(last, str) or canonical_text(hold['finish_evidence']) not in canonical_text(last):
            errors.append(f'{label}: long-take endpoint must be visible in the final beat, not an earlier stage')


def check_segment_claims(segment, values, label, errors):
    if len(segment['shots']) <= 1:
        return
    for text in values:
        clean = re.sub(r'(?:不要求|无需|不要|不得|避免|不必)(?:整段|本段|全段)?'
                       r'(?:一镜到底|无切镜|不切镜)', '', text)
        if re.search(r'(?:整段|本段|全段)(?:均|全程|保持|要求|采用|必须)?(?:一镜到底|无切镜|不切镜)', clean):
            errors.append(f'{label}: whole-segment no-cut instruction contradicts multiple actual shots')


def check_final_narration_editing(plan, errors):
    from mode_rules import narrative_mode, check_narrative_visual
    from narration_coverage import long_narrated_shot, check_narration_coverage
    if not narrative_mode(plan):
        return {}
    for segment in plan['segments']:
        for shot in segment['shots']:
            check_narrative_visual(shot, long_narrated_shot(segment, shot),
                                   errors, f'{segment["id"]}/{shot["start"]}')
    return check_narration_coverage(plan, errors)
