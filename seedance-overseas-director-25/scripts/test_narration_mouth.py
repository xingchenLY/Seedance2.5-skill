"""Text-level regression tests; these do not assert a video's lip-sync quality."""
from copy import deepcopy
import unittest

from delivery_checks import validate_delivery
from mode_rules import MODES
from narration_mouth import ANCHOR, isolation_prose, isolation_spans
from render_prompt import render_plan
from test_delivery_checks import review
from test_modes import mode_sample
from validate_plan import validate


def overlap_sample(speaker='Elias'):
    data = mode_sample()
    segment = data['segments'][0]
    narrator = segment['speech'][0]
    narrator.update(start=0, end=2, estimated_seconds=2)
    source = deepcopy(data['sources'][0])
    source.update(id='D2', kind='dialogue', speaker=speaker, text='Wait.')
    data['sources'].append(source)
    segment['speech'].append(dict(source_id='D2', kind='dialogue', speaker=speaker, text='Wait.',
                                  start=1, end=2, estimated_seconds=1, performance='短促喊出'))
    segment['overlap_basis'] = '合成测试原文明示人物在叙述尚未结束时喊话'
    segment['frame_voice_exceptions'] = {'分镜1': dict(basis=segment['overlap_basis'],
        source_ids=[narrator['source_id'], 'D2'], lip_sync_speakers=[speaker])}
    return data


class NarrationMouthTests(unittest.TestCase):
    def assert_finding(self, errors, needle):
        self.assertTrue(any(needle in error for error in errors), errors)

    def test_first_shot_with_narrator_visible_gets_isolation_and_keeps_identity(self):
        for mode in ('推文', '3D漫推文'):
            with self.subTest(mode=mode):
                authored = mode_sample(mode)
                plan, text = render_plan(authored)
                segment = plan['segments'][0]
                shot = segment['shots'][0]
                cue = isolation_prose(segment, shot)
                self.assertTrue(cue)
                self.assertIn(cue, text.split('分镜2（')[0])
                self.assertIn('（画外叙事｜Mara）旁白参考（Mara）', text)
                self.assertEqual(plan['sources'], authored['sources'])
                self.assertEqual(segment['cast'], ['Mara', 'Elias'])
                self.assertEqual(segment['english_words'], 3)
                review(plan, text)
                self.assertEqual(validate(plan) + validate_delivery(plan, text), [])

    def test_inner_uses_nonspoken_identity_and_preserves_emotion(self):
        for mode in ('推文', '3D漫推文'):
            data = mode_sample(mode)
            data['sources'][0]['kind'] = data['segments'][0]['speech'][0]['kind'] = 'inner'
            data['segments'][0]['speech'][0]['performance'] = '愤怒哽咽，声音发紧'
            data.update(narration_sources=[], narration_absence_basis='合成测试仅有内心声')
            plan, text = render_plan(data)
            self.assertIn('（内心声｜Mara）旁白参考', text)
            self.assertIn('愤怒哽咽', text)
            self.assertNotIn(ANCHOR['narration'], text)
            review(plan, text)
            self.assertEqual(validate_delivery(plan, text), [])

    def test_authorized_dialogue_and_drama_voices_do_not_receive_silent_constraints(self):
        for mode in MODES:
            for kind in (('dialogue',) if MODES[mode]['narrative'] else ('dialogue', 'VO', 'OS')):
                data = mode_sample(mode)
                data['sources'][0]['kind'] = data['segments'][0]['speech'][0]['kind'] = kind
                if MODES[mode]['narrative']:
                    data.update(narration_sources=[], narration_absence_basis='合成测试仅有对白')
                plan, text = render_plan(data)
                self.assertNotIn('保持无声表演', text)
                self.assertNotIn('画外叙事｜', text)
                review(plan, text)
                self.assertEqual(validate_delivery(plan, text), [])

    def test_authored_speaking_in_narration_only_picture_is_rejected(self):
        for text in ('Mara开口质问，口型同步。', 'Elias开口说话。',
                     'Mara嘴唇随旁白变化。', '画内人物全体跟读旁白。'):
            data = mode_sample()
            data['segments'][0]['shots'][0]['visual_text'] += text
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'speaking directions|driven by narrative'):
                render_plan(data)

    def test_voice_performance_cannot_request_lips(self):
        for text in ('中性平稳，人物口型同步', '中性平稳，Mara开口说出旁白', '中性平稳，嘴唇随旁白变化'):
            data = mode_sample()
            data['segments'][0]['speech'][0]['performance'] = text
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'must not direct on-screen speech'):
                render_plan(data)

    def test_negative_instructions_and_natural_nonverbal_mouth_actions_are_allowed(self):
        for text in ('Mara没有开口说话，嘴唇自然合拢。', 'Mara不跟随旁白做口型。',
                     'Mara嘴唇不随旁白变化。', 'Mara微笑后抿唇，按原有动作张口喘气。'):
            data = mode_sample()
            data['segments'][0]['shots'][0]['visual_text'] += text
            with self.subTest(text=text):
                plan, final = render_plan(data)
                review(plan, final)
                self.assertEqual(validate_delivery(plan, final), [])

    def test_final_positive_conflict_is_checked_even_with_new_review_hash(self):
        plan, text = render_plan(mode_sample())
        changed = text.replace('画面内容：0–2 秒 ', '画面内容：0–2 秒 Mara开口讲话，口型同步。', 1)
        review(plan, changed)
        self.assert_finding(validate_delivery(plan, changed), 'speaking directions')

    def test_final_voice_anchor_or_prefix_cannot_be_removed_or_negated(self):
        plan, text = render_plan(mode_sample())
        mutations = [text.replace('画外叙事｜', ''), text.replace(ANCHOR['narration'], ''),
                     text.replace(ANCHOR['narration'], '不是' + ANCHOR['narration'])]
        for changed in mutations:
            review(plan, changed)
            self.assertTrue(validate_delivery(plan, changed))

    def test_final_mouth_instruction_must_stay_in_its_own_visual_interval(self):
        plan, text = render_plan(mode_sample())
        cue = isolation_prose(plan['segments'][0], plan['segments'][0]['shots'][0])
        for replacement in ('', '画面人物不对旁白做口型。'):
            changed = text.replace(cue, replacement)
            review(plan, changed)
            self.assert_finding(validate_delivery(plan, changed), 'isolation is absent')
        changed = text.replace(cue, '').replace('画面内容：2–4 秒 ', '画面内容：2–4 秒 ' + cue)
        review(plan, changed)
        self.assert_finding(validate_delivery(plan, changed), 'isolation is absent')

    def test_overlap_allocates_lips_only_for_actual_dialogue_in_its_time_window(self):
        for speaker in ('Mara', 'Elias'):
            data = overlap_sample(speaker)
            s = data['segments'][0]
            s['shots'][0]['visual_text'] += f'第1秒至第2秒，{speaker}开口说话，口型同步。'
            plan, text = render_plan(data)
            s = plan['segments'][0]
            spans = isolation_spans(s, s['shots'][0])
            self.assertEqual(spans, [(0, 1, ('narration',), ()), (1, 2, ('narration',), (speaker,))])
            self.assertIn(f'画内仅{speaker}按本人同期台词讲话', text)
            review(plan, text)
            self.assertEqual(validate_delivery(plan, text), [])
            s['shots'][0]['visual_text'] = f'第0秒至第1秒，{speaker}开口说话。'
            self.assert_finding(validate(plan), 'speaking directions')

    def test_narration_then_dialogue_does_not_keep_person_silent_in_next_shot(self):
        data = mode_sample()
        segment = data['segments'][0]
        data['sources'].append(dict(id='D2', episode=segment['episode'], kind='dialogue', speaker='Mara',
                                    language='en', text='Wait.'))
        segment['speech'].append(dict(source_id='D2', kind='dialogue', speaker='Mara', text='Wait.',
                                      start=2, end=3, estimated_seconds=1, performance='自然说出'))
        segment['shots'][1]['visual_text'] += 'Mara开口说话。'
        plan, text = render_plan(data)
        self.assertEqual(isolation_prose(plan['segments'][0], plan['segments'][0]['shots'][1]), '')
        self.assertIn('（Mara）台词参考', text)
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])

    def test_narration_crosses_frames_once_but_every_covered_shot_has_isolation(self):
        data = mode_sample()
        data['segments'][0]['speech'][0].update(start=0, end=4, estimated_seconds=4)
        plan, text = render_plan(data)
        self.assertEqual(text.count('旁白参考'), 1)
        self.assertEqual(text.count('“Put it down.”'), 1)
        for shot in plan['segments'][0]['shots']:
            self.assertIn(isolation_prose(plan['segments'][0], shot), text)
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])

    def test_spoken_source_words_are_not_scanned_as_mouth_instructions(self):
        data = mode_sample()
        source = '她开口讲话，口型同步。'
        data['sources'][0].update(text=source, language='zh')
        data['narration_sources'][0].update(original_text=source, source_language='zh')
        data['segments'][0]['speech'][0]['text'] = source
        data['segments'][0]['speech'][0].update(estimated_seconds=1.3, end=1.5)
        plan, text = render_plan(data)
        self.assertIn('“' + source + '”', text)
        review(plan, text)
        self.assertEqual(validate_delivery(plan, text), [])

    def test_empty_insert_does_not_invent_a_visible_speaker(self):
        segment = mode_sample()['segments'][0]
        shot = dict(start=0, end=2, visible_characters=[])
        cue = isolation_prose(segment, shot)
        self.assertIn('没有可见人物', cue)
        self.assertNotIn('画内Mara', cue)


if __name__ == '__main__':
    unittest.main()
