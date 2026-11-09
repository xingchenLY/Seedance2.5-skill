"""Route every narrated picture to natural-endpoint content review.

Speech duration and a source-to-picture mapping do not establish useful visual
duration. This reports actual shots, including short ones; it does not estimate
movement speed, invent actions, change timing or certify generated video.
"""
from shot_pacing import fmt

EPS = 1e-7
KINDS = {'narration': '叙事旁白', 'inner': '内心声'}


def review_narrative_visual_pacing(plan):
    """Locate whole shots touched by narration/inner speech in narrative modes.

    Call after structural validation. The returned voice ranges are intersections,
    not a claim that sound covers the whole shot or keeps its picture active.
    """
    if plan.get('mode') not in ('推文', '3D漫推文'):
        return []
    findings = []
    for segment in plan['segments']:
        for shot in segment['shots']:
            ranges = []
            for index, voice in enumerate(segment['speech']):
                if voice['kind'] not in KINDS:
                    continue
                start, end = max(shot['start'], voice['start']), min(shot['end'], voice['end'])
                if end > start + EPS:
                    ranges.append(dict(speech_index=index, kind=voice['kind'], start=start, end=end))
            if ranges:
                findings.append(dict(segment_id=segment['id'], shot_start=shot['start'],
                                     shot_end=shot['end'], voice_ranges=ranges))
    return findings


def print_narrative_visual_review(plan):
    """Compact routing outside the deliverable; no automatic pass/fail judgment."""
    findings = review_narrative_visual_pacing(plan)
    if not findings:
        return
    print('REVIEW: 推文旁白／内心声画面逐镜核对自然结束点；短镜同样检查，声音时长不等于动作时长。')
    grouped = {}
    for item in findings:
        voices = '、'.join(f'{KINDS[v["kind"]]}{fmt(v["start"])}–{fmt(v["end"])}秒'
                          for v in item['voice_ranges'])
        label = f'实际镜头{fmt(item["shot_start"])}–{fmt(item["shot_end"])}秒（{voices}）'
        grouped.setdefault(item['segment_id'], []).append(label)
    for segment_id, labels in grouped.items():
        print(f'REVIEW [narrative_visual_pacing] {segment_id}: ' + '；'.join(labels))
    print('REVIEW: 对照原文核实正常速度下动作／阅读／必要情绪的落点及落点后内容；'
          '无有效进展时重排有依据的画面，不拉慢、重复或提前揭露来填时。'
          '实际修订或保留依据并入既有 timing／visual_continuity，覆盖全镜及相邻镜头；'
          '本提示不判超时、不测动作速度、不代替内容复核。')

