"""Render authored director records without inventing narrative or marking manual review complete."""
import argparse
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from delivery_checks import EPS, PREFIX_FIELDS, TIME_RANGE, frame_groups, validate_delivery
from validate_plan import validate, speech_english_words
from relation_layouts import derive_scene_cast, check_scene_positions, relation_layout_prose
from camera_views import camera_view_prose
from speech_continuity import speech_context, speech_carry_prose
from speech_output_guard import CROSS_SHOT_CUE, crosses_shots
from narration_mouth import isolation_prose, voice_display_speaker
from narration_editing import internal_cut_prose
from dialogue_reactions import check_dialogue_coverage, reception_prose
from action_continuity import action_context, action_prose
from cut_transitions import jump_cut_prose
from shot_pacing import static_span_prose
from closeup_pacing import closeup_prose
from sound_timing import frame_sound_prose
from segment_numbering import segment_labels
from mode_rules import presentation_defaults, voice_reference, voice_category, narration_visual_prose, require_mode
from prop_inventory import derive_prop_field

DEFAULTS = {
    '画面规格': 'AI 真人写实竖屏短剧，9:16构图，8K分辨率。',
    '声音限制': '全程无背景音乐；仅使用环境音、动作音效、转场音效及人物自然声音。',
    '画面限制': '全画面可见文字按本次已确认的目标语种呈现，剧情必要文字清晰可读；不添加非剧情所需的字幕、装饰文字、水印、LOGO、悬浮 UI 或片头片尾文字。',
}
VOICE_LABELS = {'dialogue': '台词参考', 'VO': 'VO 参考', 'OS': 'OS 参考', 'narration': '旁白参考', 'inner': '旁白参考'}


def line(value, label):
    if not isinstance(value, str) or not value.strip() or '\n' in value or '\r' in value:
        raise ValueError(f'{label}: expected nonempty single-line text')
    return value.strip()


def fmt(value):
    result = format(Decimal(str(value)), 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def unique_parts(*parts):
    # Only eliminate an identical whole fragment already present. No semantic
    # rewrite, sentence deletion, speech deduplication or loss of bound evidence.
    result = ''
    for part in parts:
        if part and part not in result:
            result += part
    return result


def prepare_plan(authored):
    from narrative_budget import check_narrative_budgets
    budget_errors = check_narrative_budgets(authored)
    if budget_errors:
        raise ValueError('\n'.join(budget_errors))
    plan = deepcopy(authored)
    # Any render invalidates earlier manual review; the user/model reviews the new final text.
    plan.pop('delivery_review', None)
    sources = {s['id']: s for s in plan['sources']}
    errors = []
    for segment in plan['segments']:
        derive_prop_field(segment, errors)
        shots = segment['shots']
        if not shots:
            raise ValueError(f'{segment["id"]}: no shots')
        segment['duration'] = shots[-1]['end']
        segment['cast'] = derive_scene_cast(segment)
        check_scene_positions(segment, segment.get('presentation', {}).get('人物位置关系'),
                              segment['id'], errors)
        segment['english_words'] = sum(speech_english_words(v, sources[v['source_id']]['language'],
                                       f'{segment["id"]}/{v["source_id"]}', errors) for v in segment['speech'])
        if 'mode' in plan:
            from chinese_timing import chinese_speech_seconds
            import math
            for voice in segment['speech']:
                seconds = chinese_speech_seconds(voice, sources[voice['source_id']]['language'],
                                                f'{segment["id"]}/{voice["source_id"]}', errors)
                if seconds is not None and 'estimated_seconds' not in voice:
                    voice['estimated_seconds'] = math.ceil((seconds - EPS) * 10) / 10
        for shot in shots:
            if isinstance(shot.get('relation'), dict) and 'evidence' not in shot['relation']:
                shot['relation']['evidence'] = (relation_layout_prose(shot)
                                                or line(shot['visual_text'], 'visual_text'))
            start_view = shot.get('view')
            end_view = shot.get('end_view', start_view if shot['start_camera'] == shot['end_camera'] else None)
            for endpoint, view in [('start', start_view), ('end', end_view)]:
                if isinstance(view, dict) and isinstance(view.get('subject'), str):
                    shot.setdefault(endpoint + '_subject', view['subject'])
    errors.extend(validate(plan))
    if errors:
        raise ValueError('\n'.join(errors))
    return plan


def render_plan(authored):
    """Return derived plan + text. All supplied prose must already reflect the screenplay."""
    plan = prepare_plan(authored)
    voice_context = speech_context(plan)
    action_errors = []
    actions = action_context(plan, action_errors, required=True)
    reactions = check_dialogue_coverage(plan, action_errors)
    if action_errors:
        raise ValueError("\n".join(action_errors))
    sections = []
    segment_numbers = segment_labels(plan)
    for episode in plan['episodes']:
        segments = [s for s in plan['segments'] if s['episode'] == episode['id']]
        title = episode.get('title')
        if title:
            title = line(title, 'episode.title')
            if not re.fullmatch(r'第\s*\S+?\s*集', title):
                raise ValueError('episode.title: use the original 第 X 集 title')
            sections.append(f'{title}｜共 {len(segments)} 段｜总预估时长 {fmt(sum(s["duration"] for s in segments))} 秒')
        elif len(plan['episodes']) > 1:
            raise ValueError('Multiple episodes need their original titles; do not invent episode numbers')
        elif episode['complete']:
            sections.append(f'共 {len(segments)} 段｜总预估时长 {fmt(sum(s["duration"] for s in segments))} 秒')
        for segment in segments:
            number = segment_numbers[segment['id']]
            sections.append(f'第 {number} 段｜预估时长 {fmt(segment["duration"])} 秒｜发声词数 {segment["english_words"]}')
            fields = {**DEFAULTS, **presentation_defaults(plan), **segment['presentation']}
            extra = set(fields) - set(PREFIX_FIELDS)
            if extra:
                raise ValueError(f'Unexpected presentation fields: {sorted(extra)}')
            fields['出镜人物'] = '、'.join(segment['cast']) or '无'
            rows = []
            for key in PREFIX_FIELDS:
                if key == '特殊道具' and not fields.get(key):
                    continue
                rows.append(f'{key}：{line(fields[key], key)}')
            sections.append('\n'.join(rows))
            groups = frame_groups(segment)
            notes = segment.get('frame_notes', {})
            if not isinstance(notes, dict) or set(notes) - {g['id'] for g in groups}:
                raise ValueError('frame_notes must refer to existing frame IDs')
            for gi, group in enumerate(groups):
                fid = group['id']
                rows = [f'{fid}（{fmt(group["start"])}–{fmt(group["end"])} 秒）']
                cameras, visuals = [], []
                for shot in (s for s in segment['shots'] if s['frame_id'] == fid):
                    camera = line(shot['camera_text'], 'camera_text')
                    camera = unique_parts(camera_view_prose(shot), jump_cut_prose(shot), camera, closeup_prose(shot))
                    if cameras:
                        camera = internal_cut_prose(shot) + camera
                    visual = unique_parts(line(shot['visual_text'], 'visual_text'), action_prose(segment, shot, actions),
                                          relation_layout_prose(shot), speech_carry_prose(segment, shot, voice_context),
                                          isolation_prose(segment, shot), static_span_prose(shot),
                                          narration_visual_prose(shot), reception_prose(segment, shot, reactions))
                    if TIME_RANGE.search(camera) or TIME_RANGE.search(visual):
                        raise ValueError('Shot text must not duplicate interval labels; use 第 X 秒 for action cues')
                    scale = shot['start_scale']
                    if scale != shot['end_scale']:
                        scale += '连续变为' + shot['end_scale']
                    span = f'{fmt(shot["start"])}–{fmt(shot["end"])} 秒 '
                    cameras.append(span + scale + '，' + camera)
                    visuals.append(span + visual)
                rows += ['运镜：' + '；'.join(cameras), '画面内容：' + '；'.join(visuals)]
                for voice in segment['speech']:
                    if group['start'] - EPS <= voice['start'] < group['end'] - EPS:
                        name = line(voice_display_speaker(voice), 'speaker')
                        reference = line(voice_reference(voice), 'voice_reference')
                        performance = voice_category(voice['kind']) + line(voice['performance'], 'performance')
                        if TIME_RANGE.search(performance) or '“' in performance or '”' in performance:
                            raise ValueError('performance: keep intervals and quoted speech in structured fields')
                        if crosses_shots(segment, voice) and CROSS_SHOT_CUE not in performance:
                            performance += '；' + CROSS_SHOT_CUE
                        words = ' '.join(voice['text'].split())
                        rows.append(f'（{name}）{VOICE_LABELS[voice["kind"]]}（{reference}）音色：'
                                    f'（{performance}，{fmt(voice["start"])}–{fmt(voice["end"])} 秒）“{words}”')
                note = notes.get(fid, {})
                if not isinstance(note, dict) or set(note) - {'sound_effects', 'transition'}:
                    raise ValueError(f'{fid}: unsupported frame_notes fields')
                effect = frame_sound_prose(segment, group)
                if 'sound_effects' in note:
                    raise ValueError('Use segment.sound_events for effects; frame_notes.sound_effects is no longer accepted')
                if effect:
                    rows.append('音效：' + effect)
                for key, label in [('transition', '转场方式')]:
                    if key in note:
                        if key == 'transition' and gi == len(groups)-1:
                            raise ValueError('Last frame must omit outgoing transition; internal cuts belong in camera_text')
                        rows.append(f'{label}：{line(note[key], key)}')
                sections.append('\n'.join(rows))
        if 'playback' in plan:
            by_id = {s['id']: s for s in segments}
            clips = [c for c in plan['playback'] if c['segment_id'] in by_id]
            sections.append('剪辑顺序：' + ' → '.join(
                f'第 {segment_numbers[c["segment_id"]]} 段 {fmt(c["start"])}–{fmt(c["end"])} 秒' for c in clips))
    text = '\n\n'.join(sections) + '\n'
    errors = validate_delivery(plan, text, require_review=False)
    if errors:
        raise ValueError('\n'.join(errors))
    return plan, text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plan-output', type=Path, required=True)
    parser.add_argument('--rhythm-output', type=Path, help='Optional internal shot overview; never included in the prompt')
    args = parser.parse_args()
    try:
        paths = [args.plan, args.output, args.plan_output]
        if args.rhythm_output is not None:
            paths.append(args.rhythm_output)
        if len({p.resolve() for p in paths}) != len(paths):
            raise ValueError('Input, rendered prompt, derived plan and optional rhythm overview must use different paths')
        authored = json.loads(args.plan.read_text(encoding='utf-8-sig'))
        require_mode(authored)
        from narrative_budget import check_narrative_budgets
        budget_errors = check_narrative_budgets(authored, required=True)
        if budget_errors:
            raise ValueError('\n'.join(budget_errors))
        from language_policy import require_content_language
        require_content_language(authored)
        plan, text = render_plan(authored)
        if args.rhythm_output is not None:
            from rhythm_overview import render_rhythm_overview
            rhythm = render_rhythm_overview(plan)
        with args.output.open('w', encoding='utf-8', newline='\n') as prompt_file:
            prompt_file.write(text)
        args.plan_output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if args.rhythm_output is not None:
            args.rhythm_output.write_text(rhythm, encoding='utf-8')
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f'RENDER FAILED: {exc}\n')
    print('RENDERED: deterministic checks passed; manual review and --final-prompt validation still required.')
    print('prompt_sha256=' + hashlib.sha256(text.encode('utf-8')).hexdigest())
    from review_closure import plan_digest
    print('plan_sha256=' + plan_digest(plan))
    from review_report import print_review_report
    print_review_report(plan)


if __name__ == '__main__':
    main()
