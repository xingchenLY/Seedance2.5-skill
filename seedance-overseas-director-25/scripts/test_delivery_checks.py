from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from validate_plan import validate, source_parts_match
from delivery_checks import SCALES, validate_delivery
from test_validate_plan import sample
from camera_views import camera_view_prose
from relation_layouts import relation_layout_prose
from speech_continuity import speech_context, speech_carry_prose
from speech_output_guard import CROSS_SHOT_CUE, crosses_shots
import test_validate_plan as legacy


def delivery_sample():
    plan = sample()
    plan['action_tracks'] = []
    segment = plan['segments'][0]
    segment['cast'] = ['Mara', 'Elias']
    segment['props'] = []  # This minimal synthetic fixture has no specified prop.
    for i, shot in enumerate(segment['shots']):
        shot.update(static_spans=[], frame_id=f'分镜{i+1}', start_scale='中景', end_scale='中景',
                    start_camera=f'camera-{i}', end_camera=f'camera-{i}',
                    visible_characters=['Mara', 'Elias'])
        shot['relation'] = dict(mode='same_scene', characters=['Mara', 'Elias'], evidence=[
            'Mara 看向 Elias，前景保留他的肩背。',
            'Elias 沉默，前景保留 Mara 肩背。'][i])
    text = '''第 1 段｜预估时长 4 秒｜发声词数 3

整体风格：真人写实，各镜头固定。
对话氛围：Mara 低声说话，Elias 闭口听着。
画面规格：AI 真人写实竖屏短剧，8K 分辨率，人物、道具及接触位置清楚可见，前景、主体与背景位置明确。
声音限制：全程无背景音乐；仅使用环境音、动作音效、转场音效及人物自然声音。
画面限制：全画面任何位置均不得出现简体或繁体中文，包括背景、屏幕及道具文字；剧情必要文字清晰可读，默认英文；不添加非剧情所需的字幕、装饰文字、水印、LOGO、悬浮 UI 或片头片尾文字。
场景：夜，室内。
环境光影：右侧窗光。
出镜人物：Mara、Elias
人物位置关系：两人面对面站立。

分镜1（0–2 秒）
运镜：Mara 中景过肩，固定。
画面内容：Mara 看向 Elias，前景保留他的肩背。
（Mara）台词参考（Mara）音色：（低声，咬字清楚，0.2–1.7 秒）“Put it down.”
转场方式：硬切。

分镜2（2–4 秒）
运镜：反向中景过肩，固定。
画面内容：Elias 沉默，前景保留 Mara 肩背。
'''
    for i, shot in enumerate(segment['shots']):
        shot['view'] = dict(space='大厅', position=['南端东侧 Elias 右肩后', '北端东侧 Mara 左肩后'][i],
                            direction=['Mara', 'Elias'][i], height='人物眼高',
                            subject=['Mara', 'Elias'][i], foreground=[['Elias'], ['Mara']][i],
                            background=['北窗', '南墙'][i])
        original = ['运镜：Mara 中景过肩，固定。', '运镜：反向中景过肩，固定。'][i]
        text = text.replace(original, original + camera_view_prose(shot))
    for i, shot in enumerate(segment['shots']):
        shot['relation']['scene_characters'] = ['Mara', 'Elias']
        descriptions = ({'Mara': '在画面右后方直视 Elias', 'Elias': '以肩背留在左前景，朝向 Mara'}
                        if i == 0 else
                        {'Elias': '在画面左后方直视 Mara', 'Mara': '以肩背留在右前景，朝向 Elias'})
        shot['relation']['placements'] = [dict(character=n, visibility='visible', placement=descriptions[n])
                                           for n in ['Mara', 'Elias']]
        evidence = shot['relation']['evidence']
        text = text.replace('画面内容：' + evidence, '画面内容：' + evidence + relation_layout_prose(shot))
    review(plan, text)
    return plan, text


def review(plan, text):
    # Synthetic test receipts only. Production code must never auto-certify a review.
    import re
    anchors = re.findall(r'^环境光影：(.*)$', text, re.M)
    plan['delivery_review'] = dict(prompt_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(),
        source_coverage='L1 全文对应；人物与场景来自本次节选。',
        timing='L1 预估 1.5 秒；第二镜只保留当前沉默反应。',
        visual_continuity='两镜关系位与窗光承接，原文无出入场。', conflicts='本片段无重叠声音或服装线索冲突。',
        lighting=[dict(segment_id=s['id'], anchor=anchors[i] if i < len(anchors) else '',
                       basis='合成测试输入中的窗光设定，非场景图片验证。',
                       checked_shots=[shot['start'] for shot in s['shots']],
                       continuity='合成测试沿既定窗光核对全部镜头，未设计动态光效。', changes=[])
                  for i, s in enumerate(plan['segments'])])
    from timing_overlap_evidence import required_overlap_gaps
    by_id = {s['id']: s for s in plan['segments']}
    # Fabricated fixtures for validator plumbing, never a production review.
    plan['delivery_review']['timing_overlap'] = []
    for gap in required_overlap_gaps(plan):
        shot = next(s for s in by_id[gap['segment_id']]['shots'] if s['start'] == gap['shot_starts'][0])
        evidence = shot.get('visual_text') or shot.get('relation', {}).get('evidence', '')
        plan['delivery_review']['timing_overlap'].append(dict(
            segment_id=gap['segment_id'], start=gap['start'], end=gap['end'],
            shot_start=shot['start'], task_evidence=evidence,
            comparisons=[dict(segment_id=v['segment_id'], speech_index=v['speech_index'],
                constraint='source_order', basis='合成校验用既定先后，非真实原文内容审阅。')
                         for v in gap['speech_candidates']]))
    # Synthetic receipts for validator tests ONLY. Production must independently
    # read source material and judge each candidate; never import this helper.
    from review_closure import plan_digest
    from review_report import collect_review_report
    from relation_layouts import scene_cast
    from prop_inventory import prop_declaration
    plan['delivery_review']['plan_sha256'] = plan_digest(plan)
    plan['delivery_review']['findings'] = []
    for item in collect_review_report(plan)['items']:
        evidence = []
        for sid, start in item['locations']:
            shot = next(s for s in by_id[sid]['shots'] if s['start'] == start)
            value = shot.get('visual_text') or shot.get('relation', {}).get('evidence', '')
            evidence.append(dict(segment_id=sid, shot_start=start, text=value))
        plan['delivery_review']['findings'].append(dict(key=item['key'], decision='retain',
            basis='合成输入保留本镜预定任务，仅测试绑定，不代表真实导演复核。', evidence=evidence))
    plan['delivery_review']['source_audit'] = [dict(segment_id=s['id'],
        source_ref='合成测试输入；仅验证结构，不冒充用户原文',
        source_excerpt='合成两人关系与已声明物品；非真实原始剧本节选。',
        characters=scene_cast(s), props=prop_declaration(s, [])[1] or '无', key_information=[],
        coverage_basis='本合成用例的视觉信息由专门回归另测，此处仅测试源文复核记录绑定。')
        for s in plan['segments']]
    plan['delivery_review']['editorial_review'] = dict(
        method='separate_pass', reviewer_ref='合成维护测试的记录绑定样本，非真实审稿',
        fallback_basis='仅测试程序的记录校验；不调用真实制作审阅者，也不作为生产放行记录。',
        plan_sha256=plan_digest(plan), prompt_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(),
        reviewed_segments=[s['id'] for s in plan['segments']],
        finding_keys=[i['key'] for i in collect_review_report(plan)['items']],
        scan_basis='本合成用例只验证成稿审阅记录的版本与范围绑定，不代表语义已审阅。', issues=[])


def two_speaker_delivery(merged=True, kind='dialogue', speaker='Elias', overlap=False):
    plan, text = delivery_sample()
    segment = plan['segments'][0]
    start, end = (1.2, 2.2) if overlap else (2.1, 3.1)
    plan['sources'][0]['kind'] = kind
    segment['speech'][0]['kind'] = kind
    plan['sources'].append(dict(id='L2', episode='current', speaker=speaker,
                                kind=kind, language='en', text='Wait.'))
    segment['speech'].append(dict(source_id='L2', speaker=speaker, kind=kind,
                                  start=start, end=end, estimated_seconds=1, text='Wait.'))
    segment['english_words'] = 4
    if overlap:
        segment['overlap_basis'] = '原文 L2 抢话，与 L1 句尾同时发生。'
    label = {'dialogue': '台词参考', 'VO': 'VO 参考', 'OS': 'OS 参考'}[kind]
    text = text.replace('发声词数 3', '发声词数 4').replace('台词参考（Mara）', label + '（Mara）')
    old = segment['shots'][1]['relation']['evidence']
    new = 'Elias 看向 Mara，前景保留 Mara 肩背。'
    segment['shots'][1]['relation']['evidence'] = new
    text = text.replace(old, new)
    carry = '；' + CROSS_SHOT_CUE if crosses_shots(segment, segment['speech'][-1]) else ''
    response = f'（{speaker}）{label}（{speaker}）音色：（急促{carry}，{start}–{end} 秒）“Wait.”'
    if merged:
        segment['shots'][1]['frame_id'] = '分镜1'
        prefix = text.split('分镜1（')[0]
        text = (prefix + '分镜1（0–4 秒）\n'
                '运镜：0–2 秒 Mara 中景过肩固定，' + camera_view_prose(segment['shots'][0]) +
                '；2–4 秒硬切反向中景过肩固定，' + camera_view_prose(segment['shots'][1]) + '\n' +
                '画面内容：0–2 秒 Mara 看向 Elias，前景保留他的肩背。' + relation_layout_prose(segment['shots'][0]) +
                '2–4 秒 ' + new + relation_layout_prose(segment['shots'][1]) + '\n'
                f'（Mara）{label}（Mara）音色：（低声，咬字清楚，0.2–1.7 秒）“Put it down.”\n' + response + '\n')
    else:
        body = '画面内容：' + new + relation_layout_prose(segment['shots'][1])
        text = text.replace(body, body + '\n' + response)
    context = speech_context(plan)
    for shot in segment['shots']:
        layout = relation_layout_prose(shot)
        text = text.replace(layout, layout + speech_carry_prose(segment, shot, context))
    review(plan, text)
    return plan, text


def two_segment_delivery():
    plan, text = delivery_sample()
    second = deepcopy(plan['segments'][0])
    second.update(id='S2', speech=[], english_words=0)
    for i, shot in enumerate(second['shots']):
        shot.update(start_camera=f'S2-camera-{i}', end_camera=f'S2-camera-{i}')
    plan['segments'].append(second)
    silent = text.replace('第 1 段', '第 2 段').replace('发声词数 3', '发声词数 0')
    silent = '\n'.join(line for line in silent.splitlines() if '台词参考（' not in line)
    text += '\n' + silent + '\n'
    review(plan, text)
    return plan, text


def visual_flashback_plan():
    plan = legacy.PlanTests().flashback_plan()
    for segment in plan['segments']:
        segment['cast'] = ['Mara', 'Elias']
        scale = '中近景' if segment['id'] == 'S1' else '面部特写'
        for i, shot in enumerate(segment['shots']):
            shot.update(frame_id=f'分镜{i+1}', start_scale=scale, end_scale=scale,
                        start_camera=f'{segment["id"]}-{i}', end_camera=f'{segment["id"]}-{i}',
                        visible_characters=['Mara', 'Elias'])
    return plan


class DeliveryTests(unittest.TestCase):
    def assert_finding(self, findings, fragment):
        self.assertTrue(any(fragment in message for message in findings), findings)

    def test_latin_word_boundaries(self):
        for text, parts in [('Nobody leaves.', ['No', 'body leaves.']),
                            ("Don't go.", ['Don', "'t go."]),
                            ('Well-known.', ['Well-', 'known.']),
                            ('先说 Stop。', ['先说 St', 'op。'])]:
            with self.subTest(text=text):
                self.assertFalse(source_parts_match(text, parts))
        self.assertTrue(source_parts_match('Nobody leaves.', ['Nobody', 'leaves.']))
        self.assertTrue(source_parts_match('Wait!', ['Wait', '!']))

    def test_spoken_form_cannot_hide_word_limit(self):
        plan = sample()
        text = ' '.join(['word'] * 97)
        plan['sources'][0]['text'] = text
        voice = plan['segments'][0]['speech'][0]
        voice.update(text=text, spoken_form='Okay.')
        plan['segments'][0]['english_words'] = 1
        findings = validate(plan)
        self.assert_finding(findings, 'changes nonnumeric')
        self.assert_finding(findings, 'word count exceeds')

    def test_number_mapping_preserves_surrounding_text(self):
        plan = sample()
        plan['sources'][0]['text'] = 'Pay $20.'
        voice = plan['segments'][0]['speech'][0]
        voice.update(text='Pay $20.', number_expansions=[dict(start=4, end=7, spoken='twenty dollars')])
        self.assertEqual(validate(plan), [])
        voice['spoken_form'] = 'Keep twenty dollars.'
        self.assert_finding(validate(plan), 'differs from number-only')
        voice['number_expansions'][0]['start'] = 0
        self.assert_finding(validate(plan), 'numeric expressions only')

    def test_invalid_numeric_range_is_rejected(self):
        plan = sample()
        plan['sources'][0]['text'] = 'Pay $20.'
        voice = plan['segments'][0]['speech'][0]
        voice.update(text='Pay $20.', number_expansions=[dict(start=4, end=90, spoken='twenty dollars')])
        self.assert_finding(validate(plan), 'invalid number expansion')

    def test_mixed_english_cannot_be_zero(self):
        plan = sample()
        english = ' '.join(['word'] * 97)
        text = '请说 ' + english + '。'
        plan['sources'][0].update(text=text, language='zh')
        voice = plan['segments'][0]['speech'][0]
        voice['text'] = text
        plan['segments'][0]['english_words'] = 0
        self.assert_finding(validate(plan), 'requires explicit language_parts')
        voice['language_parts'] = [dict(language='zh', text='请说'), dict(language='en', text=english), dict(language='zh', text='。')]
        self.assert_finding(validate(plan), 'word count exceeds')

    def test_mixed_language_integrity_and_mul(self):
        plan = sample()
        plan['sources'][0].update(language='mul', text='快走 Stop!')
        voice = plan['segments'][0]['speech'][0]
        voice.update(text='快走 Stop!', language_parts=[dict(language='zh', text='快走'), dict(language='en', text='Stop!')])
        plan['segments'][0]['english_words'] = 1
        self.assertEqual(validate(plan), [])
        voice['language_parts'][1]['text'] = 'Go!'
        self.assert_finding(validate(plan), 'do not reconstruct')

    def test_extreme_estimate_requires_measured_review(self):
        plan = sample()
        voice = plan['segments'][0]['speech'][0]
        voice.update(start=0, end=0.1, estimated_seconds=0.1)
        self.assert_finding(validate(plan), 'requires timing_review')
        voice['timing_review'] = dict(basis='guess', source='none', measurement='none', seconds=0.1)
        self.assert_finding(validate(plan), 'requires measured audio')

    def test_review_trigger_is_not_the_3_4_rate(self):
        plan = sample()
        plan['segments'][0]['speech'][0].update(start=0, end=0.6, estimated_seconds=0.6)
        self.assertEqual(validate(plan), [])

    def test_voice_kinds_per_continuous_shot(self):
        plan = legacy.PlanTests().two_lines()
        plan['sources'][1]['kind'] = 'OS'
        plan['segments'][0]['speech'][1]['kind'] = 'OS'
        plan['segments'][0]['shots'] = [dict(start=0, end=4)]
        self.assert_finding(validate(plan), 'continuous shot contains multiple')

    def test_valid_delivery(self):
        plan, text = delivery_sample()
        self.assertEqual(validate(plan), [])
        self.assertEqual(validate_delivery(plan, text), [])

    def test_delivery_cannot_omit_camera_metadata(self):
        self.assert_finding(validate_delivery(sample(), delivery_sample()[1]), 'delivery shot fields missing')

    def test_delivery_requires_relation_for_each_actual_shot(self):
        plan, text = delivery_sample()
        del plan['segments'][0]['shots'][1]['relation']
        self.assertEqual(validate(plan), [])  # Draft records remain supported.
        self.assert_finding(validate_delivery(plan, text), 'per-shot relation record is required')

    def test_relation_removal_is_detected_after_rehash_and_cast_update(self):
        plan, text = delivery_sample()
        text = text.replace('人物位置关系：两人面对面站立。', '人物位置关系：两人在室内。')
        text = text.replace('Mara 中景过肩，固定。', 'Mara 中景，固定。')
        text = text.replace('反向中景过肩，固定。', 'Elias 中景，固定。')
        text = text.replace('Mara 看向 Elias，前景保留他的肩背。', 'Mara 神情严肃。')
        text = text.replace('Elias 沉默，前景保留 Mara 肩背。', 'Elias 沉默。')
        plan['segments'][0]['shots'][0]['visible_characters'] = ['Mara']
        plan['segments'][0]['shots'][1]['visible_characters'] = ['Elias']
        review(plan, text)
        self.assert_finding(validate_delivery(plan, text), 'relation evidence is absent')

    def test_relation_evidence_cannot_exist_only_in_segment_header(self):
        plan, text = delivery_sample()
        evidence = plan['segments'][0]['shots'][0]['relation']['evidence']
        text = text.replace('人物位置关系：两人面对面站立。', '人物位置关系：' + evidence)
        text = text.replace('画面内容：' + evidence, '画面内容：Mara 神情严肃。')
        review(plan, text)
        self.assert_finding(validate_delivery(plan, text), 'relation evidence is absent')

    def test_relation_evidence_must_belong_to_its_frame(self):
        plan, text = delivery_sample()
        plan['segments'][0]['shots'][0]['relation']['evidence'] = plan['segments'][0]['shots'][1]['relation']['evidence']
        review(plan, text)
        self.assert_finding(validate_delivery(plan, text), 'relation evidence is absent')

    def test_same_frame_internal_shots_each_need_relation_evidence(self):
        plan, text = delivery_sample()
        first = plan['segments'][0]['shots'][0]
        next_shot = deepcopy(first)
        first['end'] = 1
        next_shot.update(start=1, start_camera='camera-internal', end_camera='camera-internal')
        next_shot['view']['position'] = '桌西侧 Mara 斜前方'
        next_shot['view']['background'] = '北窗和东墙交界'
        next_shot['relation']['evidence'] = 'Mara 仍面向 Elias，Elias 的侧脸在左前景承接。'
        text = text.replace('画面内容：' + first['relation']['evidence'],
                            '画面内容：0–1 秒，' + first['relation']['evidence'] + relation_layout_prose(first) +
                            '1–2 秒，' + next_shot['relation']['evidence'])
        text = text.replace('运镜：Mara 中景过肩，固定。' + camera_view_prose(first),
                            '运镜：0–1 秒 Mara 中景过肩固定，' + camera_view_prose(first) +
                            '；1–2 秒硬切另一机位 Mara 中景过肩固定，' + camera_view_prose(next_shot))
        plan['segments'][0]['shots'].insert(1, next_shot)
        text = text.replace('低声，咬字清楚，0.2', '低声，咬字清楚；' + CROSS_SHOT_CUE + '，0.2')
        evidence = next_shot['relation']['evidence']
        text = text.replace(evidence, evidence + speech_carry_prose(
            plan['segments'][0], next_shot, speech_context(plan)))
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])
        del next_shot['relation']
        self.assert_finding(validate_delivery(plan, text), 'per-shot relation record is required')

    def test_moving_shot_relation_can_continue_outside_the_frame(self):
        plan, text = delivery_sample()
        first = plan['segments'][0]['shots'][0]
        old = first['relation']['evidence']
        evidence = ('起始 Elias 的肩背在左前景，Mara 在右侧中近景；'
                    '推至近景时 Elias 肩背离框，仍在 Mara 左前方画外，Mara 的视线持续朝向 Elias。')
        first.update(start_scale='中近景', end_scale='近景')
        old_view = camera_view_prose(first)
        first['end_view'] = deepcopy(first['view'])
        first['end_view']['foreground'] = []
        old_layout = relation_layout_prose(first)
        first['relation']['placements'][1]['placement'] = '起始以肩背留在左前景，推近后移出画框，仍在 Mara 左前方画外'
        text = text.replace(old_layout, relation_layout_prose(first))
        first['relation']['evidence'] = evidence
        text = text.replace(old, evidence).replace('Mara 中景过肩，固定。', '从 Mara 中近景连续推至近景。')
        text = text.replace(old_view, camera_view_prose(first))
        review(plan, text)
        self.assertEqual(validate(plan), [])
        self.assertEqual(validate_delivery(plan, text), [])

    def test_relation_metadata_rejects_missing_people_and_invalid_shapes(self):
        for bad, message in [
                ({'mode': [], 'characters': [], 'evidence': 'x'}, 'unknown relation mode'),
                ({'mode': 'same_scene', 'characters': [['Mara']], 'evidence': 'x'}, 'unique nonempty names'),
                ({'mode': 'same_scene', 'characters': ['Mara', 'Mara'], 'evidence': 'x'}, 'unique nonempty names'),
                ({'mode': 'same_scene', 'characters': ['Mara'], 'evidence': 'Mara'}, 'at least two characters'),
                ({'mode': 'solo', 'characters': ['Mara', 'Elias'], 'evidence': 'Mara'}, 'exactly one visible'),
                ({'mode': 'empty', 'characters': [], 'evidence': 'x'}, 'empty relation cannot'),
                ({'mode': 'same_scene', 'characters': ['Mara', 'Elias'], 'evidence': ''}, 'relation evidence is required'),
                ({'mode': 'same_scene', 'characters': ['Mara', 'Elias'], 'evidence': '前景保留他的肩背。'}, 'identify all recorded')]:
            with self.subTest(bad=bad):
                plan, text = delivery_sample()
                plan['segments'][0]['shots'][0]['relation'] = bad
                self.assert_finding(validate_delivery(plan, text), message)

    def test_relation_does_not_force_everyone_into_the_frame(self):
        plan, text = delivery_sample()
        old = plan['segments'][0]['shots'][0]['relation']['evidence']
        evidence = 'Mara 留在桌左端并看向画右外的 Elias；Elias 仍在桌右端画框外，桌沿保持横向。'
        text = text.replace(old, evidence).replace('Mara 中景过肩，固定。', 'Mara 中景，固定。')
        first = plan['segments'][0]['shots'][0]
        old_view = camera_view_prose(first)
        old_layout = relation_layout_prose(first)
        first['visible_characters'] = ['Mara']
        first['relation']['placements'] = [
            dict(character='Mara', visibility='visible', placement='在桌左端，朝画右外的 Elias 说话'),
            dict(character='Elias', visibility='offscreen', placement='在桌右端画框外，与 Mara 隔桌相对')]
        first['relation']['single_reason'] = '长桌距离较远，不能把对方肩背挤到主拍旁边'
        text = text.replace(old_layout, relation_layout_prose(first))
        first['view']['foreground'] = []
        text = text.replace(old_view, camera_view_prose(first))
        first['relation']['evidence'] = evidence
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])

    def test_remote_solo_and_insert_modes_do_not_require_two_person_framing(self):
        cases = [
            ('remote', ['Mara', 'Elias'], ['Mara'], 'Mara 独自在本地桌前接听；Elias 位于电话另一端，不在本地画面。'),
            ('solo', ['Mara'], ['Mara'], 'Mara 独自在室内，面对桌上文件。'),
            ('insert', ['Mara', 'Elias'], [], '只拍桌面文件，Mara 和 Elias 保持在桌两侧画框外。'),
            ('empty', [], [], '此镜为无人空走廊，室内人物均不在此场景。')]
        for mode, characters, visible, evidence in cases:
            with self.subTest(mode=mode):
                plan, text = delivery_sample()
                first = plan['segments'][0]['shots'][0]
                text = text.replace(first['relation']['evidence'], evidence)
                text = text.replace('Mara 中景过肩，固定。', '固定机位，呈现当前信息。')
                text = text.replace(relation_layout_prose(first), '')
                first['visible_characters'] = visible
                old_view = camera_view_prose({**first, 'visible_characters': ['Mara', 'Elias']})
                first['view']['foreground'] = []
                first['view']['subject'] = 'Mara' if 'Mara' in visible else None
                text = text.replace(old_view, camera_view_prose(first))
                first['relation'] = dict(mode=mode, characters=characters, evidence=evidence,
                                         scene_characters=characters if mode == 'insert' else visible)
                text = text.replace(evidence, evidence + speech_carry_prose(
                    plan['segments'][0], first, speech_context(plan)))
                review(plan, text)
                self.assertEqual(validate_delivery(plan, text), [])

    def test_relation_cross_segment_needs_evidence_in_each_segment(self):
        plan, text = two_segment_delivery()
        for shot in plan['segments'][1]['shots']:
            shot.update(start_scale='全景', end_scale='全景')
        split = text.index('第 2 段')
        tail = text[split:].replace('Mara 看向 Elias，前景保留他的肩背。', 'Mara 神情严肃。')
        changed = text[:split] + tail
        review(plan, changed)
        self.assert_finding(validate_delivery(plan, changed), 'relation evidence is absent')

    def test_cut_uses_ending_scale(self):
        plan, _ = delivery_sample()
        plan['segments'][0]['shots'][0].update(start_scale='中景', end_scale='中近景')
        plan['segments'][0]['shots'][1].update(start_scale='近景', start_camera='camera-0')
        self.assert_finding(validate(plan), 'forbidden playback cut')

    def test_four_cut_pairs_allow_camera_or_primary_character_change(self):
        pairs = [('中近景', '中景'), ('中近景', '近景'),
                 ('近景', '中近景'), ('中景', '中近景')]
        for left, right in pairs:
            for camera_changed, subject_changed in [(False, False), (True, False),
                                                     (False, True), (True, True)]:
                with self.subTest(pair=(left, right), camera=camera_changed, subject=subject_changed):
                    plan, _ = delivery_sample()
                    first, second = plan['segments'][0]['shots']
                    first.update(start_scale=left, end_scale=left,
                                 start_subject='Mara', end_subject='Mara')
                    second.update(start_scale=right, end_scale=right,
                                  start_camera='camera-1' if camera_changed else 'camera-0',
                                  start_subject='Elias' if subject_changed else 'Mara',
                                  end_subject='Elias' if subject_changed else 'Mara')
                    findings = validate(plan)
                    if camera_changed or subject_changed:
                        self.assertEqual(findings, [])
                    else:
                        self.assert_finding(findings, 'same camera without a verified primary-character change')

    def test_changed_camera_does_not_require_new_subject_fields(self):
        plan, _ = delivery_sample()
        first, second = plan['segments'][0]['shots']
        first.update(start_scale='中近景', end_scale='中近景')
        second.update(start_scale='近景', end_scale='近景')
        self.assertEqual(validate(plan), [])

    def test_visible_cast_changes_do_not_prove_primary_character_change(self):
        for cast in [['Elias', 'Mara'], ['Elias']]:
            for end_subject, start_subject in [(None, None), ('Mara', None), (None, 'Elias')]:
                with self.subTest(cast=cast, end=end_subject, start=start_subject):
                    plan, _ = delivery_sample()
                    first, second = plan['segments'][0]['shots']
                    first['end_scale'] = '中近景'
                    second.update(start_scale='近景', start_camera='camera-0', visible_characters=cast)
                    if end_subject is not None:
                        first['end_subject'] = end_subject
                    if start_subject is not None:
                        second['start_subject'] = start_subject
                    self.assert_finding(validate(plan), 'forbidden playback cut')

    def test_cut_uses_primary_character_and_camera_at_actual_endpoints(self):
        plan, _ = delivery_sample()
        first, second = plan['segments'][0]['shots']
        first.update(start_scale='中近景', end_scale='近景',
                     start_camera='other-camera', end_camera='camera-0',
                     start_subject='Mara', end_subject='Elias')
        second.update(start_scale='中近景', end_scale='中近景',
                      start_camera='camera-0', end_camera='other-camera',
                      start_subject='Elias', end_subject='Mara')
        self.assert_finding(validate(plan), 'forbidden playback cut')
        second['start_subject'] = 'Mara'
        self.assertEqual(validate(plan), [])

    def test_invalid_primary_character_metadata_is_rejected(self):
        for field in ('start_subject', 'end_subject'):
            for value in (None, '', '  ', 3, ['Mara'], {'name': 'Mara'}):
                with self.subTest(field=field, value=value):
                    plan, _ = delivery_sample()
                    plan['segments'][0]['shots'][0][field] = value
                    with self.assertRaisesRegex(ValueError, 'primary-character name'):
                        validate(plan)
            plan, _ = delivery_sample()
            plan['segments'][0]['shots'][0][field] = 'NotVisible'
            self.assert_finding(validate(plan), 'primary character is not in visible_characters')

    def test_subject_change_cannot_use_an_invisible_character(self):
        plan, _ = delivery_sample()
        first, second = plan['segments'][0]['shots']
        first.update(end_scale='中近景', end_subject='Mara')
        second.update(start_scale='近景', start_camera='camera-0', start_subject='NotVisible')
        findings = validate(plan)
        self.assert_finding(findings, 'primary character is not in visible_characters')
        self.assert_finding(findings, 'forbidden playback cut')

    def test_final_delivery_accepts_changed_primary_character(self):
        plan, text = delivery_sample()
        first, second = plan['segments'][0]['shots']
        first.update(start_scale='中近景', end_scale='中近景', end_subject='Mara')
        second.update(start_scale='近景', end_scale='近景', start_camera='camera-0',
                      end_camera='camera-0', start_subject='Elias')
        old_view = camera_view_prose(second)
        second['view']['position'] = first['view']['position']
        text = text.replace(old_view, camera_view_prose(second))
        text = text.replace('Mara 中景过肩，固定。', 'Mara 中近景过肩，机位 A，固定。')
        text = text.replace('反向中景过肩，固定。', '机位 A 切入 Elias 近景反应，固定。')
        review(plan, text)
        self.assertEqual(validate(plan), [])
        self.assertEqual(validate_delivery(plan, text), [])
        second['start_subject'] = 'Mara'
        self.assert_finding(validate_delivery(plan, text), 'primary character differs from camera view')

    def test_same_scale_same_camera(self):
        plan, _ = delivery_sample()
        plan['segments'][0]['shots'][1]['start_camera'] = 'camera-0'
        self.assert_finding(validate(plan), 'same scale and camera')

    def test_cross_segment_cut(self):
        plan, _ = delivery_sample()
        second = deepcopy(plan['segments'][0])
        second.update(id='S2', speech=[], english_words=0)
        second['shots'][0]['start_scale'] = '近景'
        plan['segments'][0]['shots'][-1]['end_scale'] = '中近景'
        second['shots'][0]['start_camera'] = plan['segments'][0]['shots'][-1]['end_camera']
        plan['segments'].append(second)
        self.assert_finding(validate(plan), 'forbidden playback cut')

    def test_cross_segment_cut_allows_camera_or_character_change(self):
        for use_character in (False, True):
            with self.subTest(use_character=use_character):
                plan, _ = two_segment_delivery()
                before = plan['segments'][0]['shots'][-1]
                after = plan['segments'][1]['shots'][0]
                before['end_scale'] = '中近景'
                after['start_scale'] = '近景'
                if use_character:
                    after['start_camera'] = before['end_camera']
                    before['end_subject'], after['start_subject'] = 'Mara', 'Elias'
                self.assertEqual(validate(plan), [])

    def test_character_change_does_not_waive_same_scale_rules(self):
        plan, _ = delivery_sample()
        before, after = plan['segments'][0]['shots']
        before['end_subject'] = 'Mara'
        after.update(start_camera=before['end_camera'], start_subject='Elias')
        self.assert_finding(validate(plan), 'same scale and camera')
        plan, _ = two_segment_delivery()
        plan['segments'][0]['shots'][-1]['end_subject'] = 'Mara'
        plan['segments'][1]['shots'][0]['start_subject'] = 'Elias'
        self.assert_finding(validate(plan), 'same scale across prompt boundary')

    def test_cross_segment_same_scale_with_different_cameras(self):
        for scale in sorted(SCALES):
            with self.subTest(scale=scale):
                plan, _ = two_segment_delivery()
                for segment in plan['segments']:
                    for shot in segment['shots']:
                        shot.update(start_scale=scale, end_scale=scale)
                self.assertEqual(validate(plan), [
                    f'S1 -> S2: same scale across prompt boundary ({scale})'])

    def test_cross_segment_different_scale_is_legal(self):
        plan, _ = two_segment_delivery()
        for segment, scale in zip(plan['segments'], ('中近景', '面部特写')):
            for shot in segment['shots']:
                shot.update(start_scale=scale, end_scale=scale)
        self.assertEqual(validate(plan), [])

    def test_cross_segment_same_scale_uses_motion_endpoint(self):
        plan, _ = two_segment_delivery()
        plan['segments'][0]['shots'][-1].update(start_scale='全景', end_scale='近景')
        plan['segments'][1]['shots'][0]['start_scale'] = '近景'
        self.assertEqual(validate(plan), [
            'S1 -> S2: same scale across prompt boundary (近景)'])

    def test_flashback_entry_and_return_require_different_scales(self):
        for field, expected in [('start_scale', 'S1 -> S2'), ('end_scale', 'S2 -> S1')]:
            with self.subTest(field=field):
                plan = visual_flashback_plan()
                self.assertEqual(validate(plan), [])
                plan['segments'][1]['shots'][0][field] = '中近景'
                self.assertEqual(validate(plan), [
                    f'{expected}: same scale across prompt boundary (中近景)'])

    def test_boundary_scales_follow_playback_not_generation_order(self):
        plan = visual_flashback_plan()
        # Generation order ends S1 on the same scale that starts S2, but those
        # shots are not adjacent in the actual S1-first / S2 / S1-rest playback.
        plan['segments'][0]['shots'][1].update(start_scale='面部特写', end_scale='面部特写')
        plan['segments'][1]['shots'][0]['end_scale'] = '近景'
        self.assertEqual(validate(plan), [])

    def test_new_episode_resets_boundary_comparison(self):
        plan, _ = two_segment_delivery()
        plan['episodes'].append(dict(id='next', complete=False))
        plan['segments'][1]['episode'] = 'next'
        self.assertEqual(validate(plan), [])

    def test_final_delivery_rejects_same_scale_boundary(self):
        plan, text = two_segment_delivery()
        self.assertEqual(validate_delivery(plan, text), [
            'S1 -> S2: same scale across prompt boundary (中景)'])
        for shot in plan['segments'][1]['shots']:
            shot.update(start_scale='近景', end_scale='近景')
        second_start = text.index('第 2 段')
        text = text[:second_start] + text[second_start:].replace('中景', '近景')
        review(plan, text)
        self.assertEqual(validate(plan), [])
        self.assertEqual(validate_delivery(plan, text), [])

    def test_flashback_uses_playback_order(self):
        plan = legacy.PlanTests().flashback_plan()
        for segment in plan['segments']:
            segment['cast'] = ['Mara']
            for i, shot in enumerate(segment['shots']):
                shot.update(frame_id=f'分镜{i+1}', start_scale='全景', end_scale='全景',
                            start_camera=f'{segment["id"]}-{i}', end_camera=f'{segment["id"]}-{i}', visible_characters=['Mara'])
        plan['segments'][0]['shots'][0]['end_scale'] = '中近景'
        plan['segments'][1]['shots'][0]['start_scale'] = '近景'
        plan['segments'][1]['shots'][0]['start_camera'] = plan['segments'][0]['shots'][0]['end_camera']
        self.assert_finding(validate(plan), 'forbidden playback cut')

    def test_flashback_cut_allows_camera_or_character_change(self):
        for use_character in (False, True):
            with self.subTest(use_character=use_character):
                plan = visual_flashback_plan()
                before = plan['segments'][0]['shots'][0]
                inserted = plan['segments'][1]['shots'][0]
                inserted.update(start_scale='近景', end_scale='近景')
                if use_character:
                    before['end_subject'] = 'Mara'
                    inserted.update(start_camera=before['end_camera'], start_subject='Elias')
                    inserted['end_subject'] = 'Elias'
                    resumed = plan['segments'][0]['shots'][1]
                    resumed.update(start_camera=inserted['end_camera'], start_subject='Mara')
                self.assertEqual(validate(plan), [])

    def test_cast_union_and_final_names(self):
        plan, text = delivery_sample()
        plan['segments'][0]['cast'] = ['Mara']
        self.assert_finding(validate(plan), 'scene-character union')
        self.assert_finding(validate_delivery(plan, text), 'final cast differs')

    def test_template_frame_voice_kinds(self):
        plan, _ = delivery_sample()
        voice = deepcopy(plan['segments'][0]['speech'][0])
        voice.update(source_id='L2', text='Wait.', kind='OS', start=2.1, end=3.1, estimated_seconds=1)
        plan['sources'].append(dict(id='L2', episode='current', speaker='Mara', kind='OS', language='en', text='Wait.'))
        plan['segments'][0]['speech'].append(voice)
        plan['segments'][0]['english_words'] += 1
        plan['segments'][0]['shots'][1]['frame_id'] = '分镜1'
        self.assert_finding(validate(plan), 'template frame contains multiple')

    def test_each_voice_kind_rejects_multiple_speakers_in_one_frame(self):
        for kind in ('dialogue', 'VO', 'OS'):
            with self.subTest(kind=kind):
                plan, text = two_speaker_delivery(kind=kind)
                self.assert_finding(validate(plan), 'multiple speakers in one template frame')
                self.assert_finding(validate_delivery(plan, text), 'multiple speakers in one template frame')

    def test_new_speaker_in_next_frame_is_allowed_without_new_segment(self):
        for kind in ('dialogue', 'VO', 'OS'):
            with self.subTest(kind=kind):
                plan, text = two_speaker_delivery(merged=False, kind=kind)
                self.assertEqual(validate(plan), [])
                self.assertEqual(validate_delivery(plan, text), [])

    def test_same_speaker_can_have_multiple_lines_in_one_frame(self):
        for kind in ('dialogue', 'VO', 'OS'):
            with self.subTest(kind=kind):
                plan, text = two_speaker_delivery(kind=kind, speaker='Mara')
                self.assertEqual(validate(plan), [])
                self.assertEqual(validate_delivery(plan, text), [])

    def test_source_overlap_does_not_automatically_waive_frame_speaker_rule(self):
        plan, text = two_speaker_delivery(overlap=True)
        self.assert_finding(validate(plan), 'multiple speakers in one template frame')
        self.assert_finding(validate_delivery(plan, text), 'multiple speakers in one template frame')

    def test_explicit_frame_exception_can_preserve_source_overlap(self):
        for kind in ('dialogue', 'VO', 'OS'):
            with self.subTest(kind=kind):
                plan, text = two_speaker_delivery(kind=kind, overlap=True)
                plan['segments'][0]['frame_speaker_exceptions'] = {
                    '分镜1': {'basis': '原文 L2 在 L1 句尾抢话；本分镜保留同期两人声源。',
                              'source_ids': ['L1', 'L2']}}
                review(plan, text)  # The exception changes design dependencies.
                self.assertEqual(validate(plan), [])
                self.assertEqual(validate_delivery(plan, text), [])

    def test_frame_exception_does_not_waive_voice_kind_rule(self):
        plan, text = two_speaker_delivery()
        plan['sources'][1]['kind'] = 'OS'
        plan['segments'][0]['speech'][1]['kind'] = 'OS'
        plan['segments'][0]['frame_speaker_exceptions'] = {
            '分镜1': {'basis': '用户指定同一分镜保留两人声音。', 'source_ids': ['L1', 'L2']}}
        text = text.replace('台词参考（Elias）', 'OS 参考（Elias）')
        review(plan, text)
        self.assert_finding(validate(plan), 'template frame contains multiple voice kinds')
        self.assert_finding(validate_delivery(plan, text), 'template frame contains multiple voice kinds')

    def test_frame_exception_requires_valid_scope_and_source_records(self):
        for exception, message in [
            ([], 'must be an object'),
            ({'分镜9': {'basis': '原文抢话', 'source_ids': ['L1', 'L2']}}, 'unknown frame_id'),
            ({'分镜1': None}, 'must contain basis'),
            ({'分镜1': {'basis': '', 'source_ids': ['L1', 'L2']}}, 'nonempty basis'),
            ({'分镜1': {'basis': '原文抢话', 'source_ids': [['L1']]}}, 'unique source_ids'),
            ({'分镜1': {'basis': '原文抢话', 'source_ids': ['L1', 'L1']}}, 'unique source_ids'),
            ({'分镜1': {'basis': '原文抢话', 'source_ids': ['L1']}}, 'differ from actual frame speech')]:
            with self.subTest(exception=exception):
                plan, text = two_speaker_delivery()
                plan['segments'][0]['frame_speaker_exceptions'] = exception
                self.assert_finding(validate(plan), message)
                self.assert_finding(validate_delivery(plan, text), message)

    def test_frame_exception_cannot_be_copied_to_a_single_speaker_frame(self):
        plan, text = two_speaker_delivery(merged=False)
        plan['segments'][0]['frame_speaker_exceptions'] = {
            '分镜1': {'basis': '原文特殊声画要求', 'source_ids': ['L1']}}
        self.assert_finding(validate_delivery(plan, text), 'no multiple-speaker case')

    def test_carried_speech_counts_in_the_next_frame(self):
        plan, text = two_speaker_delivery(merged=False)
        segment = plan['segments'][0]
        segment['speech'][0]['end'] = 2.1
        segment['speech'][1].update(start=2.2, end=3.2)
        text = text.replace('0.2–1.7 秒', '0.2–2.1 秒').replace('2.1–3.1 秒', '2.2–3.2 秒')
        review(plan, text)
        self.assert_finding(validate_delivery(plan, text), 'multiple speakers in one template frame')
        # A boundary at the actual voice handoff fixes the grouping without adding silence.
        segment['shots'][0]['end'] = 2.1
        segment['shots'][1]['start'] = 2.1
        text = text.replace('分镜1（0–2 秒）', '分镜1（0–2.1 秒）').replace('分镜2（2–4 秒）', '分镜2（2.1–4 秒）')
        review(plan, text)
        self.assertEqual(validate(plan), [])
        self.assertEqual(validate_delivery(plan, text), [])

    def test_final_text_change_invalidates_review(self):
        plan, text = delivery_sample()
        self.assert_finding(validate_delivery(plan, text.replace('右侧窗光', '左侧灯光')), 'changed after review')

    def test_final_speech_drift_is_detected_even_after_rehash(self):
        plan, text = delivery_sample()
        for changed in [text.replace('Put it down.', 'Drop it.'), text.replace('台词参考（Mara）音色', '台词参考（Elias）音色'),
                        text.replace('台词参考（Mara）音色', 'OS 参考（Mara）音色')]:
            with self.subTest(changed=changed):
                review(plan, changed)
                self.assert_finding(validate_delivery(plan, changed), 'final speech')

    def test_chinese_cast_and_voice_name_match_source(self):
        plan, text = delivery_sample()
        for source in plan['sources']:
            source['speaker'] = {'Mara': '林晚', 'Elias': '沈砚'}.get(source['speaker'], source['speaker'])
        segment = plan['segments'][0]
        segment['cast'] = ['林晚', '沈砚']
        for voice in segment['speech']:
            voice['speaker'] = {'Mara': '林晚', 'Elias': '沈砚'}.get(voice['speaker'], voice['speaker'])
        for shot in segment['shots']:
            shot['visible_characters'] = ['林晚', '沈砚']
            shot['relation'] = json.loads(json.dumps(shot['relation'], ensure_ascii=False).replace('Mara', '林晚').replace('Elias', '沈砚'))
            shot['relation']['characters'] = ['林晚', '沈砚']
            shot['relation']['evidence'] = shot['relation']['evidence'].replace('Mara', '林晚').replace('Elias', '沈砚')
            shot['view'] = json.loads(json.dumps(shot['view'], ensure_ascii=False).replace('Mara', '林晚').replace('Elias', '沈砚'))
        text = text.replace('Mara', '林晚').replace('Elias', '沈砚')
        review(plan, text)
        self.assertEqual(validate(plan), [])
        self.assertEqual(validate_delivery(plan, text), [])
        wrong = text.replace('台词参考（林晚）音色', '台词参考（沈砚）音色')
        review(plan, wrong)
        self.assert_finding(validate_delivery(plan, wrong), 'final speech')

    def test_voice_prefix_and_reference_support_vo_and_os(self):
        for kind, label in [('VO', 'VO 参考（Mara）音色'), ('OS', 'OS 参考（Mara）音色')]:
            with self.subTest(kind=kind):
                plan, text = delivery_sample()
                plan['sources'][0]['kind'] = kind
                plan['segments'][0]['speech'][0]['kind'] = kind
                text = text.replace('台词参考（Mara）音色', label)
                review(plan, text)
                self.assertEqual(validate(plan), [])
                self.assertEqual(validate_delivery(plan, text), [])

    def test_old_voice_name_position_is_rejected(self):
        plan, text = delivery_sample()
        text = text.replace('（Mara）台词参考（Mara）音色', '（Mara）台词参考音色')
        review(plan, text)
        self.assert_finding(validate_delivery(plan, text), 'final speech')

    def test_voice_labels_require_both_name_positions(self):
        for kind, label in [('dialogue', '台词参考'), ('VO', 'VO 参考'), ('OS', 'OS 参考')]:
            plan, text = two_speaker_delivery(kind=kind, speaker='Mara')
            full = f'（Mara）{label}（Mara）音色'
            for incomplete in (f'{label}（Mara）音色', f'（Mara）{label}音色'):
                with self.subTest(kind=kind, incomplete=incomplete):
                    changed = text.replace(full, incomplete)
                    review(plan, changed)
                    self.assert_finding(validate_delivery(plan, changed), 'final speech')

    def test_both_voice_names_must_match_each_other_and_source(self):
        for kind, label in [('dialogue', '台词参考'), ('VO', 'VO 参考'), ('OS', 'OS 参考')]:
            plan, text = two_speaker_delivery(kind=kind, speaker='Mara')
            full = f'（Mara）{label}（Mara）音色'
            for prefix, reference in [('Elias', 'Mara'), ('Mara', 'Elias'), ('Elias', 'Elias')]:
                with self.subTest(kind=kind, prefix=prefix, reference=reference):
                    changed = text.replace(full, f'（{prefix}）{label}（{reference}）音色')
                    review(plan, changed)
                    expected = 'prefix and reference names differ' if prefix != reference else 'final speech text, speaker'
                    self.assert_finding(validate_delivery(plan, changed), expected)

    def test_final_speech_interval_must_match_record(self):
        plan, text = delivery_sample()
        for annotation in ('0.2–9.7 秒', '0.4–1.7 秒', '1.7–0.2 秒', '0.2–1.7 秒，1–2 秒', '约 1.5 秒'):
            with self.subTest(annotation=annotation):
                changed = text.replace('0.2–1.7 秒', annotation)
                review(plan, changed)
                self.assertTrue(validate_delivery(plan, changed))

    def test_internal_shot_intervals_must_match_both_visual_fields(self):
        plan, text = two_speaker_delivery(speaker='Mara')
        for old, new in [('运镜：0–2 秒', '运镜：0–3 秒'), ('；2–4 秒硬切', '；1–4 秒硬切'),
                         ('画面内容：0–2 秒', '画面内容：0–1 秒'), ('2–4 秒 Elias', '3–4 秒 Elias')]:
            with self.subTest(new=new):
                changed = text.replace(old, new)
                self.assertNotEqual(text, changed)
                review(plan, changed)
                self.assert_finding(validate_delivery(plan, changed), 'internal shot intervals')

    def test_relation_evidence_cannot_be_borrowed_from_another_internal_shot(self):
        plan, text = two_speaker_delivery(speaker='Mara')
        plan['segments'][0]['shots'][0]['relation']['evidence'] = plan['segments'][0]['shots'][1]['relation']['evidence']
        self.assert_finding(validate_delivery(plan, text), 'relation evidence is absent')

    def test_final_structure_and_time(self):
        plan, text = delivery_sample()
        for changed, expected in [(text.replace('预估时长 4 秒', '预估时长 5 秒'), 'final duration'),
                                  (text.replace('环境光影：右侧窗光。\n', ''), 'segment fields'),
                                  (text + '转场方式：硬切。\n', 'omit transition'),
                                  (text.replace('分镜2（2–4 秒）', '分镜2（2–3 秒）'), 'frame name/time')]:
            with self.subTest(expected=expected):
                review(plan, changed)
                self.assert_finding(validate_delivery(plan, changed), expected)

    def test_final_playback_map_matches_records(self):
        plan, text = delivery_sample()
        plan['events'] = [dict(id=f'E{i}', episode='current', segment_id='S1', text=f'Beat {i}', start=start, end=end)
                          for i, (start, end) in enumerate([(0, 2), (2, 4)])]
        plan['playback'] = [dict(segment_id='S1', start=0, end=2), dict(segment_id='S1', start=2, end=4)]
        text += '剪辑顺序：第 1 段 0–2 秒 → 第 1 段 2–4 秒；按原文插入点剪辑。\n'
        review(plan, text)
        self.assertEqual(validate(plan), [])
        self.assertEqual(validate_delivery(plan, text), [])
        changed = text.replace('第 1 段 0–2 秒 → 第 1 段 2–4 秒', '第 1 段 2–4 秒 → 第 1 段 0–2 秒')
        review(plan, changed)
        self.assert_finding(validate_delivery(plan, changed), 'editing map differs')

    def test_final_cli_exit_codes(self):
        plan, text = delivery_sample()
        plan['mode'] = '真人剧'
        review(plan, text)
        # This hand-authored legacy fixture uses explicit presentation wording.
        plan['segments'][0]['presentation'] = {
            '整体风格': '真人写实，各镜头固定。',
            '画面规格': 'AI 真人写实竖屏短剧，8K 分辨率，人物、道具及接触位置清楚可见，前景、主体与背景位置明确。',
            '声音限制': '全程无背景音乐；仅使用环境音、动作音效、转场音效及人物自然声音。',
        }
        from mode_rules import presentation_defaults
        defaults = presentation_defaults(plan)
        for field in ('声音限制', '画面限制'):
            plan['segments'][0]['presentation'][field] = defaults[field]
            text = '\n'.join(field + '：' + defaults[field] if line.startswith(field + '：') else line
                             for line in text.splitlines()) + '\n'
        review(plan, text)
        script = Path(__file__).with_name('validate_plan.py')
        with tempfile.TemporaryDirectory() as folder:
            plan_path, final_path = Path(folder)/'plan.json', Path(folder)/'final.txt'
            plan_path.write_text(json.dumps(plan), encoding='utf-8')
            final_path.write_text(text, encoding='utf-8')
            command = [sys.executable, '-X', 'utf8', '-B', str(script), str(plan_path), '--final-prompt', str(final_path)]
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('final prompt checked', result.stdout)
            relation = plan['segments'][0]['shots'][0].pop('relation')
            plan_path.write_text(json.dumps(plan), encoding='utf-8')
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('relation record is required', result.stdout + result.stderr)
            plan['segments'][0]['shots'][0]['relation'] = relation
            plan_path.write_text(json.dumps(plan), encoding='utf-8')
            final_path.write_text(text.replace('Put it down.', 'Drop it.'), encoding='utf-8')
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            plan_path.write_text('{', encoding='utf-8')
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
