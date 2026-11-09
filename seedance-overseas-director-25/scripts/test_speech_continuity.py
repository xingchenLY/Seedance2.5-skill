from copy import deepcopy
import unittest

from delivery_checks import validate_delivery, VISUAL_RANGE
from render_prompt import render_plan
from speech_continuity import speech_context, speech_carry_prose
from test_delivery_checks import review
from test_render_prompt import authored_sample
from test_validate_plan import sample
from test_modes import mode_sample
from mode_rules import MODES, allowed_voice_kinds
from validate_plan import validate


def split_source(gap=1.8):
    plan = sample()
    plan['sources'][0]['text'] = 'You lied to me and I know why.'
    segment = plan['segments'][0]
    segment.update(duration=6, english_words=8,
                   shots=[dict(start=0, end=2), dict(start=2, end=4), dict(start=4, end=6)])
    first = segment['speech'][0]
    first.update(text='You lied to me', start=.2, end=1.8, estimated_seconds=1.6)
    second = deepcopy(first)
    second.update(text='and I know why.', start=1.8+gap, end=3.8+gap, estimated_seconds=2)
    segment['speech'].append(second)
    return plan


def long_voice(kind='dialogue'):
    plan = authored_sample()
    plan['sources'][0].update(kind=kind, text='You lied to me and I know why.')
    plan['segments'][0]['speech'][0].update(kind=kind, text=plan['sources'][0]['text'],
                                           end=3.8, estimated_seconds=3.6)
    return plan


class SpeechContinuityTests(unittest.TestCase):
    def test_complete_words_do_not_hide_reaction_gap(self):
        errors = validate(split_source())
        self.assertEqual(len(errors), 1, errors)
        self.assertIn('unexplained speech gap of 1.800s', errors[0])

    def test_contiguous_fragments_and_natural_single_interval_pass(self):
        self.assertEqual(validate(split_source(0)), [])
        plan, text = render_plan(long_voice())
        self.assertEqual(validate(plan), [])
        self.assertEqual(text.count('“You lied to me and I know why.”'), 1)

    def test_explicit_source_natural_and_story_pauses_pass(self):
        for reason in ('source_pause', 'natural_pause', 'story_break'):
            with self.subTest(reason=reason):
                plan = split_source()
                plan['segments'][0]['speech'][1]['pause_before'] = dict(
                    reason=reason, basis='测试原文在两部分之间要求迟疑，按当前语气估时。', seconds=1.8)
                self.assertEqual(validate(plan), [])

    def test_invalid_pause_exceptions_do_not_bypass_gap_check(self):
        valid = dict(reason='source_pause', basis='原文此处迟疑。', seconds=1.8)
        cases = [None, 'reaction', {}, {**valid, 'reason': 'camera_cut'},
                 {**valid, 'basis': ''}, {**valid, 'reason': []}, {**valid, 'seconds': 1},
                 {**valid, 'seconds': True}, {**valid, 'seconds': float('nan')}]
        for pause in cases:
            with self.subTest(pause=pause):
                plan = split_source()
                plan['segments'][0]['speech'][1]['pause_before'] = pause
                self.assertTrue(validate(plan))

    def test_stale_pause_on_first_or_contiguous_part_is_rejected(self):
        for index in (0, 1):
            plan = split_source(0)
            plan['segments'][0]['speech'][index]['pause_before'] = dict(
                reason='natural_pause', basis='旧的停顿依据。', seconds=0)
            self.assertTrue(any('requires a preceding fragment' in e for e in validate(plan)))

    def test_same_source_fragments_cannot_overlap(self):
        plan = split_source(-.2)
        plan['segments'][0]['overlap_basis'] = '不能用其他原文的抢话依据放宽同一句自身重叠。'
        self.assertTrue(any('same source overlap' in e for e in validate(plan)))

    def test_cross_segment_gap_includes_tail_and_head(self):
        plan = split_source(0)
        first = plan['segments'][0]
        second = deepcopy(first)
        second.update(id='S2', english_words=4)
        second['speech'] = [second['speech'][1]]
        second['speech'][0].update(start=.2, end=2.2)
        first['speech'] = first['speech'][:1]
        first['english_words'] = 4
        plan['segments'].append(second)
        context = speech_context(plan)
        self.assertAlmostEqual(context[('S2', 0)]['gap'], 4.4)
        self.assertTrue(any('unexplained speech gap' in e for e in validate(plan)))
        first['speech'][0].update(start=4.4, end=6)
        second['speech'][0].update(start=0, end=2)
        self.assertEqual(validate(plan), [])

    def test_playback_insertion_counts_actual_silence(self):
        plan = split_source(.2)
        segment = plan['segments'][0]
        segment['speech'][0].update(start=.4, end=2)
        memory = dict(id='M', episode='current', duration=2, english_words=0,
                      speech=[], shots=[dict(start=0, end=2)])
        plan['segments'].append(memory)
        plan['playback'] = [dict(segment_id='S1', start=0, end=2),
                            dict(segment_id='M', start=0, end=2),
                            dict(segment_id='S1', start=2, end=6)]
        plan['events'] = [dict(id=str(i), episode='current', text='原文对应剧情。', **clip)
                          for i, clip in enumerate(plan['playback'])]
        self.assertAlmostEqual(speech_context(plan)[('S1', 1)]['gap'], 2)
        self.assertTrue(any('unexplained speech gap of 2.000s' in e for e in validate(plan)))
        segment['speech'][1]['pause_before'] = dict(
            reason='story_break', basis='原文在此句中断处插入无声回忆，返回后接下文。', seconds=2)
        self.assertEqual(validate(plan), [])

    def test_reaction_carry_preserves_each_original_voice_kind(self):
        for kind in ('dialogue', 'VO', 'OS'):
            with self.subTest(kind=kind):
                plan, text = render_plan(long_voice(kind))
                segment = plan['segments'][0]
                cue = speech_carry_prose(segment, segment['shots'][1], speech_context(plan))
                self.assertIn('本段第2秒至第3.8秒', cue)
                self.assertIn('接续同一句后文', cue)
                self.assertFalse(VISUAL_RANGE.search(cue))
                self.assertEqual(text.count('“You lied to me and I know why.”'), 1)
                self.assertNotIn('在画外', cue)  # Speaker's shoulder remains visible.
                self.assertEqual(segment['speech'][0]['kind'], kind)
                if kind in ('VO', 'OS'):
                    self.assertIn('此声不配人物口型', cue)
                review(plan, text)
                self.assertEqual(validate_delivery(plan, text), [])

    def test_removed_or_moved_carry_fails_even_after_new_hash(self):
        plan, text = render_plan(long_voice())
        segment = plan['segments'][0]
        cue = speech_carry_prose(segment, segment['shots'][1], speech_context(plan))
        for replacement in ('', '整体风格：' + cue):
            broken = text.replace(cue, '')
            if replacement:
                broken = broken.replace('整体风格：', replacement, 1)
            review(plan, broken)
            self.assertTrue(any('speech carry is absent' in e for e in validate_delivery(plan, broken)))

    def test_internal_cut_also_needs_local_carry(self):
        plan = long_voice()
        plan['segments'][0]['shots'][1]['frame_id'] = '分镜1'
        derived, text = render_plan(plan)
        self.assertEqual(text.count('（Mara）台词参考（Mara）音色'), 1)
        self.assertIn('本段第2秒至第3.8秒', text)
        review(derived, text)
        self.assertEqual(validate_delivery(derived, text), [])

    def test_voice_starting_over_other_picture_and_ended_voice(self):
        plan = long_voice()
        segment = plan['segments'][0]
        voice = segment['speech'][0]
        voice.update(start=2.1, end=3.8, estimated_seconds=1.7)
        context = speech_context(plan)
        self.assertEqual(speech_carry_prose(segment, segment['shots'][0], context), '')
        cue = speech_carry_prose(segment, segment['shots'][1], context)
        self.assertIn('按原区间起声', cue)
        self.assertNotIn('接续同一句', cue)
        voice.update(start=.2, end=2, estimated_seconds=1.8)
        self.assertEqual(speech_carry_prose(segment, segment['shots'][1], speech_context(plan)), '')

    def test_absent_speaker_is_offscreen_and_listener_not_assigned_words(self):
        plan = long_voice()
        segment = plan['segments'][0]
        shot = segment['shots'][1]
        shot['visible_characters'] = ['Elias']
        cue = speech_carry_prose(segment, shot, speech_context(plan))
        self.assertIn('Mara的原台词声在画外', cue)
        self.assertIn('不替原说话者对口型', cue)
        self.assertNotIn('“', cue)

    def test_approved_pause_resume_is_not_called_uninterrupted(self):
        plan = split_source()
        segment = plan['segments'][0]
        segment['speech'][1]['pause_before'] = dict(
            reason='source_pause', basis='原文明示迟疑。', seconds=1.8)
        self.assertEqual(validate(plan), [])
        context = speech_context(plan)
        cue = speech_carry_prose(segment, segment['shots'][1], context)
        self.assertIn('在停顿后接续原句后文', cue)

    def test_cross_segment_render_carries_only_remaining_words(self):
        authored = long_voice()
        first = authored['segments'][0]
        second = deepcopy(first)
        second['id'] = 'S2'
        authored['segments'].append(second)
        for segment in (first, second):
            for index, shot in enumerate(segment['shots']):
                shot.update(start=index, end=index+1, start_scale=['中近景', '近景'][index],
                            end_scale=['中近景', '近景'][index])
            segment['speech'][0].update(start=0, end=2, estimated_seconds=2)
        first['speech'][0]['text'] = 'You lied to me'
        second['speech'][0]['text'] = 'and I know why.'
        plan, text = render_plan(authored)
        self.assertEqual(text.count('“You lied to me”'), 1)
        self.assertEqual(text.count('“and I know why.”'), 1)
        segment = plan['segments'][1]
        cue = speech_carry_prose(segment, segment['shots'][0], speech_context(plan))
        self.assertIn('接续同一句后文', cue)
        self.assertIn(cue, text.split('第 2 段')[1])
        review(plan, text)
        self.assertEqual(validate(plan) + validate_delivery(plan, text), [])

    def test_final_validator_independently_rejects_unexplained_gap(self):
        plan = long_voice()
        segment = plan['segments'][0]
        first = segment['speech'][0]
        second = deepcopy(first)
        first.update(text='You lied to me', end=1.8, estimated_seconds=1.6)
        second.update(text='and I know why.', start=2.2, end=3.8, estimated_seconds=1.6,
                      pause_before=dict(reason='source_pause', basis='原文明示此处迟疑。', seconds=.4))
        segment['speech'].append(second)
        derived, text = render_plan(plan)
        del derived['segments'][0]['speech'][1]['pause_before']
        review(derived, text)
        self.assertTrue(any('unexplained speech gap' in e for e in validate_delivery(derived, text)))


    def test_voice_contours_survive_reaction_cuts_in_every_mode(self):
        words = 'You lied to me and I know why.'
        contours = ('愤怒质问，起句高声，重读 lied，后半句音高上扬且咬字有力',
                    '压住哭腔解释，起句带颤声，know why 尽力说清，句尾保留哭腔')
        for mode in MODES:
            for kind in allowed_voice_kinds({'mode': mode}):
                performances = ('清晰平稳中性',) if kind == 'narration' else contours
                for performance in performances:
                    with self.subTest(mode=mode, kind=kind, performance=performance):
                        authored = mode_sample(mode)
                        source = authored['sources'][0]
                        voice = authored['segments'][0]['speech'][0]
                        source.update(kind=kind, text=words)
                        voice.update(kind=kind, text=words, end=3.8,
                                     estimated_seconds=3.6, performance=performance)
                        authored['segments'][0]['presentation']['对话氛围'] = '双方隔桌对视，原声连续。'
                        if MODES[mode]['narrative']:
                            if kind == 'narration':
                                authored['narration_sources'][0]['original_text'] = words
                            else:
                                authored['narration_sources'] = []
                                authored['narration_absence_basis'] = '测试正文只有人物声音，无独立叙事旁白'
                        original = deepcopy(authored)
                        plan, text = render_plan(authored)
                        self.assertEqual(authored, original)
                        self.assertEqual(text.count('“' + words + '”'), 1)
                        self.assertIn(performance, text)
                        segment = plan['segments'][0]
                        self.assertEqual(segment['speech'][0]['kind'], kind)
                        cue = speech_carry_prose(segment, segment['shots'][1], speech_context(plan))
                        self.assertIn('承接该句已写明的声音表演及其变化', cue)
                        self.assertNotIn('沿用原音色与语气', cue)
                        if kind != 'dialogue':
                            self.assertIn('此声不配人物口型', cue)
                        review(plan, text)
                        self.assertEqual(validate(plan) + validate_delivery(plan, text), [])

    def test_published_old_carry_texts_remain_compatible(self):
        plan, text = render_plan(long_voice())
        segment = plan['segments'][0]
        cue = speech_carry_prose(segment, segment['shots'][1], speech_context(plan))
        prefix = '本段第2秒至第3.8秒，Mara的原台词声接续同一句后文，'
        old_cues = (
            prefix + '沿用原音色与语气，声音连续。画面主体不替原说话者对口型。',
            prefix + '承接该句已写明的音色、音量、语速与气息变化，不因切镜停声或重念。'
            '反应画面不截断此声轨，画面主体不替原说话者对口型。',
        )
        for old in old_cues:
            with self.subTest(old=old):
                previous = text.replace(cue, old)
                self.assertNotEqual(previous, text)
                review(plan, previous)
                self.assertEqual(validate_delivery(plan, previous), [])

    def test_legacy_compatibility_still_binds_voice_and_picture_interval(self):
        plan, text = render_plan(long_voice())
        segment = plan['segments'][0]
        context = speech_context(plan)
        cue = speech_carry_prose(segment, segment['shots'][1], context)
        for style in ('compact-v1', 'legacy'):
            old = speech_carry_prose(segment, segment['shots'][1], context, style=style)
            for wrong in (old.replace('Mara的原台词声', 'Elias的原台词声'),
                          old.replace('第2秒至第3.8秒', '第0秒至第2秒')):
                with self.subTest(style=style, wrong=wrong):
                    broken = text.replace(cue, wrong)
                    review(plan, broken)
                    self.assertTrue(any('speech carry is absent' in e
                                        for e in validate_delivery(plan, broken)))


if __name__ == '__main__':
    unittest.main()
