"""Locate similar cuts, repeated setups and sustained shared dialogue framing.

Candidates require source-based review. A/B/A reactions are not automatically
defects. No camera quotas, inferred new actions, timing changes or auto-approval.
"""
from collections import defaultdict
from difflib import SequenceMatcher
from camera_views import endpoint_view, physical_key, canonical_text, view_error
from cut_boundary_checks import playback_shots
from continuation_boundary import previous_tails


def snapshot(segment, shot):
    return dict(segment_id=segment['id'], start=shot['start'], end=shot['end'])


def view_key(view):
    return physical_key(view) + (view['subject'], tuple(view['foreground']), canonical_text(view['direction']))


def review_shot_sequence(plan):
    findings, repeated, previous = [], defaultdict(list), None
    tails = previous_tails(plan, [], required=False)
    seen_episodes, previous_context = set(), False
    for segment, shot in playback_shots(plan):
        if segment['episode'] not in seen_episodes:
            seen_episodes.add(segment['episode'])
            tail = tails.get(segment['episode'])
            if tail:
                previous = ({'id': tail['segment_id'], 'episode': segment['episode']}, tail['shot'])
                previous_context = True
        start, end = endpoint_view(shot, 'start'), endpoint_view(shot, 'end')
        if any(view_error(v, shot.get('visible_characters', [])) for v in (start, end)):
            previous = None
            continue
        item = snapshot(segment, shot)
        if previous and previous[0]['episode'] == segment['episode']:
            before_segment, before = previous
            left = endpoint_view(before, 'end')
            if (canonical_text(left['space']) == canonical_text(start['space'])
                    and left['subject'] is not None and left['subject'] == start['subject']
                    and left['foreground'] == start['foreground'] and 'jump_cut_before' not in shot):
                a, b = physical_key(left), physical_key(start)
                similar = (a == b or before.get('end_camera') == shot.get('start_camera')
                           or SequenceMatcher(None, a[1], b[1]).ratio() >= .72)
                if similar:
                    finding = dict(code='shot_sequence', kind='similar_cut',
                        shots=[item] if previous_context else [snapshot(before_segment, before), item],
                        reason='同主体相似机位切点：对照实际观察面、前景遮挡和信息变化；改词、微移或裁切不能证明有效换镜。')
                    if previous_context:
                        finding['previous_tail'] = snapshot(before_segment, before)
                        finding['reason'] = ('前段已绑定末镜' + before_segment['id'] + '@' + str(before['start'])
                                             + '→本批首镜；' + finding['reason'])
                    findings.append(finding)
        previous = segment, shot
        previous_context = False
        key = (segment['id'], view_key(start), view_key(end), shot.get('start_scale'), shot.get('end_scale'))
        repeated[key].append(item)
        voices = [v for v in segment.get('speech', []) if v.get('kind') == 'dialogue'
                  and min(v['end'], shot['end']) > max(v['start'], shot['start'])]
        active = set(shot.get('visible_characters', [])) - set(start['foreground'])
        if (voices and len(active) >= 2 and shot.get('relation', {}).get('mode') == 'same_scene'
                and shot.get('start_scale') in ('中景', '中近景', '近景')
                and (shot['end'] - shot['start'] >= 3 or len(voices) > 1)):
            findings.append(dict(code='shot_sequence', kind='shared_dialogue', shots=[item],
                reason='双方同时作为可见对象承载对白：核对共同观看任务何时结束、关键情绪是否应转为单主体；肩背关系参照不算双人主拍。'))
    for shots in repeated.values():
        if len(shots) >= 2:
            findings.append(dict(code='shot_sequence', kind='returning_view', shots=shots,
                reason='本段重复返回同一取景：逐次说明原文触发的新信息、表演阶段或必要接收；正常正反打可保留，无进展的重复不得以换措辞放行。'))
    return findings
