"""Check a source-linked timing plan; standard library only, no file writes.

Usage: python validate_plan.py plan.json
The plan schema and the limits of this check are in references/validation.md.
"""

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import re
import sys

EPS = 1e-7
KINDS = {'dialogue', 'VO', 'OS', 'narration', 'inner'}
LATIN_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+(?:['’\-][A-Za-zÀ-ÖØ-öø-ÿ0-9]+)*")
NON_LATIN_LANGUAGES = {'zh', 'ja', 'ko', 'ru', 'uk', 'ar', 'he', 'th', 'hi'}
# Review trigger only, not a target or a universal speaking-speed limit.
RATE_REVIEW_THRESHOLD = 8


def normalized(text):
    return ' '.join(text.split())


def source_parts_match(source_text, parts):
    """Match fragments at source positions, preserving its word boundaries."""
    source = normalized(source_text)
    cursor = 0
    for index, part in enumerate(parts):
        if index and cursor < len(source) and source[cursor] == ' ':
            cursor += 1
        fragment = normalized(part)
        if not source.startswith(fragment, cursor):
            return False
        cursor += len(fragment)
        if index < len(parts) - 1 and any(match.start() < cursor < match.end()
                                         for match in LATIN_WORD.finditer(source)):
            return False
    return cursor == len(source)


def english_words(text):
    return sum(any(char.isalnum() for char in word) for word in text.split())


def counting_form(record, text, label, errors):
    """Expand only explicitly located numbers; preserve all other source text."""
    has_numbers = any(char.isdigit() for char in text)
    if not has_numbers:
        if record.get('number_expansions'):
            errors.append(f'{label}: number_expansions supplied without numbers')
        if 'spoken_form' in record and normalized(string(record, 'spoken_form')) != normalized(text):
            errors.append(f'{label}: spoken_form changes nonnumeric text')
        return text
    expansions = require_list(record.get('number_expansions', []), 'number_expansions')
    if not expansions:
        errors.append(f'{label}: expand spoken numbers using number_expansions')
        return text
    pieces, cursor = [], 0
    for expansion in expansions:
        require_object(expansion, 'number expansion')
        start, end = number(expansion, 'start'), number(expansion, 'end')
        replacement = string(expansion, 'spoken')
        if int(start) != start or int(end) != end:
            raise ValueError('number expansion offsets must be integers')
        start, end = int(start), int(end)
        if start < cursor or end <= start or end > len(text):
            errors.append(f'{label}: invalid number expansion range')
            continue
        original = text[start:end]
        if (not any(char.isdigit() for char in original) or
                not re.fullmatch(r'[\d\s$€£¥.,:/%+\-–°()]+(?:st|nd|rd|th)?', original, re.I)):
            errors.append(f'{label}: number expansion may replace numeric expressions only')
        if any(char.isdigit() for char in replacement):
            errors.append(f'{label}: number expansion still contains digits')
        pieces.extend((text[cursor:start], replacement))
        cursor = end
    pieces.append(text[cursor:])
    expanded = ''.join(pieces)
    if any(char.isdigit() for char in expanded):
        errors.append(f'{label}: expand all spoken numbers')
    if 'spoken_form' in record and normalized(string(record, 'spoken_form')) != normalized(expanded):
        errors.append(f'{label}: spoken_form differs from number-only expansion')
    return expanded


def speech_english_words(speech, language, label, errors):
    """Count English runs without treating an entire mixed utterance as non-English."""
    text = speech['text']
    language = language.lower().split('-')[0]
    if 'language_parts' in speech:
        parts = require_list(speech['language_parts'], 'language_parts')
        texts, count = [], 0
        for part in parts:
            require_object(part, 'language part')
            part_text = string(part, 'text')
            part_language = string(part, 'language').lower().split('-')[0]
            if len(part_language) != 2 or not part_language.isascii() or not part_language.isalpha():
                raise ValueError('language_parts must use single two-letter language codes')
            texts.append(part_text)
            count += speech_english_words(part, part_language, label, errors)
        if not parts or not source_parts_match(text, texts):
            errors.append(f'{label}: language_parts do not reconstruct the utterance')
        if 'spoken_form' in speech or 'number_expansions' in speech:
            errors.append(f'{label}: put numeric expansions inside the corresponding language part')
        return count
    if language == 'mul' or (language in NON_LATIN_LANGUAGES and re.search(r'[A-Za-z]', text)):
        errors.append(f'{label}: mixed language requires explicit language_parts')
    if language in {'en', 'es', 'pt'}:
        count = english_words(counting_form(speech, text, label, errors))
        # The word-rate estimate is shared; the existing English-only cap is not.
        return count if language == 'en' else 0
    if language == 'zh':
        # Chinese readings use the same numeric-range protection, but have an
        # independent character counter and never add to the English limit.
        counting_form(speech, text, label, errors)
        return 0
    if 'spoken_form' in speech or 'number_expansions' in speech:
        errors.append(f'{label}: English counting fields require an English language part')
    return 0


def require_list(value, label):
    if not isinstance(value, list):
        raise ValueError(f'{label}: expected an array')
    return value


def require_object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label}: expected an object')
    return value


def string(record, key):
    value = record[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{key}: expected a nonempty string')
    return value


def number(record, key, minimum=0):
    value = record[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{key}: expected a number')
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f'{key}: expected a finite number >= {minimum}')
    return value


def playback_speeches(plan, segments, errors):
    """Map complete generated ranges to narrative order, without duplicating them."""
    clips = plan.get('playback', [dict(segment_id=sid, start=0, end=segment['duration'])
                                  for sid, segment in segments.items()])
    require_list(clips, 'playback')
    used_until = defaultdict(float)
    ordered = []
    seen_episodes = []
    for index, clip in enumerate(clips):
        require_object(clip, 'playback clip')
        sid = string(clip, 'segment_id')
        start, end = number(clip, 'start'), number(clip, 'end')
        label = f'playback[{index}]/{sid}'
        if sid not in segments:
            errors.append(f'{label}: unknown generated segment')
            continue
        segment = segments[sid]
        if end <= start or end > segment['duration'] + EPS:
            errors.append(f'{label}: playback range is outside the segment or invalid')
            continue
        if abs(start - used_until[sid]) > EPS:
            errors.append(f'{label}: playback skips, repeats or reverses generated material')
        used_until[sid] = end
        boundaries = [0] + [shot['end'] for shot in segment['shots']]
        if any(not any(abs(point - boundary) <= EPS for boundary in boundaries)
               for point in (start, end)):
            errors.append(f'{label}: playback cut must align with a shot boundary')
        eid = segment['episode']
        if not seen_episodes or seen_episodes[-1] != eid:
            seen_episodes.append(eid)
        for speech in segment['speech']:
            if speech['start'] < end - EPS and speech['end'] > start + EPS:
                if speech['start'] < start - EPS or speech['end'] > end + EPS:
                    errors.append(f'{label}: playback cut splits a speech record')
                else:
                    ordered.append(speech)
    for sid, segment in segments.items():
        if abs(used_until[sid] - segment['duration']) > EPS:
            errors.append(f'{sid}: playback does not use the complete generated segment')
    if seen_episodes != [episode['id'] for episode in plan['episodes']]:
        errors.append('playback must cover selected episodes in order without interleaving')
    return ordered


def validate_events(plan, segments, errors):
    """Check source-ordered story units, including silent footage and returns."""
    events = require_list(plan.get('events', []), 'events')
    if not events:
        if 'playback' in plan or 'events' in plan:
            errors.append('events: source story units are required for explicit playback')
        return
    clips = plan.get('playback', [dict(segment_id=sid, start=0, end=segment['duration'])
                                  for sid, segment in segments.items()])
    # Clip schema and complete usage were checked by playback_speeches.
    placed_clips = []
    elapsed = 0
    for clip in clips:
        if (clip['segment_id'] not in segments or clip['end'] <= clip['start'] or
                clip['end'] > segments[clip['segment_id']]['duration'] + EPS):
            continue
        placed_clips.append((clip, elapsed))
        elapsed += clip['end'] - clip['start']
    ids = set()
    ranges = defaultdict(list)
    previous_end = 0
    for event in events:
        require_object(event, 'event')
        event_id = string(event, 'id')
        eid, sid = string(event, 'episode'), string(event, 'segment_id')
        string(event, 'text')
        start, end = number(event, 'start'), number(event, 'end')
        if event_id in ids:
            errors.append(f'{event_id}: duplicate event id')
        ids.add(event_id)
        if sid not in segments:
            errors.append(f'{event_id}: unknown event segment')
            continue
        if eid != segments[sid]['episode']:
            errors.append(f'{event_id}: event belongs to another episode')
        if end <= start or end > segments[sid]['duration'] + EPS:
            errors.append(f'{event_id}: event is outside the segment or has invalid duration')
            continue
        ranges[sid].append((start, end))
        placements = [offset + start - clip['start'] for clip, offset in placed_clips
                      if clip['segment_id'] == sid and start >= clip['start'] - EPS
                      and end <= clip['end'] + EPS]
        if len(placements) != 1:
            errors.append(f'{event_id}: event must be used once without splitting across playback clips')
            continue
        position = placements[0]
        if position < previous_end - EPS:
            errors.append(f'playback/{event_id}: event order changed')
        previous_end = position + end - start
    for sid, segment in segments.items():
        end = 0
        for start, next_end in sorted(ranges[sid]):
            if abs(start - end) > EPS:
                errors.append(f'{sid}: event coverage has a gap or overlap')
            end = next_end
        if abs(end - segment['duration']) > EPS:
            errors.append(f'{sid}: event coverage must include the complete generated segment')


def validate(plan):
    """Return findings. Missing/invalid schema fields raise ValueError/KeyError."""
    require_object(plan, 'plan')
    errors = []
    from mode_rules import narrative_mode, check_mode, overlap_allowed
    from narrative_budget import check_narrative_budgets
    errors.extend(check_narrative_budgets(plan))
    limits = {'english_words': 96, 'segment_seconds': 30, 'episode_seconds': 50 if narrative_mode(plan) else 0,
              'unrelated_prefix_seconds': 4}
    overrides = require_object(plan.get('limits', {}), 'limits')
    if set(overrides) - set(limits):
        raise ValueError('limits: unknown key')
    if overrides:
        string(plan, 'limits_basis')
    for key in overrides:
        limits[key] = number(overrides, key, 0 if key == 'episode_seconds' else EPS)
    if int(limits['english_words']) != limits['english_words']:
        raise ValueError('english_words limit: expected an integer')

    episodes = {}
    for episode in require_list(plan['episodes'], 'episodes'):
        require_object(episode, 'episode')
        eid = string(episode, 'id')
        if type(episode['complete']) is not bool:
            raise ValueError('complete: expected a boolean')
        if eid in episodes:
            errors.append(f'duplicate episode: {eid}')
        episodes[eid] = episode
    if not episodes:
        raise ValueError('episodes: at least one selected scope is required')

    sources = {}
    ranks = {}
    for source in require_list(plan['sources'], 'sources'):
        require_object(source, 'source')
        sid = string(source, 'id')
        for key in ('episode', 'speaker', 'kind', 'language', 'text'):
            string(source, key)
        if source['kind'] not in KINDS:
            raise ValueError(f'{sid}: unsupported kind')
        language = source['language'].split('-')[0]
        if language != 'mul' and (len(language) != 2 or not language.isascii() or not language.isalpha()):
            raise ValueError(f'{sid}: language must use a two-letter code such as en or zh')
        if sid in sources:
            errors.append(f'duplicate source: {sid}')
        if source['episode'] not in episodes:
            errors.append(f'{sid}: source outside selected episodes')
        ranks[sid] = len(ranks)
        sources[sid] = source

    reconstructed = defaultdict(list)
    seconds = defaultdict(float)
    segment_ids = set()
    segments_by_id = {}
    seen_episodes = []
    for segment in require_list(plan['segments'], 'segments'):
        require_object(segment, 'segment')
        label = string(segment, 'id')
        eid = string(segment, 'episode')
        if label in segment_ids:
            errors.append(f'duplicate segment: {label}')
        segment_ids.add(label)
        segments_by_id[label] = segment
        if eid not in episodes:
            errors.append(f'{label}: segment outside selected episodes')
        if not seen_episodes or seen_episodes[-1] != eid:
            seen_episodes.append(eid)
        duration = number(segment, 'duration', EPS)
        unrelated_prefix = segment.get('unrelated_prefix', False)
        if type(unrelated_prefix) is not bool:
            raise ValueError(f'{label}: unrelated_prefix must be a boolean')
        if unrelated_prefix and duration < limits['unrelated_prefix_seconds'] - EPS:
            errors.append(f'{label}: unrelated prefix duration is below minimum')
        seconds[eid] += duration
        from shot_pacing import check_segment_duration
        check_segment_duration(segment, label, errors)
        if duration > limits['segment_seconds'] + EPS:
            errors.append(f'{label}: segment duration exceeds limit')
        declared_words = number(segment, 'english_words')
        if int(declared_words) != declared_words:
            raise ValueError(f'{label}: english_words must be an integer')

        shots = require_list(segment['shots'], f'{label}.shots')
        previous_end = 0
        shot_ranges = []
        for shot in shots:
            require_object(shot, 'shot')
            start, end = number(shot, 'start'), number(shot, 'end')
            if abs(start - previous_end) > EPS or end <= start:
                errors.append(f'{label}: shot timeline has a gap, overlap or invalid range')
            if any(abs(t * 10 - round(t * 10)) > EPS for t in (start, end)):
                errors.append(f'{label}: shot boundaries must use 0.1-second or coarser units')
            from shot_pacing import check_shot_pacing
            check_shot_pacing(shot, f'{label}/{start}', errors)
            previous_end = end
            shot_ranges.append((start, end))
        if not shots or abs(previous_end - duration) > EPS:
            errors.append(f'{label}: shot total does not match segment duration')

        speeches = require_list(segment['speech'], f'{label}.speech')
        speech_ranges = []
        counted_words = 0
        previous_start = -1
        for speech in speeches:
            require_object(speech, 'speech')
            sid = string(speech, 'source_id')
            utterance = string(speech, 'text')
            speaker, kind = string(speech, 'speaker'), string(speech, 'kind')
            start, end = number(speech, 'start'), number(speech, 'end')
            estimated = number(speech, 'estimated_seconds', EPS)
            if end <= start or end > duration + EPS:
                errors.append(f'{label}/{sid}: speech is outside the segment or has invalid duration')
            if estimated > end - start + EPS:
                errors.append(f'{label}/{sid}: speech estimate does not fit its time slot')
            if start < previous_start - EPS:
                errors.append(f'{label}: speech records must follow start-time order')
            previous_start = start
            speech_ranges.append((start, end))
            if sid not in sources:
                errors.append(f'{label}: unknown source {sid}')
                continue
            source = sources[sid]
            if source['episode'] != eid:
                errors.append(f'{label}/{sid}: speech belongs to another episode')
            if speaker != source['speaker'] or kind != source['kind']:
                errors.append(f'{label}/{sid}: speaker or voice kind differs from source')
            words = speech_english_words(speech, source['language'], f'{label}/{sid}', errors)
            counted_words += words
            if 'mode' in plan:
                from chinese_timing import check_chinese_timing
                check_chinese_timing(speech, source['language'], f'{label}/{sid}', errors)
            if words / estimated > RATE_REVIEW_THRESHOLD:
                evidence = speech.get('timing_review')
                if evidence is None:
                    errors.append(f'{label}/{sid}: implausible speech estimate requires timing_review')
                else:
                    require_object(evidence, 'timing_review')
                    # This is a review record, not machine verification of an audio asset.
                    if evidence.get('basis') != 'measured_audio':
                        errors.append(f'{label}/{sid}: extreme speech rate requires measured audio evidence')
                    string(evidence, 'source')
                    string(evidence, 'measurement')
                    if abs(number(evidence, 'seconds', EPS) - estimated) > EPS:
                        errors.append(f'{label}/{sid}: measured audio seconds differ from estimate')

        overlap_basis = segment.get('overlap_basis')
        if overlap_basis is not None:
            string(segment, 'overlap_basis')
        if not overlap_basis:
            for index, (start, end) in enumerate(speech_ranges):
                if any(start < other_end - EPS and end > other_start + EPS
                       for other_start, other_end in speech_ranges[:index]):
                    errors.append(f'{label}: overlapping speech has no source basis')
                    break
        for start, end in shot_ranges:
            kinds = {voice['kind'] for voice in speeches
                     if start < voice['end'] - EPS and end > voice['start'] + EPS}
            if len(kinds) > 1 and not overlap_allowed(plan, segment, [voice for voice in speeches
                    if start < voice['end'] - EPS and end > voice['start'] + EPS]):
                errors.append(f'{label}: one continuous shot contains multiple voice kinds')
        if counted_words != declared_words:
            errors.append(f'{label}: English word count {declared_words} != actual {counted_words}')
        if counted_words > limits['english_words']:
            errors.append(f'{label}: English word count exceeds limit')

    previous_rank = -1
    for speech in playback_speeches(plan, segments_by_id, errors):
        sid = speech['source_id']
        if sid not in sources:
            continue
        if ranks[sid] < previous_rank:
            errors.append(f'playback/{sid}: source order changed')
        previous_rank = ranks[sid]
        reconstructed[sid].append(speech['text'])

    for sid, source in sources.items():
        if sid not in reconstructed:
            errors.append(f'{sid}: source speech missing')
        elif not source_parts_match(source['text'], reconstructed[sid]):
            errors.append(f'{sid}: source text changed, duplicated or incompletely reconstructed')
    from speech_continuity import speech_context
    speech_context(plan, errors)
    from action_continuity import action_context
    action_context(plan, errors)
    validate_events(plan, segments_by_id, errors)
    from sound_timing import sound_context
    sound_context(plan, errors)
    from delivery_checks import validate_visual_plan
    validate_visual_plan(plan, errors)
    from prop_inventory import prop_declaration
    for segment in plan['segments']:
        prop_declaration(segment, errors)
    if any('frame_id' in shot for segment in plan['segments'] for shot in segment['shots']):
        from dialogue_reactions import check_dialogue_coverage
        check_dialogue_coverage(plan, errors)
    if seen_episodes != list(episodes):
        errors.append('segments must cover selected episodes once in selected order without interleaving')
    for eid, episode in episodes.items():
        if episode['complete'] and seconds[eid] < limits['episode_seconds'] - EPS:
            errors.append(f'{eid}: complete episode is shorter than the episode minimum')
    check_mode(plan, errors)
    return errors


def validate_timing_preflight(plan):
    """Check minimal timing records before authoring camera/continuity metadata."""
    from shot_pacing import check_segment_duration
    errors = []
    from narrative_budget import check_narrative_budgets, NARRATIVE
    if plan.get('mode') in NARRATIVE:
        errors.extend(check_narrative_budgets(plan, required=True))
        for item in plan['segments']:
            if 'duration' in item:
                check_segment_duration(item, item['id'], errors, required=True)
        return errors
    overrides = require_object(plan.get('limits', {}), 'limits')
    if overrides:
        string(plan, 'limits_basis')
    maximum = number(overrides, 'segment_seconds', EPS) if 'segment_seconds' in overrides else 30
    segments = require_list(plan['segments'], 'segments')
    if not segments:
        raise ValueError('segments: at least one timing record is required')
    for item in segments:
        require_object(item, 'timing segment')
        label = string(item, 'id')
        raw = number(item, 'pre_round_duration', EPS)
        if 'timing_feasibility' not in item:
            errors.append(f'{label}: preflight needs timing_feasibility from actual sound/action constraints')
        segment = dict(item, duration=math.floor(raw))
        if segment['duration'] < 1:
            errors.append(f'{label}: integer segment would be shorter than 1 second; regroup content')
        if segment['duration'] > maximum:
            errors.append(f'{label}: integer segment exceeds {maximum:g} seconds; split at a natural boundary')
        if 'duration' in item and item['duration'] != segment['duration']:
            errors.append(f'{label}: duration differs from floor(pre_round_duration)')
        check_segment_duration(segment, label, errors, required=True)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('--timing-only', action='store_true', help='Check minimal feasibility records before shot design.')
    parser.add_argument('--final-prompt', type=Path,
                        help='Require delivery metadata and compare the actual final prompt file.')
    args = parser.parse_args()
    try:
        plan = json.loads(args.plan.read_text(encoding='utf-8-sig'))
        from mode_rules import require_mode
        require_mode(plan)
        if not args.timing_only:
            from language_policy import require_content_language
            require_content_language(plan)
        if args.timing_only and args.final_prompt:
            raise ValueError('--timing-only cannot replace or combine with --final-prompt')
        findings = validate_timing_preflight(plan) if args.timing_only else validate(plan)
        from narrative_budget import check_narrative_budgets
        findings.extend(check_narrative_budgets(plan, required=True))
        structure_ok = not findings
        if args.final_prompt:
            from delivery_checks import validate_delivery
            final_text = args.final_prompt.read_text(encoding='utf-8-sig')
            findings.extend(validate_delivery(plan, final_text))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, KeyError) as exc:
        print(f'INVALID INPUT: {exc}', file=sys.stderr)
        return 2
    if findings:
        print('\n'.join(f'FAIL: {finding}' for finding in findings))
        if structure_ok and not args.timing_only:
            from review_report import print_review_report
            print_review_report(plan)
        return 1
    if args.timing_only:
        print('PASS: timing preflight only; detailed plan and final delivery still need validation.')
    elif args.final_prompt:
        print('PASS: structured plan and final prompt checked; current scoped content-review records complete; no audio/video verification.')
    else:
        print('PASS: plan checks only; delivery NOT checked. Use --final-prompt for final delivery.')
    if not args.timing_only and not args.final_prompt:
        from review_report import print_review_report
        print_review_report(plan)
    return 0


if __name__ == '__main__':
    sys.exit(main())
