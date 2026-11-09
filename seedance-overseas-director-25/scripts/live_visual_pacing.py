"""Locate live-action unvoiced pictures for content-based pacing review.

No duration is allocated, shortened or certified here. Short pictures remain in
scope; three seconds only highlights longer intervals. Run after validation.
"""
from shot_pacing import fmt

EPS = 1e-7
REVIEW_SECONDS = 3


def unvoiced_intervals(segment, shot):
    """Subtract all speech (including carried VO/OS) from this actual shot."""
    covered = sorted((max(shot['start'], v['start']), min(shot['end'], v['end']))
                     for v in segment['speech']
                     if min(shot['end'], v['end']) - max(shot['start'], v['start']) > EPS)
    cursor, gaps = shot['start'], []
    for start, end in covered:
        if start > cursor + EPS:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < shot['end'] - EPS:
        gaps.append((cursor, shot['end']))
    return gaps


def review_live_visual_pacing(plan):
    """Return all wholly unvoiced shots plus long unvoiced parts of mixed shots.

Brief natural gaps in voiced shots still need ordinary content review; they are
not automatically diagnosed as empty time. Sound effects do not count as speech.
"""
    if plan.get('mode') != '真人剧':
        return []
    findings = []
    for segment in plan['segments']:
        for shot in segment['shots']:
            for start, end in unvoiced_intervals(segment, shot):
                whole = abs(start - shot['start']) <= EPS and abs(end - shot['end']) <= EPS
                longer = end - start > REVIEW_SECONDS + EPS
                if whole or longer:
                    findings.append(dict(segment_id=segment['id'], shot_start=shot['start'], shot_end=shot['end'],
                                         start=start, end=end, whole_shot=whole, longer_review=longer))
    return findings


def print_live_visual_review(plan):
    """One compact line per affected segment, outside the delivered prompt."""
    findings = review_live_visual_pacing(plan)
    if not findings:
        return
    print('REVIEW: 真人剧纯画面按内容定时；3秒内同样检查，3秒不是默认镜长或硬上限。')
    grouped = {}
    for finding in findings:
        a, b = fmt(finding['start']), fmt(finding['end'])
        if finding['whole_shot']:
            label = f'纯画面{a}–{b}秒'
        else:
            label = f'镜头{fmt(finding["shot_start"])}内无发声{a}–{b}秒'
        if finding['longer_review']:
            label += '（超过3秒，核对持续任务）'
        grouped.setdefault(finding['segment_id'], []).append(label)
    for segment_id, labels in grouped.items():
        print(f'REVIEW [live_visual_pacing] {segment_id}: ' + '；'.join(labels))
    print('REVIEW: 核对信息可读、动作与必要情绪完成时点及多余停留；保留原声和衔接。'
          '具体结论并入既有 delivery_review.timing；提示不代表超时错误或自动缩时。')
