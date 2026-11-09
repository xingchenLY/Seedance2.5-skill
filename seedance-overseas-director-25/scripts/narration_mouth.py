"""Separate narrative audio from visible speech without freezing natural faces.

Derived cues follow real voice intervals, not frame labels. Text checks catch
explicit contradictions only; they do not inspect or certify generated lips.
"""
import re

from speech_continuity import time_text

EPS = 1e-7
NONSCENE = {'narration', 'inner'}
PREFIX = {'narration': '画外叙事', 'inner': '内心声'}
ANCHOR = {'narration': '独立画外叙述声轨，仅观众听见',
          'inner': '独立心理声轨，角色没有实际出声'}
TIMED = re.compile(r'(?:本段)?第\s*(\d+(?:\.\d+)?)\s*秒至第\s*(\d+(?:\.\d+)?)\s*秒')
SPEAKING = ('开口说话', '开口讲话', '开口说出', '开口念出', '开口朗读', '开口质问',
            '开口回应', '开口解释', '开口回答', '开口接话', '口型同步', '同步口型',
            '对口型', '逐字口型', '说话', '讲话', '朗读', '跟读', '念出旁白', '说出旁白')


def voice_display_speaker(voice):
    prefix = PREFIX.get(voice['kind'])
    return f'{prefix}｜{voice["speaker"]}' if prefix else voice['speaker']


def parse_display_speaker(value, kind, label, errors):
    prefix = PREFIX.get(kind)
    if prefix is None:
        return value
    marker = prefix + '｜'
    if not value.startswith(marker) or not value[len(marker):].strip():
        errors.append(f'{label}: non-scene voice needs the explicit {marker} identity prefix')
        return value
    return value[len(marker):]


def active(voice, start, end):
    return min(end, voice['end']) > max(start, voice['start']) + EPS


def isolation_spans(segment, shot):
    voices = [v for v in segment['speech'] if active(v, shot['start'], shot['end'])]
    points = sorted({shot['start'], shot['end']} | {
        max(shot['start'], min(shot['end'], v[key])) for v in voices for key in ('start', 'end')})
    spans = []
    for start, end in zip(points, points[1:]):
        current = [v for v in voices if active(v, start, end)]
        kinds = tuple(sorted({v['kind'] for v in current if v['kind'] in NONSCENE}))
        if not kinds:
            continue
        talkers = tuple(sorted({v['speaker'] for v in current if v['kind'] == 'dialogue'
                                and v['speaker'] in shot.get('visible_characters', [])}))
        if spans and abs(spans[-1][1] - start) <= EPS and spans[-1][2:] == (kinds, talkers):
            spans[-1] = (spans[-1][0], end, kinds, talkers)
        else:
            spans.append((start, end, kinds, talkers))
    return spans


def isolation_prose(segment, shot):
    cues = []
    for start, end, kinds, talkers in isolation_spans(segment, shot):
        kind_text = '与'.join('叙事旁白' if k == 'narration' else '内心声' for k in kinds)
        cue = f'本段第{time_text(start)}秒至第{time_text(end)}秒，{kind_text}独立于场内人物发声。'
        silent = [name for name in shot.get('visible_characters', []) if name not in talkers]
        if talkers:
            cue += ('画内仅' + '、'.join(talkers) + '按本人同期台词讲话，嘴部可见时口型只对应本人台词。')
        if silent:
            cue += ('画内' + '、'.join(silent) + '保持无声表演，以原有动作和表情推进；'
                    '嘴部可见时保持自然非讲话状态，不形成连续说话口型。')
        elif not shot.get('visible_characters'):
            cue += '本镜没有可见人物，不承载旁白口型。'
        cues.append(cue)
    return ''.join(cues)


def positive_speech(text):
    from mode_rules import without_negated_terms
    text = re.sub(r'“[^”]*”|「[^」]*」', '', text)
    text = re.sub(r'(?:不要|不得|不应|禁止|避免|没有|不|未)(?:再|持续|进行|形成|做出|出现|呈现)*'
                  r'(?:连续)?(?:说话|讲话|朗读)(?:动作|表演|口型)?', '', text)
    # Explicit prohibition of driving lips with narration is valid, not a conflict.
    text = re.sub(r'(?:不要|不得|禁止|避免|不)(?:随|跟随|按|按照)[^。；;，,]{0,30}'
                  r'(?:旁白|内心声)[^。；;，,]{0,20}(?:口型|嘴唇|唇部)', '', text)
    text = re.sub(r'(?:嘴唇|唇部|口型)(?:不要|不得|禁止|避免|不)(?:随|跟随|按|按照)'
                  r'[^。；;，,]{0,30}(?:旁白|内心声)[^。；;，,]*', '', text)
    return without_negated_terms(text, SPEAKING)


def narrative_lips(text):
    return bool(re.search(r'(?:随|跟随|按|按照)[^。；;，,]{0,20}(?:旁白|内心声)'
                          r'[^。；;，,]{0,20}(?:口型|嘴唇|唇部)|'
                          r'(?:嘴唇|唇部|口型)[^。；;，,]{0,10}(?:随|跟随|按|按照)'
                          r'[^。；;，,]{0,20}(?:旁白|内心声)', text))


def check_voice_directions(kind, text, label, errors, *, final=False):
    if kind not in NONSCENE:
        return
    negated = re.search(r'(?:不是|并非|不属于|不采用|不使用|取消|删除)\s*' + re.escape(ANCHOR[kind]), text)
    if final and (ANCHOR[kind] not in text or negated):
        errors.append(f'{label}: independent non-scene voice anchor is absent from final voice directions')
    clean = positive_speech(text)
    if any(word in clean for word in SPEAKING) or narrative_lips(clean):
        errors.append(f'{label}: narration/inner voice must not direct on-screen speech or lip sync')


def timed_parts(text, shot):
    markers = list(TIMED.finditer(text))
    if not markers:
        yield shot['start'], shot['end'], text
        return
    yield shot['start'], shot['end'], text[:markers[0].start()]
    for index, marker in enumerate(markers):
        end_index = markers[index+1].start() if index+1 < len(markers) else len(text)
        yield float(marker[1]), float(marker[2]), text[marker.end():end_index]


def check_shot_mouth(segment, shot, values, label, errors):
    if not any(v['kind'] in NONSCENE and active(v, shot['start'], shot['end']) for v in segment['speech']):
        return
    visible = shot.get('visible_characters', [])
    for value in values:
        for start, end, text in timed_parts(value, shot):
            if not any(v['kind'] in NONSCENE and active(v, start, end) for v in segment['speech']):
                continue
            clean = positive_speech(text)
            if narrative_lips(clean):
                errors.append(f'{label}: pictured lips are explicitly driven by narrative audio')
            talkers = {v['speaker'] for v in segment['speech'] if v['kind'] == 'dialogue' and active(v, start, end)}
            if not talkers and re.search(r'(?:画内人物|所有人物|全体人物)[^。；;，,]{0,12}'
                                         r'(?:开口讲话|开口说话|口型同步|跟读)', clean):
                errors.append(f'{label}: pictured people have speaking directions during non-scene audio only')
            for name in visible:
                if name in talkers:
                    continue
                others = [re.escape(n) for n in visible if n != name]
                stop = '|'.join(others + [r'[。；;\n]'])
                for match in re.finditer(re.escape(name) + r'(?:(?!' + stop + r').){0,80}', clean):
                    if any(word in match[0] for word in SPEAKING):
                        errors.append(f'{label}: {name} has on-screen speaking directions without dialogue in this interval')
                        break


def check_isolation_binding(segment, shot, values, label, errors):
    prose = isolation_prose(segment, shot)
    normalize = lambda text: ' '.join(text.split())
    if prose and not any(normalize(prose) in normalize(value) for value in values):
        errors.append(f'{label}: narrative mouth isolation is absent from this final visual interval')
