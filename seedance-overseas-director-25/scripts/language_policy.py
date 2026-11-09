"""Share the authored target language between speech and visible-text policies.

This validates declared languages and bound directions, not translation quality
or the language of arbitrary Latin-script prose. Those still need source review.
"""
import re

LANGUAGE = re.compile(r'[a-z]{2}(?:-[a-z0-9]{2,8})*', re.I)
NAMES = {'zh': '中文', 'en': '英语', 'es': '西班牙语', 'pt': '葡萄牙语'}


def content_language(plan):
    if 'content_language' in plan:
        value = plan['content_language']
        if not isinstance(value, str) or not LANGUAGE.fullmatch(value):
            raise ValueError('content_language: use one target language code, e.g. zh, en, es, pt or pt-BR')
        return value.lower()
    # Infer only from declared speech sources, never Chinese direction prose,
    # character labels, filenames, or arbitrary Latin-script text.
    languages = {s.get('language', '').lower()
                 for s in plan.get('sources', []) if isinstance(s.get('language'), str)}
    bases = {code.split('-')[0] for code in languages}
    if len(bases) == 1 and bases <= NAMES.keys() and all(LANGUAGE.fullmatch(code) for code in languages):
        return next(iter(languages)) if len(languages) == 1 else next(iter(bases))
    return None


def require_content_language(plan):
    code = content_language(plan)
    if code is None:
        raise ValueError('content_language: determine the target from the screenplay or user instruction; '
                         'silent/multilingual records need an explicit target, no automatic English default')
    return code


def language_clauses(plan):
    code = content_language(plan)
    if not code:
        return {}
    base = code.split('-')[0]
    name = NAMES.get(base, code)
    if '-' in code and base in NAMES:
        name += f'（{code}）'
    return {
        '声音限制': f'人物台词及本模式允许的其他原文发声统一使用{name}。',
        '画面限制': f'剧情必要文字清晰可读，统一使用{name}。',
    }


def check_language_sources(plan, errors):
    target = content_language(plan)
    if not target:
        return
    parts_by_source = {}
    for segment in plan.get('segments', []):
        for voice in segment.get('speech', []):
            parts = voice.get('language_parts')
            if isinstance(parts, list):
                parts_by_source.setdefault(voice.get('source_id'), []).extend(
                    str(part.get('language', '')).lower() for part in parts if isinstance(part, dict))
    for source in plan.get('sources', []):
        languages = [str(source.get('language', '')).lower(), *parts_by_source.get(source.get('id'), [])]
        same = all(language.split('-')[0] == target.split('-')[0]
                   and (not ('-' in language and '-' in target) or language == target)
                   for language in languages)
        basis = source.get('language_exception_basis')
        if not same and not (isinstance(basis, str) and basis.strip()):
            errors.append(f'{source.get("id", "source")}: source language differs from content_language; '
                          'restore the target language or record the original/user basis in language_exception_basis')


def check_language_presentation(plan, presentation, label, errors):
    clauses = language_clauses(plan)
    for field, clause in clauses.items():
        actual = presentation.get(field, '')
        if not isinstance(actual, str) or clause not in actual:
            errors.append(f'{label}: {field} must preserve the shared content_language clause')
        # Check affirmative policy clauses only; a prohibition is not a conflict.
        aliases = {'zh': '中文|汉语', 'en': '英文|英语', 'es': '西班牙语|西语', 'pt': '葡萄牙语|葡语'}
        target = content_language(plan).split('-')[0]
        for code, names in aliases.items():
            directive = r'(?:默认|统一|全部)(?:使用|采用|为)?(?:' + names + ')'
            positive = re.sub(r'(?:不要|不得|不能|禁止|不再|避免|不)' + directive, '', actual) if isinstance(actual, str) else ''
            if code != target and re.search(directive, positive):
                errors.append(f'{label}: {field} retains a conflicting language-default instruction for content_language')
        if field == '画面限制' and target == 'zh' and isinstance(actual, str):
            compact = re.sub(r'\s+', '', actual)
            chinese_ban = (r'(?:(?:禁止|不得|不能|不允许|不可|严禁)(?:出现|使用|显示|包含|带有)?(?:任何)?'
                           r'(?:简体或繁体|简体及繁体|简繁|简体|繁体)?(?:中文|汉字)|'
                           r'无(?:简体或繁体|简繁)?中文)(?:入画)?(?=[。；，,;！!]|$)')
            if re.search(chinese_ban, compact):
                errors.append(f'{label}: 画面限制 contains a Chinese prohibition conflicting with the Chinese target')
