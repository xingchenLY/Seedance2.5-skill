"""Catch a few explicit contradictions in authored shot prose.

This is deliberately narrow: no inferred mouth visibility, image geometry,
keyword rewriting, deletion of source speech, or claim of semantic validation.
"""
import re

EPS = 1e-7


def check_explicit_contradictions(segment, shot, camera, visual, errors):
    label = f'{segment["id"]}/{shot.get("frame_id", shot["start"])}'
    combined = camera + visual
    back = re.search(r'手机(?:背板|背面)(?:始终|仍)?朝(?:向)?(?:摄影机|镜头)', camera)
    display = re.search(r'屏幕(?:上)?(?:清楚|清晰)?(?:地)?(?:显示|呈现|可读)', visual)
    # A mirror, a second phone or a visible turn may legitimately reveal a screen.
    changed = re.search(r'反射|镜子|倒影|另一|第二|两部|翻转|转动|旋转|绕到|转向摄影机|转向镜头', combined)
    if back and display and '固定' in camera and not changed:
        if not re.search(r'(?:不|未|无法|不能|没有)[^。；;，,\n]{0,8}屏幕', visual):
            errors.append(f'{label}: fixed back-facing phone contradicts a visible screen claim; review framing')
    for voice in segment.get('speech', []):
        speaker = voice['speaker']
        if (voice['kind'] != 'dialogue' or speaker not in shot.get('visible_characters', [])
                or voice['end'] <= shot['start'] + EPS or voice['start'] >= shot['end'] - EPS):
            continue
        others = set(shot.get('visible_characters', [])) - {speaker}
        for sentence in re.split(r'[。；;]', visual):
            if (speaker in sentence and not any(name in sentence for name in others)
                    and re.search(r'(?:全程|整镜)(?:始终|保持|都)?闭口', sentence)
                    and not re.search(r'不(?:是|会|再)?.{0,3}(?:全程|整镜)', sentence)):
                errors.append(f'{label}: on-screen dialogue contradicts a whole-shot closed mouth for {speaker}')
                break
