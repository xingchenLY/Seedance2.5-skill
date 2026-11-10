"""Lock narrative segment duration from speech before authoring shots.

This validates records and drift, not whether an author really listened to audio.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from chinese_timing import chinese_speech_seconds, finite, leaves, word_rate_seconds

EPS = 1e-7
NARRATIVE = {'推文', '3D漫推文'}


def measured_seconds(voice):
    value = voice.get('measured_seconds')
    if value is not None:
        if not finite(value) or value <= 0 or not str(voice.get('measurement_basis', '')).strip():
            raise ValueError('measured speech needs positive seconds and actual measurement basis')
        return value
    review = voice.get('timing_review')
    if isinstance(review, dict) and review.get('basis') == 'measured_audio':
        value = review.get('seconds')
        if (not finite(value) or value <= 0 or
                any(not isinstance(review.get(k), str) or not review[k].strip() for k in ('source', 'measurement'))):
            raise ValueError('measured timing needs actual seconds, source and measurement')
        return value
    return None


def derive_budget(plan, segment):
    from validate_plan import speech_english_words, require_object, string, number
    voices = segment.get('speech', [])
    if not isinstance(voices, list) or not voices:
        raise ValueError('no speech: regroup with original voiced content or report the missing duration basis')
    sources = {s['id']: s for s in plan.get('sources', [])}
    total, words, evidence, errors = 0.0, 0, [], []
    for voice in voices:
        source = sources[voice['source_id']]
        language = source['language']
        label = str(segment['id']) + '/' + voice['source_id']
        count = speech_english_words(voice, language, label, errors)
        words += count
        seconds = chinese_speech_seconds(voice, language, label, errors)
        if seconds is None:
            seconds = measured_seconds(voice)
            if seconds is None:
                seconds = 0.0
                for part, code in leaves(voice, language):
                    word_seconds = word_rate_seconds(part, code, label, errors)
                    if word_seconds is not None:
                        seconds += word_seconds
                    else:
                        value = part.get('estimated_seconds')
                        if not finite(value) or value <= 0:
                            raise ValueError(f'{label}: {code} speech needs its own natural-language estimate')
                        seconds += value
        if not finite(seconds) or seconds <= 0:
            raise ValueError(f'{label}: speech must contain timed spoken content')
        total += seconds
        evidence.append(dict(source=source, text=voice['text'], seconds=seconds,
                             readings={k: voice[k] for k in ('language_parts', 'number_expansions',
                                 'spoken_form', 'chinese_timing', 'measured_seconds',
                                 'measurement_basis', 'timing_review') if k in voice}))
    pauses = segment.get('narrative_pauses', [])
    if not isinstance(pauses, list):
        raise ValueError('narrative_pauses must be a list')
    previous = -1.0
    for pause in pauses:
        if (not isinstance(pause, dict) or set(pause) != {'start', 'end', 'basis'} or
                not all(finite(pause.get(k)) for k in ('start', 'end')) or
                pause['start'] < 0 or pause['end'] <= pause['start'] or
                pause['start'] < previous - EPS or
                not isinstance(pause['basis'], str) or not pause['basis'].strip()):
            raise ValueError('narrative pauses need ordered, nonoverlapping intervals and concrete speech basis')
        previous = pause['end']
        total += pause['end'] - pause['start']
    if errors:
        raise ValueError('\n'.join(errors))
    duration = math.ceil(total - EPS)
    overrides = require_object(plan.get('limits', {}), 'limits')
    if set(overrides) - {'english_words', 'segment_seconds', 'episode_seconds', 'unrelated_prefix_seconds'}:
        raise ValueError('limits: unknown key')
    if overrides:
        string(plan, 'limits_basis')
        for key in overrides:
            number(overrides, key, 0 if key == 'episode_seconds' else EPS)
    max_seconds = overrides.get('segment_seconds', 30)
    max_words = overrides.get('english_words', 96)
    if int(max_words) != max_words:
        raise ValueError('english_words limit: expected an integer')
    if duration < 1 or duration > max_seconds or words > max_words:
        raise ValueError(f'speech budget exceeds {max_seconds} seconds / {max_words} English words or is empty; regroup before shots')
    if any(p['end'] > duration + EPS for p in pauses):
        raise ValueError('speech pause exceeds the locked segment')
    fingerprint = hashlib.sha256(json.dumps(dict(voices=evidence, pauses=pauses),
        ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return dict(version=1, speech_sha256=fingerprint, raw_seconds=total, duration=duration)


def check_narrative_budgets(plan, required=False):
    errors = []
    narrative = plan.get('mode') in NARRATIVE
    for segment in plan.get('segments', []):
        label = segment.get('id', '?')
        lock = segment.get('narrative_budget')
        if not narrative:
            if lock is not None:
                errors.append(f'{label}: narrative_budget is only for the two narrative modes')
            continue
        if lock is None:
            if required:
                errors.append(f'{label}: narrative_budget required; calculate and lock speech before authoring shots')
            continue
        try:
            expected = derive_budget(plan, segment)
            if (not isinstance(lock, dict) or lock != expected):
                errors.append(f'{label}: narrative_budget is stale or edited; recount changed speech before regenerating shots')
                continue
            if segment.get('duration') != expected['duration']:
                errors.append(f'{label}: segment duration differs from locked speech budget')
            raw = segment.get('pre_round_duration')
            if not finite(raw) or abs(raw - expected['raw_seconds']) > EPS:
                errors.append(f'{label}: pre_round_duration must be the locked speech total, not visual duration')
            shots = segment.get('shots', [])
            if shots and (shots[0]['start'] != 0 or abs(shots[-1]['end'] - expected['duration']) > EPS):
                errors.append(f'{label}: shot timeline must end at locked speech duration')
            for pause in segment.get('narrative_pauses', []):
                for voice in segment['speech']:
                    if 'start' in voice and 'end' in voice and min(pause['end'], voice['end']) > max(pause['start'], voice['start']) + EPS:
                        errors.append(f'{label}: declared separate pause overlaps speech; do not count an included pause twice')
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            errors.append(f'{label}: invalid narrative budget: {exc}')
    return errors


def lock_plan(plan):
    if plan.get('mode') not in NARRATIVE:
        raise ValueError('speech-first locking applies only to 推文 / 3D漫推文')
    result = deepcopy(plan)
    if not result.get('segments'):
        raise ValueError('at least one segment is required')
    for segment in result['segments']:
        if segment.get('shots') or 'narrative_budget' in segment:
            raise ValueError('lock before shots; to regroup, create a new speech-only input and preserve the old record')
        lock = derive_budget(result, segment)
        segment.update(narrative_budget=lock, duration=lock['duration'], pre_round_duration=lock['raw_seconds'])
    errors = check_narrative_budgets(result, required=True)
    if errors:
        raise ValueError('\n'.join(errors))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.input.resolve() == args.output.resolve() or args.output.exists():
            raise ValueError('write a new output path; do not overwrite the source or an existing lock')
        result = lock_plan(json.loads(args.input.read_text(encoding='utf-8-sig')))
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f'BUDGET FAILED: {exc}\n')
    for segment in result['segments']:
        print(f"{segment['id']}: {segment['pre_round_duration']:.6g}s -> locked {segment['duration']}s")


if __name__ == '__main__':
    main()
