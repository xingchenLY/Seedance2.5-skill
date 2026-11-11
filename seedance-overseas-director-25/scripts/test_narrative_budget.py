from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from narrative_budget import lock_plan, check_narrative_budgets
from render_prompt import prepare_plan
from shot_pacing import check_segment_duration
from validate_plan import validate_timing_preflight


def sample(text='字'*75, language='zh', mode='推文'):
    return dict(mode=mode, sources=[dict(id='v1', language=language, text=text,
        kind='narration', speaker='她', episode='E1')],
        segments=[dict(id='S1', speech=[dict(source_id='v1', text=text)])])


def budgeted_fixture(plan, text):
    """Give a synthetic CLI-review fixture source text fitting its shot window."""
    old_text = plan['sources'][0]['text']
    def replace(value):
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        return text if value == old_text else value
    result = replace(plan)
    segment = result['segments'][0]
    duration = segment['duration']
    shots = segment.pop('shots')
    segment['speech'][0].update(start=0, end=duration, estimated_seconds=duration)
    result = lock_plan(result)
    assert result['segments'][0]['duration'] == duration
    result['segments'][0]['shots'] = shots
    return result


class NarrativeBudgetTests(unittest.TestCase):
    def test_chinese_75_chars_locks_eleven_in_both_modes(self):
        for mode in ('推文','3D漫推文'):
            result=lock_plan(sample(mode=mode))
            s=result['segments'][0]
            self.assertAlmostEqual(s['pre_round_duration'],75/7)
            self.assertEqual(s['duration'],11)
            self.assertEqual(check_narrative_budgets(result,True),[])
            self.assertEqual(validate_timing_preflight(result),[])

    def test_english_96_words_locks_29_not_thirty(self):
        s=lock_plan(sample('word '*96,'en'))['segments'][0]
        self.assertAlmostEqual(s['pre_round_duration'],96/3.4)
        self.assertEqual(s['duration'],29)

    def test_whole_total_not_individual_rounding(self):
        p=sample('字'*25)
        p['segments'][0]['speech']=[dict(source_id='v1',text='字'*5) for _ in range(5)]
        self.assertEqual(lock_plan(p)['segments'][0]['duration'],4)

    def test_mixed_language_counts_each_part_once(self):
        p=sample('一二三四五六七 '+ 'word '*17,'mul')
        p['segments'][0]['speech'][0]['language_parts']=[dict(language='zh',text='一二三四五六七'),dict(language='en',text='word '*17)]
        self.assertEqual(lock_plan(p)['segments'][0]['duration'],6)

    def test_numbers_use_spoken_reading(self):
        p=sample('付20元。'+'字'*21)
        p['segments'][0]['speech'][0]['number_expansions']=[dict(start=1,end=3,spoken='二十')]
        s=lock_plan(p)['segments'][0]
        self.assertAlmostEqual(s['pre_round_duration'],25/7)
        self.assertEqual(s['duration'],4)

    def test_spanish_and_portuguese_use_same_word_rate_as_english(self):
        for lang in ('en','es','pt','pt-BR','es-ES'):
            p=sample('palabra '*34,lang)
            p['segments'][0]['speech'][0]['estimated_seconds']=28
            self.assertEqual(lock_plan(p)['segments'][0]['duration'],10)

    def test_mixed_estimates_cannot_override_unified_word_rate(self):
        for lang in ('en','es','pt'):
            p=sample('一二三四五六七 '+ 'texto '*17,'mul')
            p['segments'][0]['speech'][0]['language_parts']=[
                dict(language='zh',text='一二三四五六七'),
                dict(language=lang,text='texto '*17,estimated_seconds=20)]
            self.assertEqual(lock_plan(p)['segments'][0]['duration'],6)

    def test_spanish_portuguese_numbers_expand_without_entering_english_cap(self):
        from validate_plan import speech_english_words
        for lang,text,spoken in [('es','Tengo 20 euros. '+'texto '*10,'veinte'),('pt','Tenho 20 euros. '+'texto '*10,'vinte')]:
            p=sample(text,lang);voice=p['segments'][0]['speech'][0]
            with self.assertRaisesRegex(ValueError,'expand spoken numbers'):lock_plan(p)
            voice['number_expansions']=[dict(start=6,end=8,spoken=spoken)]
            self.assertAlmostEqual(lock_plan(p)['segments'][0]['pre_round_duration'],13/3.4)
            errors=[]
            self.assertEqual(speech_english_words(voice,lang,'test',errors),0)
            self.assertEqual(errors,[])

    def test_word_counts_preserve_accents_and_punctuation(self):
        from chinese_timing import word_rate_seconds
        for lang,text,count in [('es','¿Por qué? ¡Qué alegría!',4),('pt','Não, por quê? Está bem.',5)]:
            errors=[]
            self.assertAlmostEqual(word_rate_seconds(dict(text=text),lang,'test',errors),count/3.4)
            self.assertEqual(errors,[])

    def test_actual_audio_before_lock_is_authoritative(self):
        p=sample()
        p['segments'][0]['speech'][0].update(measured_seconds=12.8,measurement_basis='test recording observed duration')
        self.assertEqual(lock_plan(p)['segments'][0]['duration'],13)

    def test_explicit_speech_pause_is_counted_once(self):
        p=sample('字'*28)
        p['segments'][0]['speech'][0].update(start=0,end=4)
        p['segments'][0]['narrative_pauses']=[dict(start=4,end=4.4,basis='source requires this silence')]
        result=lock_plan(p)
        self.assertEqual(result['segments'][0]['duration'],5)
        p['segments'][0]['narrative_pauses'][0].update(start=1,end=1.4)
        with self.assertRaisesRegex(ValueError,'overlaps speech'):lock_plan(p)

    def test_included_chinese_pause_cannot_duplicate_measured_audio(self):
        p=sample()
        p['segments'][0]['speech'][0].update(measured_seconds=12.8,measurement_basis='recording',chinese_timing=dict(pause_seconds=.3,basis='source'))
        with self.assertRaisesRegex(ValueError,'already includes pauses'):lock_plan(p)

    def test_over_limit_or_silent_input_rejected_before_shots(self):
        for p in [sample('字'*211),sample('word '*97,'en')]:
            with self.assertRaisesRegex(ValueError,'regroup'):lock_plan(p)
        p=sample();p['segments'][0]['speech']=[]
        with self.assertRaisesRegex(ValueError,'no speech'):lock_plan(p)

    def test_existing_limit_overrides_can_tighten_but_not_relax_range(self):
        p=sample();p['limits']=dict(segment_seconds=10)
        with self.assertRaises((ValueError,KeyError)):lock_plan(p)
        p['limits_basis']='synthetic explicit user limit'
        with self.assertRaisesRegex(ValueError,'regroup'):lock_plan(p)
        p=sample('字'*217);p.update(limits=dict(segment_seconds=32),limits_basis='synthetic explicit user limit')
        with self.assertRaisesRegex(ValueError,'4-30'):lock_plan(p)
        p['sources'][0]['text']='字'*210
        p['segments'][0]['speech'][0]['text']='字'*210
        self.assertEqual(lock_plan(p)['segments'][0]['duration'],30)

    def test_no_in_place_mutation_and_no_post_shot_lock(self):
        p=sample();before=deepcopy(p);result=lock_plan(p)
        self.assertEqual(p,before)
        with self.assertRaisesRegex(ValueError,'before shots'):lock_plan(result)
        p['segments'][0]['shots']=[dict(start=0,end=11)]
        with self.assertRaisesRegex(ValueError,'before shots'):lock_plan(p)

    def test_changed_text_or_source_invalidates_lock(self):
        for field in ('voice','source'):
            p=lock_plan(sample())
            target=p['segments'][0]['speech'][0] if field=='voice' else p['sources'][0]
            target['text']='改'+target['text'][1:]
            self.assertTrue(any('stale' in e for e in check_narrative_budgets(p,True)))

    def test_duration_raw_and_shot_drift_rejected(self):
        for update in [dict(duration=12),dict(pre_round_duration=11),dict(shots=[dict(start=0,end=12)])]:
            p=lock_plan(sample());p['segments'][0].update(update)
            self.assertTrue(check_narrative_budgets(p,True))
            with self.assertRaises(ValueError):prepare_plan(p)

    def test_cuts_do_not_change_budget(self):
        p=lock_plan(sample())
        p['segments'][0]['shots']=[dict(start=0,end=3),dict(start=3,end=7),dict(start=7,end=11)]
        self.assertEqual(check_narrative_budgets(p,True),[])

    def test_floor_remains_for_drama_and_ceil_for_locked_narrative(self):
        errors=[]
        check_segment_duration(dict(duration=12,pre_round_duration=12.8),'drama',errors,True)
        self.assertEqual(errors,[])
        p=lock_plan(sample());errors=[]
        check_segment_duration(p['segments'][0],'narrative',errors,True)
        self.assertEqual(errors,[])
        p['mode']='真人剧'
        self.assertTrue(any('only' in e for e in check_narrative_budgets(p,True)))

    def test_missing_lock_is_required_only_in_public_path(self):
        p=sample()
        self.assertEqual(check_narrative_budgets(p),[])
        self.assertTrue(check_narrative_budgets(p,True))

    def test_minimum_visual_time_cannot_enlarge_locked_duration(self):
        p=lock_plan(sample('字'*28))
        p['segments'][0]['timing_feasibility']=dict(minimum_seconds=5,basis='irreducible original action')
        self.assertTrue(any('infeasible' in e for e in validate_timing_preflight(p)))
        self.assertEqual(p['segments'][0]['duration'],4)

    def test_four_to_thirty_second_boundaries_in_both_narrative_modes(self):
        for mode in ('推文','3D漫推文'):
            for count,expected in ((22,4),(28,4),(210,30)):
                self.assertEqual(lock_plan(sample('字'*count,mode=mode))['segments'][0]['duration'],expected)
            for count in (1,21,211):
                p=sample('字'*count,mode=mode);before=deepcopy(p)
                with self.assertRaisesRegex(ValueError,'regroup'):lock_plan(p)
                self.assertEqual(p,before)

    def test_short_fragments_can_be_grouped_without_individual_duration_padding(self):
        text='她打开门看见桌上放着一封信。她拿起信封发现没有署名。'
        p=sample(text)
        p['segments'][0]['speech']=[dict(source_id='v1',text=part)
                                   for part in ('她打开门看见桌上放着一封信。','她拿起信封发现没有署名。')]
        result=lock_plan(p)
        self.assertEqual(result['segments'][0]['duration'],4)
        self.assertEqual(result['sources'],p['sources'])
        self.assertEqual(result['segments'][0]['speech'],p['segments'][0]['speech'])

    def test_old_out_of_range_records_and_missing_locks_are_rejected(self):
        for duration in (3,31):
            p=sample();p['segments'][0]['duration']=duration
            self.assertTrue(any('4-30' in e for e in check_narrative_budgets(p)))
            self.assertTrue(any('4-30' in e for e in validate_timing_preflight(p)))
        self.assertTrue(any('narrative_budget required' in e for e in validate_timing_preflight(sample())))

    def test_short_shots_remain_allowed_inside_valid_segment(self):
        p=lock_plan(sample('字'*28))
        p['segments'][0]['shots']=[dict(start=i,end=i+1) for i in range(4)]
        self.assertEqual(check_narrative_budgets(p,True),[])

    def test_word_rate_boundary_does_not_round_short_groups_up_to_four(self):
        for language in ('en','es','pt'):
            with self.assertRaisesRegex(ValueError,'below 4'):lock_plan(sample('texto '*10,language))
            self.assertEqual(lock_plan(sample('texto '*11,language))['segments'][0]['duration'],4)

    def test_full_validation_and_delivery_reject_out_of_range_old_records(self):
        from test_modes import mode_sample
        from render_prompt import render_plan
        from validate_plan import validate
        from delivery_checks import validate_delivery
        for mode in ('推文','3D漫推文'):
            plan,text=render_plan(mode_sample(mode))
            for duration in (3,31):
                broken=deepcopy(plan);broken['segments'][0]['duration']=duration
                self.assertTrue(any('4-30' in e for e in validate(broken)))
                self.assertTrue(any('4-30' in e for e in validate_delivery(broken,text,require_review=False)))

    def test_public_cli_rejects_short_group_and_tampered_segment_without_output(self):
        root=Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);src=path/'input.json';out=path/'locked.json'
            call=lambda *args:subprocess.run([sys.executable,'-B','-X','utf8',*map(str,args)],
                capture_output=True,text=True,encoding='utf-8')
            src.write_text(json.dumps(sample('字'*21),ensure_ascii=False),encoding='utf-8')
            result=call(root/'narrative_budget.py',src,'--output',out)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('below 4',result.stderr)
            self.assertFalse(out.exists())
            for duration in (3,31):
                plan=lock_plan(sample());plan['segments'][0]['duration']=duration
                src.write_text(json.dumps(plan,ensure_ascii=False),encoding='utf-8')
                result=call(root/'validate_plan.py',src,'--timing-only')
                self.assertNotEqual(result.returncode,0)
                self.assertIn('4-30',result.stdout+result.stderr)
                result=call(root/'render_prompt.py',src,'--output',path/'prompt.txt','--plan-output',path/'plan.json')
                self.assertNotEqual(result.returncode,0)
                self.assertIn('4-30',result.stdout+result.stderr)
                self.assertFalse((path/'prompt.txt').exists())

    def test_real_cli_and_timing_only_preflight(self):
        root=Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'input.json';out=Path(d)/'locked.json'
            src.write_text(json.dumps(sample(),ensure_ascii=False),encoding='utf-8')
            call=lambda *args:subprocess.run([sys.executable,'-B','-X','utf8',*map(str,args)],capture_output=True,text=True,encoding='utf-8')
            result=call(root/'narrative_budget.py',src,'--output',out)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=call(root/'validate_plan.py',out,'--timing-only')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            result=call(root/'validate_plan.py',src,'--timing-only')
            self.assertNotEqual(result.returncode,0)
            result=call(root/'render_prompt.py',src,'--output',Path(d)/'prompt.txt','--plan-output',Path(d)/'plan.json')
            self.assertNotEqual(result.returncode,0)
            self.assertIn('narrative_budget',result.stderr)
            self.assertFalse((Path(d)/'prompt.txt').exists())


if __name__=='__main__':unittest.main()
