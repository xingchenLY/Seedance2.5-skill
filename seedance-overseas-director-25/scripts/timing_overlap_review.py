"""Locate authored speech-uncovered time; do not diagnose or remove idle time.

All speech intervals share the picture timeline. Their complement can contain
necessary silence, actions, reading or emotional performance. Content review
decides whether any of it can overlap the original speech; this never retimes.
"""
from live_visual_pacing import unvoiced_intervals
from shot_pacing import EPS, fmt


def adjacent_speech(plan, segment, start, end):
    """Locate comparison windows, never infer that moving a task is permissible.

    Same-segment neighbors come first. At a clip boundary, also consider the
    immediately adjacent playback clip in the same episode. Do not skip silent
    clips, bridge episodes, or pretend segment-local seconds are global time.
    """
    candidates = []

    def add(pool, direction):
        if not pool:
            return
        boundary = (max(v['end'] for _, _, v in pool) if direction == 'before'
                    else min(v['start'] for _, _, v in pool))
        for sid, index, voice in pool:
            if abs(voice['end' if direction == 'before' else 'start'] - boundary) <= EPS:
                key = (sid, index)
                if any((v['segment_id'], v['speech_index']) == key for v in candidates):
                    continue
                candidates.append(dict(segment_id=sid, speech_index=index, direction=direction,
                    start=voice['start'], end=voice['end'], speaker=voice.get('speaker', ''),
                    kind=voice.get('kind', ''), source_id=voice.get('source_id', '')))

    voices = [(segment['id'], i, v) for i, v in enumerate(segment['speech'])]
    add([v for v in voices if v[2]['end'] <= start + EPS], 'before')
    add([v for v in voices if v[2]['start'] >= end - EPS], 'after')
    clips = plan.get('playback', [dict(segment_id=s['id'], start=0, end=s['duration'])
                                  for s in plan['segments']])
    by_id = {s['id']: s for s in plan['segments']}
    for ci, clip in enumerate(clips):
        if clip['segment_id'] != segment['id']:
            continue
        for direction, edge, offset in [('before', start, -1), ('after', end, 1)]:
            clip_edge = clip['start' if direction == 'before' else 'end']
            if abs(edge - clip_edge) > EPS or not 0 <= ci + offset < len(clips):
                continue
            other_clip = clips[ci + offset]
            other = by_id[other_clip['segment_id']]
            # A missing episode in an incomplete locator-only input is not proof.
            if not segment.get('episode') or other.get('episode') != segment['episode']:
                continue
            add([(other['id'], i, v) for i, v in enumerate(other['speech'])
                 if v['start'] >= other_clip['start'] - EPS
                 and v['end'] <= other_clip['end'] + EPS], direction)
    return candidates


def gap_summary(gap):
    windows = '、'.join(
        f"{v['segment_id']}/speech[{v['speech_index']}] "
        f"{fmt(v['start'])}–{fmt(v['end'])}秒"
        for v in gap['speech_candidates'])
    return (f"{fmt(gap['start'])}–{fmt(gap['end'])}秒"
            + (f'（待比较原声：{windows}）' if windows else '（邻近无原声）'))


def review_timing_overlap(plan):
    """Return segment gaps and intersecting actual shots after validation.

Use every speech kind, including carried speech, regardless of mode. Shot cuts
do not create gaps or add time. This locates authored ranges, not measured audio
or the semantic duration of actions; fully covered time still needs review.
"""
    findings = []
    for segment in plan['segments']:
        duration = segment['duration']
        gaps = unvoiced_intervals(segment, dict(start=0, end=duration))
        if not gaps:
            continue
        findings.append(dict(
            segment_id=segment['id'], duration=duration,
            speech_covered_seconds=duration - sum(b - a for a, b in gaps),
            uncovered_ranges=[dict(
                start=a, end=b,
                speech_candidates=adjacent_speech(plan, segment, a, b),
                shot_starts=[shot['start'] for shot in segment['shots']
                             if min(b, shot['end']) > max(a, shot['start']) + EPS])
                for a, b in gaps]))
    return findings


def print_timing_overlap_review(plan):
    """Compact review locations outside the prompt, without a pass/fail verdict."""
    findings = review_timing_overlap(plan)
    if not findings:
        return
    print('REVIEW: 四模式核对同期与独立占时；发声记录未覆盖的区间不等于可删时间。')
    for item in findings:
        ranges = []
        for gap in item['uncovered_ranges']:
            shots = '、'.join(fmt(t) for t in gap['shot_starts'])
            ranges.append(gap_summary(gap) + f'（对应镜头起点{shots}秒）')
        covered = fmt(round(item['speech_covered_seconds'], 7))
        print(f'REVIEW [timing_overlap] {item["segment_id"]}: '
              f'段长{fmt(item["duration"])}秒，发声记录覆盖{covered}秒；未覆盖：' + '；'.join(ranges))
    print('REVIEW: 结合原文核对可同期却被后置的动作／反应，以及独立占时的任务和先后依据；'
          '保留必要沉默、情绪、接触、阅读与切镜，不提前知情或新增抢话。'
          '修订／保留依据并入既有 timing；真人剧保留区间另填 timing_overlap 比较记录，变化同步后镜与音效。'
          '程序不自动缩时、不测量真实音频；全程有声也须核对估时是否有据。')
