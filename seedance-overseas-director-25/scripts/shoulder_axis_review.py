"""Locate explicit OTS shoulder/foreground inconsistencies for content review.

Existing prose is evidence, not computed geometry. No text is rewritten and no
absence of findings proves an axis valid. Moving, reflected and unusual views
need the same manual spatial review as unrecognised wording.
"""
import re

from camera_views import canonical_text, endpoint_view


def shoulder_side(position, character):
    match = re.search(re.escape(character) + r'\s*(?:的\s*)?([左右])(?:侧)?肩(?:背)?后', position)
    return match.group(1) if match else None


def foreground_side(placement):
    matches = re.findall(r'(?:画面|画)?([左右])(?:侧|下角|上角)?(?:的)?前景|前景(?:画面|画)?([左右])侧?', placement)
    sides = {a or b for a, b in matches}
    return next(iter(sides)) if len(sides) == 1 else None


def setup(shot, endpoint):
    view = endpoint_view(shot, endpoint)
    if not isinstance(view, dict) or not isinstance(view.get('subject'), str):
        return None
    foreground = view.get('foreground', [])
    if len(foreground) != 1 or not isinstance(foreground[0], str) or foreground[0] == view['subject']:
        return None
    relation = shot.get('relation', {})
    if relation.get('mode') != 'same_scene':
        return None
    name = foreground[0]
    side = shoulder_side(view.get('position', ''), name)
    if not side:
        return None
    return dict(view=view, character=name, subject=view['subject'], shoulder=side)


def location(segment, shot):
    return dict(segment_id=segment['id'], start=shot['start'], end=shot['end'])


def review_shoulder_axis(plan):
    findings = []
    for segment in plan['segments']:
        for shot in segment['shots']:
            # A single placement sentence does not describe both endpoints of a
            # moving view. Such shots remain an explicit manual-review duty.
            first, last = setup(shot, 'start'), setup(shot, 'end')
            if not first or not last or first['view'] != last['view']:
                continue
            for placement in shot.get('relation', {}).get('placements', []):
                if placement.get('character') != first['character'] or placement.get('visibility') != 'visible':
                    continue
                text = placement.get('placement', '')
                screen = foreground_side(text)
                body_sides = set(re.findall(r'([左右])(?:侧)?肩(?:背)?', text))
                reasons = []
                if screen == first['shoulder']:
                    reasons.append(f"{first['character']}{first['shoulder']}肩后与画{screen}前景同侧")
                if body_sides and body_sides != {first['shoulder']}:
                    reasons.append('机位肩侧与位置句所写肩侧不同')
                if reasons:
                    findings.append(dict(code='shoulder_axis', kind='shoulder_projection',
                        shots=[location(segment, shot)], reasons=reasons,
                        evidence=[first['view']['position'], first['character'] + text]))
    segments = {segment['id']: segment for segment in plan['segments']}
    clips = plan.get('playback')
    if clips is None:
        clips = [dict(segment_id=s['id'], start=0, end=s['shots'][-1]['end'])
                 for s in plan['segments'] if s['shots']]
    previous = None
    for clip in clips:
        segment = segments.get(clip['segment_id'])
        if segment is None:
            previous = None
            continue
        for shot in segment['shots']:
            if shot['start'] < clip['start'] - 1e-7 or shot['end'] > clip['end'] + 1e-7:
                continue
            current = setup(shot, 'start')
            if previous and current:
                old_segment, old_shot, old = previous
                if (old and old_segment['episode'] == segment['episode']
                        and canonical_text(old['view']['space']) == canonical_text(current['view']['space'])
                        and old['subject'] == current['character'] and old['character'] == current['subject']
                        and old['shoulder'] == current['shoulder']):
                    findings.append(dict(code='shoulder_axis', kind='same_shoulder_reverse',
                        shots=[location(old_segment, old_shot), location(segment, shot)],
                        reasons=[f"相邻反打都从前景人物{current['shoulder']}肩后拍摄"],
                        evidence=[old['view']['position'], current['view']['position']]))
            previous = (segment, shot, setup(shot, 'end'))
    return findings
