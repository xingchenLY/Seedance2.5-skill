"""Flag concrete facial-color directions and bind permitted exceptions to evidence.

This is a limited Chinese-direction scan, not image analysis or a proof that the
author's source quotation is true. Spoken lines and literal prop text are kept.
"""
import re
from decimal import Decimal

from review_evidence import concrete_note

PART = r'面部毛发|脸部毛发|整张脸|整脸|满脸|面部|脸部|脸色|面色|肤色|唇色|双唇|嘴唇|脸颊|面颊|双颊|面孔|毛色|眼眶|眼周|眼睑|眼皮|眼白|虹膜|瞳孔|眼球|双眼|眼睛|眼部|脸'
COLOR = r'惨白|煞白|苍白|骤白|发白|变白|泛白|失去(?:了)?血色|毫无血色|不见血色|血色(?:尽失|褪尽|全无)|涨红|通红|血红|鲜红|赤红|猩红|发红|泛红|变红|红晕|泛青|铁青|发青|发紫|变紫|变黑|发黑|褪色|变色|染红|染白|染黑|充血|血丝|发光|(?:变成|变为|呈现|化为|染成)(?:了)?(?:红|白|青|紫|黑|蓝|绿|金|银)色?'
CHANGE = re.compile(r'(?P<part>' + PART + r')(?P<bridge>[^，,。；;！？!?\n]{0,12}?)(?P<color>' + COLOR + r')')
NEGATIVE = re.compile(r'(?:不要|不得|不能|禁止|避免|不再|没有|不|未|无)(?:让|使|将|把|其|她们|他们|它们|她|他|它|人物|角色|的|任何|明显|再|突然|直接|出现|呈现|变得|变成)*$')
SUBTLE = re.compile(r'轻微|微微|略微|稍微|淡淡|淡红|一丝|少许')
INTENSE = re.compile(r'明显|强烈|鲜艳|大面积|整个|整片|整圈|非常|极其|通红|鲜红|血红|猩红|充血|血丝')
KINDS = {'source_state', 'source_vfx', 'existing_appearance', 'lighting', 'user_requested'}
VAGUE = {'有视效', '有特效', '视效剧本', '特效剧本', '原文有视效', '原文有特效',
         '加强情绪', '按原文', '原文明示', '原文明确', '参考图', '根据剧情'}


def source_note(value):
    return concrete_note(value) and re.sub(r'[\W_]+', '', value) not in VAGUE


def scope_time(value):
    result = format(Decimal(str(value)), 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def mask_literal_text(value):
    """Only quoted text explicitly displayed on a carrier, not quoted directions."""
    chars = list(value)
    for match in re.finditer(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"', value):
        prefix = re.split(r'[。；;\n]', value[:match.start()])[-1]
        carrier = re.search(r'屏幕|短信|信件|纸条|标牌|招牌|文件|海报|按钮|评论|字幕|碑文|书页', prefix)
        displayed = re.search(r'(?:显示|写着|写有|印着|印有|刻着|刻有|内容为|文字为)[：:\s]*$', prefix)
        if carrier and displayed:
            chars[match.start():match.end()] = ' ' * (match.end() - match.start())
    return ''.join(chars)


def findings(value):
    text = mask_literal_text(value)
    for match in CHANGE.finditer(text):
        prefix = re.split(r'[，,。；;！？!?\n]', text[:match.start()])[-1]
        bridge, color = match.group('bridge', 'color')
        if NEGATIVE.search(prefix) or NEGATIVE.search(bridge):
            continue
        # Mild permission is local to cheeks/eye rims, never iris or sclera.
        local = prefix[-6:] + match.group(0)
        mild = (match.group('part') in {'脸颊', '面颊', '双颊', '眼眶'}
                and color in {'发红', '泛红', '红晕'} and SUBTLE.search(local)
                and not INTENSE.search(local))
        if not mild:
            yield match


def check_appearance(segment, scopes, errors):
    """Check rendered, scope-addressed prose; never mutate it or auto-review it."""
    label = segment['id']
    rows = segment.get('appearance_reviews', [])
    if not isinstance(rows, list):
        errors.append(f'{label}: appearance_reviews must be a list')
        rows = []
    valid = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            errors.append(f'{label}: appearance review must be an object')
            continue
        required = ('scope', 'evidence', 'subject', 'kind', 'basis', 'source_ref', 'source_evidence')
        if any(not source_note(row.get(k)) for k in required) or row.get('kind') not in KINDS:
            errors.append(f'{label}: appearance review needs scope, exact evidence, subject, supported kind and concrete source/basis')
            continue
        scope, evidence = row['scope'], row['evidence']
        key = (scope, evidence)
        if key in seen:
            errors.append(f'{label}: duplicate appearance review')
            continue
        seen.add(key)
        if scope not in scopes or evidence not in scopes[scope] or row['subject'] not in evidence:
            errors.append(f'{label}/{scope}: appearance evidence must name its subject and occur in this exact final scope')
            continue
        valid.append(row)
    for scope, text in scopes.items():
        for match in findings(text):
            covered = False
            for row in valid:
                if row['scope'] != scope:
                    continue
                for occurrence in re.finditer(re.escape(row['evidence']), text):
                    if occurrence.start() <= match.start() and match.end() <= occurrence.end():
                        covered = True
            if not covered:
                errors.append(f'{label}/{scope}: facial appearance REVIEW for {match.group(0)!r}; '
                              'use supported subtle cheek/eye-rim wording or supply source-bound appearance_reviews')
