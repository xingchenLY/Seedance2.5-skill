"""Bind dialogue reception decisions to actual playback, visibility and final shots.

Long turns need reception review; actual long shots additionally need cut review.
Story importance, hearing and the merit of a held view remain content review.
"""
import math
import re

from speech_continuity import speech_context, time_text
from narration_editing import nonempty
from dialogue_editing import check_dialogue_editing

EPS = 1e-7
REVIEW_SECONDS = 6


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def norm(value):
    return ''.join(value.split())


def quote_spans(quote, text):
    """Locate exact phrases; repeated occurrences remain ambiguous, not guessed."""
    quote, text = ' '.join(quote.split()), ' '.join(text.split())
    left = r'(?<![A-Za-zÀ-ÖØ-öø-ÿ0-9])' if quote[0].isascii() and quote[0].isalnum() else ''
    right = r'(?![A-Za-zÀ-ÖØ-öø-ÿ0-9])' if quote[-1].isascii() and quote[-1].isalnum() else ''
    pattern = '(?=(' + left + re.escape(quote) + right + '))'
    return [match.span(1) for match in re.finditer(pattern, text)]


def quote_in_speech(quote, text):
    return bool(quote_spans(quote, text))


def positive_evidence(value, expected):
    text, fragment = norm(value), norm(expected)
    for match in re.finditer(re.escape(fragment), text):
        prefix = text[max(0, match.start() - 30):match.start()]
        if not re.search(r'(?:不要|不得|禁止|无需|不必|省略|删除|不呈现|不显示)'
                         r'(?:呈现|显示|出现|拍摄|表现|安排|让|写出|保留)?(?:这段|此处|以下)?[：:]*$', prefix):
            return True
    return False


def dialogue_turns(plan):
    """Keep consecutive dialogue by one person together across source/shot/segment splits."""
    segments = {s['id']: s for s in plan['segments']}
    turns, current = [], []
    for key, item in sorted(speech_context(plan).items(), key=lambda kv: kv[1]['start']):
        identity = (segments[key[0]]['episode'], item['voice']['speaker'])
        if current and (item['voice']['kind'] != 'dialogue' or identity != current[-1][2]):
            turns.append(current)
            current = []
        if item['voice']['kind'] == 'dialogue':
            current.append((key, item, identity))
    if current:
        turns.append(current)
    return turns


def shot_at(segments, record, label, errors):
    segment = segments.get(record.get('segment_id')) if isinstance(record.get('segment_id'), str) else None
    start = record.get('shot_start')
    shots = [s for s in segment['shots'] if finite(start) and abs(s['start'] - start) <= EPS] if segment else []
    if len(shots) != 1:
        errors.append(f'{label}: coverage must reference one actual shot')
        return None
    return shots[0]


def playback_time(plan, segment_id, start, end):
    segments = {s['id']: s for s in plan['segments']}
    clips = plan.get('playback', [dict(segment_id=s['id'], start=0, end=s['duration']) for s in plan['segments']])
    offset, matches = 0, []
    for clip in clips:
        if (clip['segment_id'] == segment_id and start >= clip['start'] - EPS and end <= clip['end'] + EPS):
            matches.append((offset + start - clip['start'], offset + end - clip['start']))
        offset += clip['end'] - clip['start']
    return matches[0] if len(matches) == 1 and segment_id in segments else None


def reaction_prose(node):
    # Keep exact words in the internal trigger, not in a second apparent utterance.
    # Authored evidence supplies the concrete meaning and visible response.
    return (f'本段第{time_text(node["start"])}秒至第{time_text(node["end"])}秒，'
            + node['evidence'].rstrip('。') + '。')


def placement_text(shot, listener):
    return ' '.join(p.get('placement', '') for p in shot.get('relation', {}).get('placements', [])
                    if p.get('character') == listener and p.get('visibility') == 'visible')


def check_face_visibility(shot, node, label, errors):
    listener, proof = node['listener'], node['visibility_evidence']
    if node['channel'] != 'face':
        return
    placement = placement_text(shot, listener)
    if (re.search(r'肩背|背影|背对|后脑', placement)
            and not re.search(r'侧脸|正脸|面部可见|脸部可见', placement)):
        errors.append(f'{label}: shoulder/back-only listener cannot establish a readable face reaction')
    # Narrow explicit checks, not a reconstruction of camera geometry.
    if not re.search(r'侧脸|正脸|面部|脸部|眉眼', proof) or re.search(r'不可见|看不见|看不清|遮住|遮挡|仅见肩|只露肩', proof):
        errors.append(f'{label}: face reaction needs explicit readable facial visibility')


def check_dialogue_coverage(plan, errors):
    """Return authored reaction/hold evidence indexed by actual shot; do not invent it."""
    segments = {s['id']: s for s in plan['segments']}
    sources = {s['id']: s for s in plan['sources']}
    bindings = check_dialogue_editing(plan, errors)
    eligible = set()
    for turn in dialogue_turns(plan):
        first, first_item, _ = turn[0]
        eligible.add(first)
        label = f'{first[0]}/speech[{first[1]}]/dialogue_review'
        items = {key: item for key, item, _ in turn}
        required_targets = set()
        required_channels = set()
        for key, item, _ in turn:
            targets = sources.get(item['voice']['source_id'], {}).get('reaction_targets', [])
            if not isinstance(targets, list) or any(not nonempty(t) for t in targets):
                errors.append(f'{label}: source reaction_targets must be a list of listener names')
            else:
                required_targets.update((item['voice']['source_id'], t) for t in targets)
            channels = sources.get(item['voice']['source_id'], {}).get('reaction_channels', {})
            if (not isinstance(channels, dict) or any(not nonempty(t) or not isinstance(c, str)
                    or c not in ('face', 'body') for t, c in channels.items())):
                errors.append(f'{label}: source reaction_channels must map listener names to face/body')
            else:
                required_targets.update((item['voice']['source_id'], t) for t in channels)
                required_channels.update((item['voice']['source_id'], t, c) for t, c in channels.items())
        # Speech slots are already checked for illegal overlaps elsewhere.
        seconds = sum(item['end'] - item['start'] for _, item, _ in turn)
        review = first_item['voice'].get('dialogue_review')
        if review is None and seconds <= REVIEW_SECONDS + EPS and not required_targets:
            continue
        if not isinstance(review, dict):
            errors.append(f'{label}: long dialogue or required reception needs a dialogue_review; no automatic cut is imposed')
            continue
        mode, listeners = review.get('mode'), review.get('listeners')
        if (mode not in ('covered', 'hold_speaker', 'no_listener') or not isinstance(listeners, list)
                or any(not nonempty(n) for n in listeners) or len(set(listeners)) != len(listeners)
                or first_item['voice']['speaker'] in listeners or not nonempty(review.get('basis'))):
            errors.append(f'{label}: need mode, distinct listeners other than the speaker, and concrete source/hearing basis')
            continue
        nodes = review.get('reactions', [])
        if not isinstance(nodes, list):
            errors.append(f'{label}: reactions must be a list')
            continue
        if mode == 'no_listener':
            if listeners or nodes or required_targets or review.get('hold'):
                errors.append(f'{label}: no_listener cannot erase listeners or required source reactions')
            continue
        if not listeners:
            errors.append(f'{label}: covered/hold_speaker needs the relevant listener names')
        if mode == 'hold_speaker':
            if nodes or required_targets:
                errors.append(f'{label}: a held speaker cannot replace required source reactions')
            hold = review.get('hold')
            if not isinstance(hold, dict):
                errors.append(f'{label}: held speaker needs a concrete hold record')
                continue
            reason, why, evidence = hold.get('reason'), hold.get('why'), hold.get('evidence')
            empty_reason = re.sub(r'保持台词连续|台词还没说完|避免断句|同一说话人|一分镜一人|保持张力|持续讲话|为了|所以|必须|不切镜|[\W_]', '', why) if isinstance(why, str) else ''
            if (reason not in ('speaker_performance', 'deliberate_withholding', 'user_requested')
                    or not nonempty(why) or not empty_reason or not nonempty(evidence)):
                errors.append(f'{label}: hold needs a concrete performance/viewpoint/user reason, not audio continuity')
                continue
            shot = shot_at(segments, hold, label, errors)
            if shot is not None:
                if first_item['voice']['speaker'] not in shot.get('visible_characters', []) or not positive_evidence(shot.get('visual_text', ''), evidence):
                    errors.append(f'{label}: held performance evidence must occur in its authored visible shot')
                target_time = playback_time(plan, hold['segment_id'], shot['start'], shot['end'])
                if not target_time or target_time[1] <= first_item['start'] or target_time[0] >= turn[-1][1]['end']:
                    errors.append(f'{label}: held shot must overlap this dialogue turn')
                bindings.setdefault((hold['segment_id'], shot['start']), []).append(('hold', hold))
            continue
        if not nodes or review.get('hold'):
            errors.append(f'{label}: covered requires actual reception nodes, not a hold record')
        covered = set()
        channels_seen = set()
        arrivals = {}
        for ni, node in enumerate(nodes):
            nl = f'{label}/reaction[{ni}]'
            if not isinstance(node, dict):
                errors.append(f'{nl}: expected a reaction object')
                continue
            listener, trigger = node.get('listener'), node.get('trigger')
            if not isinstance(listener, str) or listener not in listeners or not isinstance(trigger, dict):
                errors.append(f'{nl}: identify a reviewed listener and original speech trigger')
                continue
            si, sid = trigger.get('speech_index'), trigger.get('segment_id')
            key = (sid, si) if isinstance(sid, str) and type(si) is int else None
            item = items.get(key)
            heard, quote = trigger.get('heard_at'), trigger.get('quote')
            if item is None or not finite(heard) or not nonempty(quote):
                errors.append(f'{nl}: trigger must reference speech in this turn and its information-arrival time')
                continue
            voice = item['voice']
            if not voice['start'] <= heard <= voice['end'] or not quote_in_speech(quote, voice['text']):
                errors.append(f'{nl}: trigger quote/time must belong to its original speech interval')
                continue
            if heard <= voice['start'] + EPS:
                errors.append(f'{nl}: audible trigger must arrive after speech starts, not at its first instant')
                continue
            spans = quote_spans(quote, voice['text'])
            if len(spans) == 1:
                prefix = ' '.join(voice['text'].split())[:spans[0][1]]
                # Trailing punctuation is not another spoken information point.
                while prefix and not prefix[-1].isalnum():
                    prefix = prefix[:-1]
                quote_end = len(prefix)
                for prior_end, prior_heard in arrivals.get(key, []):
                    if ((quote_end > prior_end and heard < prior_heard - EPS)
                            or (quote_end < prior_end and heard > prior_heard + EPS)):
                        errors.append(f'{nl}: trigger arrival order contradicts phrase order in the same speech')
                        break
                arrivals.setdefault(key, []).append((quote_end, heard))
            shot = shot_at(segments, node, nl, errors)
            if shot is None:
                continue
            start, end = node.get('start'), node.get('end')
            if not finite(start) or not finite(end) or not shot['start'] <= start < end <= shot['end']:
                errors.append(f'{nl}: reaction range must be inside its actual shot')
                continue
            reception_time = playback_time(plan, node['segment_id'], start, end)
            arrived = item['start'] + heard - voice['start']
            if not reception_time or reception_time[0] < arrived - EPS:
                errors.append(f'{nl}: reaction precedes information arrival in actual playback')
            elif reception_time[0] >= turn[-1][1]['end'] - EPS:
                after = node.get('after_basis')
                concrete = re.sub(r'等台词说完|等说完|台词结束|等整句结束|留出反应时间|为了|所以|[\W_]', '', after) if isinstance(after, str) else ''
                if not nonempty(after) or not concrete:
                    errors.append(f'{nl}: postponing reception until after the turn needs a source/readability basis')
            if segments[node['segment_id']]['episode'] != segments[sid]['episode']:
                errors.append(f'{nl}: reaction cannot move into another episode')
            evidence, proof = node.get('evidence'), node.get('visibility_evidence')
            if (not nonempty(evidence) or listener not in evidence or not nonempty(proof) or listener not in proof
                    or node.get('channel') not in ('face', 'body')):
                errors.append(f'{nl}: need named visible performance, face/body channel and visibility evidence')
                continue
            authored = shot.get('camera_text', '') + shot.get('visual_text', '') + placement_text(shot, listener)
            if listener not in shot.get('visible_characters', []) or not positive_evidence(authored, proof):
                errors.append(f'{nl}: listener visibility must be supported in this authored shot, not only the cast list')
            check_face_visibility(shot, node, nl, errors)
            covered.add((voice['source_id'], listener))
            channels_seen.add((voice['source_id'], listener, node['channel']))
            bindings.setdefault((node['segment_id'], shot['start']), []).append(('reaction', node))
        if required_targets - covered:
            errors.append(f'{label}: required source reaction_targets are missing from visible reception nodes')
        if required_channels - channels_seen:
            errors.append(f'{label}: required source reaction_channels are missing; body coverage cannot replace a required face reaction')
        if set(listeners) - {listener for _, listener in covered}:
            errors.append(f'{label}: reviewed listeners lack visible reception coverage')
    for segment in plan['segments']:
        for index, voice in enumerate(segment['speech']):
            if 'dialogue_review' in voice and (segment['id'], index) not in eligible:
                errors.append(f'{segment["id"]}/speech[{index}]: dialogue_review belongs at the first speech of the actual dialogue turn')
    return bindings


def reception_prose(segment, shot, bindings):
    return ''.join(reaction_prose(record) for kind, record in bindings.get((segment['id'], shot['start']), [])
                   if kind in ('reaction', 'dialogue_beat'))


def check_reception_binding(segment, shot, bindings, camera_values, content_values, errors):
    label = f'{segment["id"]}/{shot["start"]}'
    for kind, record in bindings.get((segment['id'], shot['start']), []):
        expected = record['evidence'] if kind == 'hold' else reaction_prose(record)
        if not any(positive_evidence(value, expected) for value in content_values):
            errors.append(f'{label}: dialogue reception/performance is absent from its final visual interval')
        if kind == 'reaction' and not any(positive_evidence(value, record['visibility_evidence']) for value in camera_values + content_values):
            errors.append(f'{label}: listener visibility evidence is absent from its final shot')
