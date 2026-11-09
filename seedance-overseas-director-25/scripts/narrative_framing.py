"""Flag narrative framing decisions for content review, never impose shot quotas.

Run after structural validation. Labels and task mappings can identify candidates,
but only review of the actual composition can establish visual readability.
"""
from cut_boundary_checks import playback_shots

EPS = 1e-7
BROAD = {'中景', '中全景', '全景', '远景'}
WIDE = {'中全景', '全景', '远景'}
NARRATIVE_MODES = {'推文', '3D漫推文'}


def non_scene_overlap(segment, shot):
    return any(voice['kind'] in {'narration', 'inner'} and
               min(voice['end'], shot['end']) > max(voice['start'], shot['start']) + EPS
               for voice in segment['speech'])


def stays_in(shot, scales):
    # A move toward a close view is not a sustained broad shot. Its actual
    # intermediate framing and readable duration still need content review.
    return shot.get('start_scale') in scales and shot.get('end_scale') in scales


def shot_ref(segment, shot):
    return dict(segment_id=segment['id'], start=shot['start'], end=shot['end'],
                start_scale=shot.get('start_scale'), end_scale=shot.get('end_scale'))


def review_narrative_framing(plan):
    """Return non-mutating advisories using actual playback in the narrative modes."""
    if plan.get('mode') not in NARRATIVE_MODES:
        return []
    findings, run = [], []
    previous_episode = None

    def flush():
        if len(run) >= 3:
            findings.append(dict(code='broad_narrative_run', shots=list(run)))
        run.clear()

    for segment, shot in playback_shots(plan):
        if segment['episode'] != previous_episode:
            flush()
        previous_episode = segment['episode']
        voiced = non_scene_overlap(segment, shot)
        if voiced and stays_in(shot, BROAD):
            run.append(shot_ref(segment, shot))
        else:
            flush()
        if not voiced:
            continue
        for unit in segment.get('narration_coverage') or []:
            task = unit['task']
            scales = BROAD if task == 'detail' else WIDE if task == 'reaction' else set()
            if not stays_in(shot, scales):
                continue
            for view in unit['views']:
                if abs(view['shot_start'] - shot['start']) <= EPS:
                    findings.append(dict(code='narrative_task_readability', task=task,
                                         source_id=unit['source_id'], focus=unit['focus'],
                                         evidence=view['evidence'], shots=[shot_ref(segment, shot)]))
    flush()
    return findings


def print_framing_review(plan):
    """CLI diagnostics stay out of prompt text and authored records."""
    findings = review_narrative_framing(plan)
    if not findings:
        return
    print('REVIEW: 推文景别需内容复核；以下是候选问题，不是强制近景配额或自动错误。')
    for finding in findings:
        refs = finding['shots']
        locations = ' → '.join(f"{s['segment_id']}@{s['start']:g}–{s['end']:g}s"
                                f"({s['start_scale']}→{s['end_scale']})" for s in refs)
        if finding['code'] == 'broad_narrative_run':
            reason = f'连续{len(refs)}个旁白／内心声镜头均停留在中景或更宽范围；逐镜核对空间／身体任务及遗漏的情绪细节。'
        else:
            reason = f"{finding['task']}任务“{finding['focus']}”：核对该取景能否实际读清所写信息。"
        print(f"REVIEW [{finding['code']}]: {locations}；{reason}")
    print('REVIEW: 按实际画面修订，或把逐项保留依据记入既有 delivery_review.visual_continuity；程序不证明可读性。')
