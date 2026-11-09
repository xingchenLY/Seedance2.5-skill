"""Narrow final-prose guards against duplicate utterances and explicit restarts.

This does not prove semantic roles, source coverage, word timing or actual audio.
Quoted screen text and one-word stress cues are distinguished by local context;
their source authority and visual readability still require content review.
"""
import re

EPS = 1e-7
CROSS_SHOT_CUE = '本条原声在所列完整区间只执行一次，跨镜只续未完成部分'
QUOTES = re.compile(r'“([^”\n]+)”|「([^」\n]+)」|"([^"\n]+)"')
SENTENCE_END = '.!?。！？'
NEGATION = re.compile(r'(?:不应|不得|不要|禁止|避免|不能|无需|不允许|没有|未|不)[^，,。；;\n]{0,18}$')
VISUAL_ROLE = re.compile(
    r'(?:屏幕|短信|消息|评论|信件|文件|纸条|标牌|招牌|门牌|按钮|铭牌|界面|页面|标签|标识|牌匾|文字)'
    r'[^，,。；;\n「」“”"]{0,24}(?:显示|写着|写有|印着|印有|标着|标有|刻着|刻有|内容为|文字为|字样为|呈现|可读|可辨)[：:\s]*$')
VISUAL_SUFFIX = re.compile(r'^\s*(?:按钮|标签|字样|文字|标牌)[^，,。；;\n]{0,12}(?:可读|可辨|可见)')
RESTART = re.compile(r'重新起句|从头(?:重新)?(?:读|念|说)|重(?:读|念)整句|再说一遍|'
                     r'重复(?:本句|原句|这句话|前句)|再次说出原句|重新开口说出原句')
AMBIGUOUS_START = re.compile(r'(?<!继续)(?<!接着)开口(?:说出原句|读出整句)')


def normalized(value):
    return ' '.join(value.split())


def crosses_shots(segment, voice):
    return sum(voice['start'] < s['end'] - EPS and voice['end'] > s['start'] + EPS
               for s in segment.get('shots', [])) > 1


def complete_source_quote(quote, source):
    """Match an entire source or a bounded complete sentence, not a stress phrase."""
    quote, source = normalized(quote), normalized(source)
    if not quote:
        return False
    if quote == source:
        return True
    if quote[-1] not in SENTENCE_END:
        return False
    for match in re.finditer(re.escape(quote), source):
        before = source[:match.start()].rstrip()
        after = source[match.end():]
        if ((not before or before[-1] in SENTENCE_END)
                and (not after or after[0].isspace() or after[0] in '”」\'"'
                     or quote[-1] in '。！？')):
            return True
    return False


def check_quoted_speech_copies(plan, segment, values, label, errors):
    """Inspect non-voice prose only; real repeated source events remain untouched."""
    used = {v['source_id'] for v in segment.get('speech', [])}
    sources = [s['text'] for s in plan.get('sources', []) if s['id'] in used]
    for value in values:
        previous_end = 0
        for match in QUOTES.finditer(value):
            quote = next(g for g in match.groups() if g is not None)
            lead = re.split(r'[，,。；;\n]', value[previous_end:match.start()])[-1]
            previous_end = match.end()
            if not any(complete_source_quote(quote, source) for source in sources):
                continue
            if VISUAL_ROLE.search(lead) or VISUAL_SUFFIX.search(value[match.end():]):
                continue  # The displayed text must still be justified by the source.
            words = re.findall(r'[^\W_]+(?:[\'’][^\W_]+)*', quote, re.UNICODE)
            if len(words) == 1 and re.search(r'(?:重读|重音(?:落在|放在)|强调)[：:\s]*$', lead):
                continue
            errors.append(f'{label}: complete source speech is copied into non-voice prose; '
                          'keep trigger words internal or identify necessary displayed text')
            break


def check_voice_execution(segment, voice, directions, label, errors):
    if crosses_shots(segment, voice) and CROSS_SHOT_CUE not in directions:
        errors.append(f'{label}: cross-shot speech needs its single-execution direction')


def check_restart_prose(segment, shot, context, values, label, errors):
    """Catch explicit same-voice restarts; do not guess every opening-mouth action."""
    continuing = []
    for index, voice in enumerate(segment.get('speech', [])):
        if voice['end'] <= shot['start'] + EPS or voice['start'] >= shot['end'] - EPS:
            continue
        item = context.get((segment['id'], index), {})
        if voice['start'] < shot['start'] - EPS or item.get('previous'):
            continuing.append(voice)
    if not continuing:
        return
    voices = segment.get('speech', [])
    for value in values:
        # Dialogue quoted by the source or text on a prop is not an instruction.
        prose = QUOTES.sub('', value)
        for clause in re.split(r'[，,。；;\n]', prose):
            for voice in continuing:
                speaker = voice['speaker']
                if speaker not in clause:
                    # Only unambiguous references to the continuing audio itself.
                    if not re.search(r'原声|原台词|同一句|该句|这句', clause):
                        continue
                    if any(v['speaker'] != speaker and v['speaker'] in clause for v in voices):
                        continue
                has_new_turn = any(v['speaker'] == speaker
                                   and shot['start'] - EPS <= v['start'] < shot['end'] - EPS
                                   and v is not voice for v in voices)
                if has_new_turn:
                    continue  # Per-event scope must be resolved in content review.
                for pattern in (RESTART, AMBIGUOUS_START):
                    for match in pattern.finditer(clause):
                        if NEGATION.search(clause[:match.start()]):
                            continue
                        errors.append(f'{label}: continuing speech has a restart instruction for {speaker}')
                        break
                    else:
                        continue
                    break

