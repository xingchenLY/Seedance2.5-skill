"""Check split-source timing and bind continuing audio to its actual picture interval.

Timing and text binding are deterministic. Pause reasons and whether a listener
has heard enough information still need screenplay-based content review.
"""
import math

EPS = 1e-7
PAUSE_REASONS = {'source_pause', 'natural_pause', 'story_break'}


def speech_context(plan, errors=None):
    """Map voice records to playback time, including intervening clips and silence."""
    segments = {s['id']: s for s in plan['segments']}
    clips = plan.get('playback', [dict(segment_id=s['id'], start=0, end=s['duration'])
                                  for s in plan['segments']])
    context, previous = {}, {}
    cursor = 0
    for clip in clips:
        segment = segments.get(clip['segment_id'])
        start, end = clip['start'], clip['end']
        if segment is None or not 0 <= start < end <= segment['duration'] + EPS:
            continue  # The plan validator reports invalid clips.
        for index, voice in enumerate(segment['speech']):
            if voice['start'] < start - EPS or voice['end'] > end + EPS:
                continue  # A speech record may not straddle an editing-map cut.
            key = (segment['id'], index)
            prior = previous.get(voice['source_id'])
            item = dict(voice=voice, start=cursor + voice['start'] - start,
                        end=cursor + voice['end'] - start, previous=prior)
            item['gap'] = item['start'] - prior['end'] if prior else None
            context[key] = item
            previous[voice['source_id']] = item
            if errors is not None:
                check_pause(item, f'{segment["id"]}/{voice["source_id"]}', errors)
        cursor += end - start
    return context


def check_pause(item, label, errors):
    voice, gap = item['voice'], item['gap']
    has_pause = 'pause_before' in voice
    if gap is not None and gap < -EPS:
        errors.append(f'{label}: fragments of the same source overlap in playback')
    if gap is None or gap <= EPS:
        if has_pause:
            errors.append(f'{label}: pause_before requires a preceding fragment and a real playback gap')
        return
    pause = voice.get('pause_before')
    if not isinstance(pause, dict):
        errors.append(f'{label}: unexplained speech gap of {gap:.3f}s; reaction cuts do not pause speech')
        return
    seconds, basis = pause.get('seconds'), pause.get('basis')
    if (not isinstance(pause.get('reason'), str) or pause['reason'] not in PAUSE_REASONS
            or not isinstance(basis, str)
            or not basis.strip() or isinstance(seconds, bool)
            or not isinstance(seconds, (int, float)) or not math.isfinite(seconds)
            or abs(seconds - gap) > EPS):
        errors.append(f'{label}: pause_before needs a source/natural/story reason, concrete basis '
                      'and seconds matching the actual playback gap')


def time_text(value):
    return f'{value:.7f}'.rstrip('0').rstrip('.')


def speech_carry_prose(segment, shot, context, *, style='compact'):
    """Emit no quoted words or extra voice rows; retain the original voice kind."""
    cues = []
    for index, voice in enumerate(segment['speech']):
        start, end = max(shot['start'], voice['start']), min(shot['end'], voice['end'])
        if end <= start + EPS:
            continue
        item = context.get((segment['id'], index), {})
        prior, gap = item.get('previous'), item.get('gap')
        crossing = voice['start'] < shot['start'] - EPS
        offscreen = voice['speaker'] not in shot.get('visible_characters', [])
        subject = shot.get('view', {}).get('subject', shot.get('start_subject'))
        reaction = subject is not None and subject != voice['speaker']
        if not (crossing or prior or offscreen or reaction):
            continue
        label = {'dialogue': '原台词声', 'VO': '原 VO 声音', 'OS': '原 OS 声音', 'narration': '原叙事旁白', 'inner': '原内心声'}[voice['kind']]
        placement = '在画外' if offscreen and voice['kind'] == 'dialogue' else ''
        if crossing or (prior and gap is not None and abs(gap) <= EPS):
            action = '接续同一句后文'
        elif prior:
            action = '在停顿后接续原句后文'
        else:
            action = '按原区间起声'
        # Voice identity stays consistent; carry the authored performance arc,
        # rather than suggesting that delivery must remain flat across a cut.
        if style == 'compact':
            delivery = '承接该句已写明的声音表演及其变化，原声连续，不因切镜重新起句或重念已完成部分。'
        elif style == 'compact-v1':
            delivery = '沿用原音色与语气，声音连续。'
        else:
            delivery = '承接该句已写明的音色、音量、语速与气息变化，不因切镜停声或重念。'
        cue = (f'本段第{time_text(start)}秒至第{time_text(end)}秒，{voice["speaker"]}的{label}'
               f'{placement}{action}，' + delivery)
        if voice['kind'] in ('VO', 'OS', 'narration', 'inner'):
            cue += '此声不配人物口型。'
        elif reaction:
            cue += ('画面主体不替原说话者对口型。' if style in ('compact', 'compact-v1') else
                    '反应画面不截断此声轨，画面主体不替原说话者对口型。')
        cues.append(cue)
    return ''.join(cues)


def check_speech_carry_binding(segment, shot, context, visual_values, label, errors):
    prose = speech_carry_prose(segment, shot, context)
    normalize = lambda value: ' '.join(value.split())
    alternatives = (prose, speech_carry_prose(segment, shot, context, style='compact-v1'),
                    speech_carry_prose(segment, shot, context, style='legacy'))
    if prose and not any(normalize(candidate) in normalize(value)
                         for candidate in alternatives for value in visual_values):
        errors.append(f'{label}: speech carry is absent from this final visual interval')
