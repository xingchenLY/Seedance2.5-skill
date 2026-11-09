"""Display segment IDs from source episode titles, never from opaque internal IDs."""
import re

DIGITS = dict(zip('零〇一二两三四五六七八九', (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
UNITS = {'十': 10, '百': 100, '千': 1000, '万': 10000}


def episode_number(episode):
    title = episode.get('title')
    if title is None or title == '':
        return None
    if not isinstance(title, str) or '\n' in title or '\r' in title:
        raise ValueError('episode.title: expected original 第 X 集 title')
    match = re.fullmatch(r'第\s*([^\s]+?)\s*集', title.strip())
    if not match:
        raise ValueError('episode.title: expected original 第 X 集 title')
    token = match.group(1)
    if token.isdecimal():
        value = int(token)
    elif all(c in DIGITS for c in token):
        value = int(''.join(str(DIGITS[c]) for c in token))
    elif all(c in DIGITS or c in UNITS for c in token):
        total = section = digit = 0
        for c in token:
            if c in DIGITS:
                digit = DIGITS[c]
            elif c == '万':
                total += (section + digit or 1) * 10000
                section = digit = 0
            else:
                section += (digit or 1) * UNITS[c]
                digit = 0
        value = total + section + digit
    else:
        raise ValueError('episode.title: episode number must use Arabic or Chinese numerals')
    return str(value)


def segment_labels(plan):
    episodes = {e['id']: e for e in plan['episodes']}
    counters, labels, used = {}, {}, set()
    for eid, episode in episodes.items():
        start = episode.get('segment_start', 1)
        if type(start) is not int or start < 1:
            raise ValueError('episode.segment_start: expected a positive integer')
        counters[eid] = start
    prefixes = {eid: episode_number(e) for eid, e in episodes.items()}
    for segment in plan['segments']:
        eid = segment['episode']
        prefix, number = prefixes[eid], counters[eid]
        label = f'{prefix}-{number}' if prefix is not None else str(number)
        if prefix is not None and label in used:
            raise ValueError(f'duplicate displayed segment number: {label}')
        labels[segment['id']] = label
        used.add(label)
        counters[eid] += 1
    return labels
