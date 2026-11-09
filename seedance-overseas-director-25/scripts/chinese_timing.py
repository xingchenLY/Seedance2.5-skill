"""Chinese speech uses seven spoken Han characters per second, independently.

This calculates estimates from authored readings, not measured audio or an NLP
guess of numeral pronunciation. Other-language-only speech is left unchanged.
"""
import math
import unicodedata

RATE = 7
EPS = 1e-7


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def chinese_characters(text):
    return sum(char == '〇' or unicodedata.name(char, '').startswith(
        ('CJK UNIFIED IDEOGRAPH', 'CJK COMPATIBILITY IDEOGRAPH')) for char in text)


def leaves(record, language):
    if 'language_parts' not in record:
        return [(record, language.lower().split('-')[0])]
    from validate_plan import require_list, require_object, string
    result = []
    for part in require_list(record['language_parts'], 'language_parts'):
        require_object(part, 'language part')
        result.extend(leaves(part, string(part, 'language')))
    return result


def chinese_speech_seconds(speech, language, label, errors):
    """Return the full event estimate when it includes Chinese, otherwise None."""
    from validate_plan import counting_form, speech_english_words, string
    parts = leaves(speech, language)
    if not any(code == 'zh' for _, code in parts):
        return None
    total = 0.0
    for part, code in parts:
        if code == 'zh':
            text = counting_form(part, string(part, 'text'), label, errors)
            total += chinese_characters(text) / RATE
        elif part is not speech:
            supplied = part.get('estimated_seconds')
            if supplied is not None:
                if not finite(supplied) or supplied <= 0:
                    errors.append(f'{label}: non-Chinese language part needs positive estimated_seconds')
                else:
                    total += supplied
            elif code == 'en':
                total += speech_english_words(part, code, label, errors) / 3.4
            else:
                errors.append(f'{label}: {code} part needs its own estimated_seconds; do not use Chinese 7 chars/s')
    adjustment = speech.get('chinese_timing', {})
    if (not isinstance(adjustment, dict) or set(adjustment) - {'pause_seconds', 'basis'}):
        errors.append(f'{label}: chinese_timing accepts only pause_seconds and its concrete basis')
        adjustment = {}
    pause = adjustment.get('pause_seconds', 0)
    if not finite(pause) or pause < 0:
        errors.append(f'{label}: Chinese pause_seconds must be finite and nonnegative')
        pause = 0
    if adjustment and (pause <= 0 or not isinstance(adjustment.get('basis'), str) or not adjustment['basis'].strip()):
        errors.append(f'{label}: Chinese pause adjustment needs positive seconds and a source/performance basis')
    total += pause
    # Reuse existing measured-audio records instead of inventing a second proof.
    measured = speech.get('measured_seconds')
    review = speech.get('timing_review')
    if adjustment and (measured is not None or isinstance(review, dict) and review.get('basis') == 'measured_audio'):
        errors.append(f'{label}: measured audio already includes pauses; remove separate chinese_timing adjustment')
    if measured is not None:
        if (not finite(measured) or measured <= 0 or not isinstance(speech.get('measurement_basis'), str)
                or not speech['measurement_basis'].strip()):
            errors.append(f'{label}: Chinese measured timing needs actual seconds and measurement_basis')
        else:
            total = measured
    elif isinstance(review, dict) and review.get('basis') == 'measured_audio':
        if (not finite(review.get('seconds')) or review['seconds'] <= 0
                or any(not isinstance(review.get(k), str) or not review[k].strip() for k in ('source', 'measurement'))):
            errors.append(f'{label}: Chinese measured timing needs source, measurement and actual seconds')
        else:
            total = review['seconds']
    if total <= 0:
        errors.append(f'{label}: Chinese speech has no counted spoken content; do not create a punctuation-only voice')
    return total


def check_chinese_timing(speech, language, label, errors):
    expected = chinese_speech_seconds(speech, language, label, errors)
    if expected is None:
        return
    # Allow an upward 0.1-second scheduling grid, never arbitrary padding or
    # shortening. A cut does not create another speech event or another charge.
    for key, value in (('estimated_seconds', speech['estimated_seconds']),
                       ('voice interval', speech['end'] - speech['start'])):
        if value < expected - EPS or value >= expected + 0.1 - EPS:
            errors.append(f'{label}: Chinese {key} must follow 7 chars/s plus declared pauses '
                          f'or measured audio ({expected:.6g}s), with less than 0.1s upward rounding')
