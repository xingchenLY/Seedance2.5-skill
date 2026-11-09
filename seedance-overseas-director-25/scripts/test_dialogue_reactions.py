"""Synthetic dialogue cases check missing reception, visible coverage and timing.

These do not certify a generated video or substitute for judging the source.
"""
from copy import deepcopy
import unittest

from dialogue_reactions import check_dialogue_coverage, dialogue_turns, reaction_prose
from delivery_checks import validate_delivery
from render_prompt import render_plan
from test_modes import mode_sample
from mode_rules import MODES


WORDS = ('You used my name without asking me. I trusted you to keep our conversation '
         'private, but you let everyone watch and never even asked what I wanted.')
PROOF = 'Elias 正脸与眉眼清楚可见'
REACTION = 'Elias 听清未经许可的指责后，原本勉强维持的笑容消失，目光从 Mara 脸上避开；他没有开口'


def long_dialogue(mode='真人剧', reviewed=True):
    data = mode_sample(mode)
    data['sources'][0].update(kind='dialogue', text=WORDS)
    if MODES[mode]['narrative']:
        data['narration_sources'] = []
        data['narration_absence_basis'] = '合成测试只有原文现场对白，没有叙事旁白。'
    segment = data['segments'][0]
    voice = segment['speech'][0]
    voice.update(kind='dialogue', text=WORDS, start=0, end=12, estimated_seconds=12,
                 performance='延续质问力度，咬字清楚，在 asking 和 wanted 加重原词')
    shots = segment['shots']
    shots.append(deepcopy(shots[0]))
    for shot, start, end, scale in zip(shots, (0, 4, 7), (4, 7, 12), ('近景', '中近景', '近景')):
        shot.update(start=start, end=end, frame_id='分镜1', start_scale=scale, end_scale=scale)
    shots[1]['camera_text'] += PROOF + '。'
    segment['duration'] = 12
    if reviewed:
        voice['dialogue_review'] = dict(mode='covered', listeners=['Elias'],
            basis='合成源文中两人当面交谈，Elias 听到未经许可使用姓名的指责后笑容消失。', reactions=[
                dict(listener='Elias', trigger=dict(segment_id='S1', speech_index=0,
                     quote='You used my name without asking me.', heard_at=3.8),
                     segment_id='S1', shot_start=4, start=4, end=6.5, channel='face',
                     visibility_evidence=PROOF, evidence=REACTION)])
    return data


def errors(data):
    result = []
    check_dialogue_coverage(data, result)
    return result


def long_take_progression():
    """An authored synthetic performance arc; merits still require source review."""
    stages = [
        'Mara 指责未经许可使用姓名，躯干前倾，空闲手掌向上朝 Elias 展开以要求回应',
        'Mara 说到曾经的信任，伸出的手臂停住，掌心转向自己胸前，强硬质问露出受伤',
        'Mara 回到未询问就公开的指责，手臂收回身侧，躯干恢复直立，目光重新锁住 Elias',
    ]
    return dict(mode='progression', basis='合成原词从未经许可转到曾经的信任，再回到公开与未询问；三阶段姿态承接同一表演。',
                beats=[dict(start=a, end=b, change='performance_turn', new_information=e, visual_evidence=e)
                       for a, b, e in zip((0, 4, 8), (4, 8, 12), stages)],
                long_take=dict(reason='performance_arc',
                    why_uncut='连续观察 Mara 向对方要求回应的手势如何转向自己，再放下手重新正视对方；显出指责中暴露受伤又恢复边界的过程。',
                    finish_evidence=stages[-1], cut_review=dict(at=4,
                        next_view='切到 Elias 收到指责后的正面近景',
                        loss_if_cut='此处恰是 Mara 伸出的手停住并转向自己胸前的起点，切走会漏掉强硬姿态转为受伤的可见瞬间。')))


class DialogueReactionTests(unittest.TestCase):
    def assert_finding(self, found, phrase):
        self.assertTrue(any(phrase in f for f in found), found)

    def test_unreviewed_long_speaker_hold_is_rejected_in_all_modes(self):
        for mode in MODES:
            data = long_dialogue(mode, reviewed=False)
            data['segments'][0]['shots'] = data['segments'][0]['shots'][:1]
            data['segments'][0]['shots'][0]['end'] = 12
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'needs a dialogue_review'):
                render_plan(data)

    def test_continuous_dialogue_covers_listener_without_splitting_audio_all_modes(self):
        for mode in MODES:
            data = long_dialogue(mode)
            original = deepcopy(data['segments'][0]['speech'])
            plan, text = render_plan(data)
            with self.subTest(mode=mode):
                self.assertEqual(plan['segments'][0]['speech'], original)
                self.assertEqual(text.count('“' + WORDS + '”'), 1)
                self.assertEqual(len(plan['segments'][0]['shots']), 3)
                self.assertEqual(text.count('内部硬切至本镜'), 2)
                self.assertIn('Mara的原台词声接续同一句后文', text)
                self.assertNotIn('dialogue_review', text)
                self.assertNotIn('合成源文中两人当面交谈', text)
                self.assertEqual(validate_delivery(plan, text, require_review=False), [])

    def test_short_critical_line_requires_its_source_target(self):
        data = long_dialogue()
        voice = data['segments'][0]['speech'][0]
        voice.update(end=5, estimated_seconds=5)
        data['sources'][0]['reaction_targets'] = ['Elias']
        del voice['dialogue_review']
        self.assert_finding(errors(data), 'needs a dialogue_review')
        voice['dialogue_review'] = dict(mode='no_listener', listeners=[], basis='误称没有听者')
        self.assert_finding(errors(data), 'cannot erase')

    def test_source_targets_cannot_be_replaced_by_an_unrelated_earlier_reaction(self):
        data = long_dialogue()
        source = deepcopy(data['sources'][0]); source.update(id='L2', text='I quit.', reaction_targets=['Elias'])
        data['sources'].append(source)
        segment = data['segments'][0]
        segment['speech'].append(dict(source_id='L2', speaker='Mara', kind='dialogue',
                                      text='I quit.', start=12, end=13, estimated_seconds=1))
        segment['duration'] = segment['shots'][-1]['end'] = 13
        self.assert_finding(errors(data), 'reaction_targets are missing')

    def test_real_same_frame_face_reaction_needs_no_forced_cut(self):
        data = long_dialogue()
        segment = data['segments'][0]
        shot = segment['shots'][0]
        segment['shots'] = [shot]; shot['end'] = 12
        shot['view'].update(position='两人东侧', direction='西侧两人', foreground=[])
        shot['camera_text'] = '固定双人中近景，' + PROOF + '。'
        shot['relation']['placements'][1]['placement'] = 'Elias 位于画左，侧身面向 Mara，正脸可见'
        shot['visual_text'] = 'Mara 在画右继续开口，Elias 位于画左听着。'
        shot['dialogue_visual'] = long_take_progression()
        node = segment['speech'][0]['dialogue_review']['reactions'][0]; node['shot_start'] = 0
        plan, text = render_plan(data)
        self.assertEqual(len(plan['segments'][0]['shots']), 1)
        self.assertNotIn('内部硬切', text)
        self.assertIn(REACTION, text)

    def test_shoulder_only_cannot_claim_face_reaction_but_visible_body_can_work(self):
        data = long_dialogue()
        node = data['segments'][0]['speech'][0]['dialogue_review']['reactions'][0]
        node.update(shot_start=0, start=3.8, end=4)
        shot = data['segments'][0]['shots'][0]
        shot['camera_text'] += PROOF
        self.assert_finding(errors(data), 'shoulder/back-only')
        node.update(channel='body', evidence='Elias 前景双肩从微耸状态缓慢落低，脚位没有改变',
                    visibility_evidence='Elias 肩背在左前景清楚可见')
        shot['camera_text'] += node['visibility_evidence']
        self.assertEqual(errors(data), [])

    def test_cast_membership_and_another_person_face_do_not_prove_visibility(self):
        data = long_dialogue()
        node = data['segments'][0]['speech'][0]['dialogue_review']['reactions'][0]
        node['visibility_evidence'] = 'Mara 正脸清楚可见'
        self.assert_finding(errors(data), 'visibility evidence')
        node['visibility_evidence'] = PROOF
        data['segments'][0]['shots'][1]['visible_characters'] = ['Mara']
        self.assert_finding(errors(data), 'not only the cast list')

    def test_future_quote_or_early_reaction_or_wrong_interval_fails(self):
        for field, value, expected in [('heard_at', 8, 'precedes information'),
                                      ('quote', 'A later secret.', 'trigger quote/time')]:
            data = long_dialogue()
            data['segments'][0]['speech'][0]['dialogue_review']['reactions'][0]['trigger'][field] = value
            with self.subTest(field=field): self.assert_finding(errors(data), expected)
        data = long_dialogue()
        data['segments'][0]['speech'][0]['dialogue_review']['reactions'][0]['end'] = 9
        self.assert_finding(errors(data), 'inside its actual shot')

    def test_final_removed_moved_or_hidden_reaction_fails_independent_of_review_hash(self):
        plan, text = render_plan(long_dialogue())
        node = plan['segments'][0]['speech'][0]['dialogue_review']['reactions'][0]
        cue = reaction_prose(node)
        for broken in (text.replace(cue, ''), text.replace(cue, '').replace('画面内容：0–4 秒 ', '画面内容：0–4 秒 ' + cue),
                       text.replace(PROOF, 'Elias 仅露后脑')):
            self.assertTrue(validate_delivery(plan, broken, require_review=False))
        del plan['segments'][0]['speech'][0]['dialogue_review']
        self.assert_finding(validate_delivery(plan, text, require_review=False), 'needs a dialogue_review')

    def test_long_take_has_concrete_hold_and_does_not_gain_narration_metadata(self):
        data = long_dialogue()
        segment = data['segments'][0]; shot = segment['shots'][0]
        segment['shots'] = [shot]; shot['end'] = 12
        evidence = 'Mara 从向对方要求回应转为暴露受伤，最后重新直立并盯住 Elias'
        shot['visual_text'] = evidence
        shot['dialogue_visual'] = long_take_progression()
        review = dict(mode='hold_speaker', listeners=['Elias'], basis='合成情境要求连续保留指责中的态度转折，本段没有必拍听者节点。',
            hold=dict(reason='speaker_performance', why='连续看清 Mara 强硬指责中暴露受伤再恢复边界；听者判断暂未揭示。',
                      segment_id='S1', shot_start=0, evidence=evidence))
        segment['speech'][0]['dialogue_review'] = review
        plan, text = render_plan(data)
        self.assertNotIn('narration_visual', plan['segments'][0]['shots'][0])
        self.assertNotIn('内部硬切', text)
        self.assert_finding(validate_delivery(plan, text.replace(evidence, 'Mara 继续讲话'), require_review=False), 'performance is absent')
        review['hold']['why'] = '为了保持台词连续，所以不切镜'
        self.assert_finding(errors(data), 'not audio continuity')
        data['sources'][0]['reaction_targets'] = ['Elias']
        self.assert_finding(errors(data), 'cannot replace required')

    def test_negated_reaction_or_visibility_is_not_positive_final_coverage(self):
        plan, text = render_plan(long_dialogue())
        node = plan['segments'][0]['speech'][0]['dialogue_review']['reactions'][0]
        cue = reaction_prose(node)
        for broken in (text.replace(cue, '不要呈现：' + cue), text.replace(PROOF, '不要让' + PROOF)):
            self.assertTrue(validate_delivery(plan, broken, require_review=False))

    def test_one_source_split_into_fragments_does_not_require_repeating_its_reaction(self):
        data = long_dialogue()
        data['sources'][0]['reaction_targets'] = ['Elias']
        first = data['segments'][0]['speech'][0]
        second = deepcopy(first); second.pop('dialogue_review')
        first.update(text='You used my name without asking me.', end=4, estimated_seconds=4)
        second.update(text=WORDS[len(first['text']):].strip(), start=4, end=12, estimated_seconds=8)
        data['segments'][0]['speech'].append(second)
        self.assertEqual(errors(data), [])

    def test_drama_internal_cuts_cannot_be_removed_or_contradicted(self):
        for mode in ('真人剧', '3D漫真人剧'):
            plan, text = render_plan(long_dialogue(mode))
            broken = text.replace('内部硬切至本镜。', '保持原镜继续。', 1)
            self.assert_finding(validate_delivery(plan, broken, require_review=False), 'explicit internal cut is missing')
            broken = text.replace('运镜：0–4 秒 ', '运镜：0–4 秒 本段全程不切镜。')
            self.assert_finding(validate_delivery(plan, broken, require_review=False), 'whole-segment no-cut')

    def test_six_seconds_is_review_threshold_not_a_cut_quota(self):
        data = long_dialogue(reviewed=False)
        voice = data['segments'][0]['speech'][0]
        voice.update(end=6, estimated_seconds=6)
        self.assertEqual(errors(data), [])
        voice['end'] = 6.1
        self.assert_finding(errors(data), 'needs a dialogue_review')

    def test_multiple_short_lines_from_one_speaker_are_one_reviewed_turn(self):
        data = long_dialogue(reviewed=False)
        segment = data['segments'][0]; first = segment['speech'][0]
        first.update(end=4, estimated_seconds=4)
        second = deepcopy(first); second.update(source_id='L2', start=4, end=8)
        segment['speech'].append(second)
        data['sources'].append(dict(data['sources'][0], id='L2'))
        self.assertEqual(len(dialogue_turns(data)), 1)
        self.assert_finding(errors(data), 'needs a dialogue_review')
        second['dialogue_review'] = dict(mode='no_listener', listeners=[], basis='测试错误放置')
        self.assert_finding(errors(data), 'first speech')

    def test_cross_segment_turn_uses_playback_time_for_arrival(self):
        data = long_dialogue()
        first = data['segments'][0]; first['duration'] = first['shots'][-1]['end'] = 12
        second = deepcopy(first); second.update(id='S2', speech=[], duration=12)
        data['segments'].append(second)
        node = first['speech'][0]['dialogue_review']['reactions'][0]
        node.update(segment_id='S2', shot_start=0, start=0, end=3, after_basis='合成原文明确听完后才给出反应。', channel='body',
                    visibility_evidence='Elias 肩背在左前景清楚可见', evidence='Elias 前景双肩随刚收到的信息缓慢落低')
        second['shots'][0]['camera_text'] += node['visibility_evidence']
        self.assertEqual(errors(data), [])
        data['playback'] = [dict(segment_id='S2', start=0, end=12), dict(segment_id='S1', start=0, end=12)]
        self.assert_finding(errors(data), 'precedes information')

    def test_cross_segment_short_speeches_do_not_evade_review(self):
        data = long_dialogue(reviewed=False)
        first = data['segments'][0]; first['speech'][0].update(end=4, estimated_seconds=4)
        second = deepcopy(first); second['id'] = 'S2'
        second['speech'][0]['source_id'] = 'L2'
        data['segments'].append(second); data['sources'].append(dict(data['sources'][0], id='L2'))
        self.assert_finding(errors(data), 'needs a dialogue_review')

    def test_after_turn_reaction_requires_a_real_reason(self):
        data = long_dialogue()
        segment = data['segments'][0]; voice = segment['speech'][0]
        voice.update(end=4, estimated_seconds=4)
        node = voice['dialogue_review']['reactions'][0]
        self.assert_finding(errors(data), 'after the turn needs')
        node['after_basis'] = '等台词说完'
        self.assert_finding(errors(data), 'after the turn needs')
        node['after_basis'] = '合成原文指定听完完整指责后才作反应。'
        self.assertEqual(errors(data), [])

    def test_trigger_quote_preserves_source_word_boundaries(self):
        data = long_dialogue()
        node = data['segments'][0]['speech'][0]['dialogue_review']['reactions'][0]
        for wrong in ('askingme', 'ask'):
            node['trigger']['quote'] = wrong
            self.assert_finding(errors(data), 'trigger quote/time')

    def test_narration_os_and_inner_are_not_heard_dialogue(self):
        for kind in ('narration', 'OS', 'inner', 'VO'):
            data = long_dialogue(reviewed=False)
            data['segments'][0]['speech'][0]['kind'] = kind
            with self.subTest(kind=kind): self.assertEqual(errors(data), [])

    def test_explicit_no_listener_is_reviewable_without_inventing_someone(self):
        data = long_dialogue()
        data['segments'][0]['speech'][0]['dialogue_review'] = dict(mode='no_listener', listeners=[],
            basis='合成情境改为人物独处对没有接收人的设备自述；实际原文与取景须另行修改复核。')
        self.assertEqual(errors(data), [])  # The source-based claim is intentionally not machine certified.

    def test_malformed_records_return_findings(self):
        for value in ([], '', 3, {}, None):
            data = long_dialogue()
            data['segments'][0]['speech'][0]['dialogue_review'] = value
            with self.subTest(value=value): self.assertTrue(errors(data))
        for field, value in [('start', float('nan')), ('listener', []), ('trigger', None),
                             ('shot_start', False), ('visibility_evidence', 4)]:
            data = long_dialogue()
            data['segments'][0]['speech'][0]['dialogue_review']['reactions'][0][field] = value
            with self.subTest(field=field): self.assertTrue(errors(data))


if __name__ == '__main__':
    unittest.main()
