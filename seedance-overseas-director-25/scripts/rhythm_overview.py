"""Derive an internal, nonjudgmental shot overview from a validated plan.

No scheduling, narrative inference, extra review receipts or prompt mutations.
Generation order and optional edit references are deliberately distinguished.
"""
from decimal import Decimal

from camera_views import endpoint_view
from shot_pacing import fmt


def cell(value):
    """Keep authored labels on one Markdown row without truncating their identity."""
    return ' '.join(str(value).split()).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '&#124;')


def change(start, end):
    return start if start == end else f'{start} → {end}'


def subject(shot, endpoint):
    view = endpoint_view(shot, endpoint)
    if not isinstance(view, dict) or 'subject' not in view:
        return '未记录'
    return '无主拍人物' if view['subject'] is None else cell(view['subject'])


def render_rhythm_overview(plan):
    """Read an already validated plan; return derived Markdown, never edit it."""
    rows = [
        '# 内部节奏总览', '',
        '按生成段顺序列实际镜头；区间为原段局部秒数。仅供定位，不是节奏通过结论。',
        '主拍人物不是全部可见人物；未记录不代表无人，观看目的须结合实际画面判断。', '',
    ]
    for segment in plan['segments']:
        rows += [f'## {cell(segment["episode"])} / {cell(segment["id"])}', '',
                 '| 实际镜头（标题） | 区间／镜长（秒） | 起止景别 | 主拍人物 |',
                 '| --- | --- | --- | --- |']
        total = Decimal(0)
        for index, shot in enumerate(segment['shots'], 1):
            duration = Decimal(str(shot['end'])) - Decimal(str(shot['start']))
            total += duration
            scale = change(cell(shot.get('start_scale', '未记录')),
                           cell(shot.get('end_scale', '未记录')))
            focus = change(subject(shot, 'start'), subject(shot, 'end'))
            title = cell(shot.get('frame_id', '未记录'))
            rows.append(f'| {index}（{title}） | {fmt(shot["start"])}–{fmt(shot["end"])} / {fmt(duration)} | {scale} | {focus} |')
        rows += ['', f'本生成段实际镜长合计：{fmt(total)}秒（同期声音不另加秒）。', '']
    if 'playback' in plan:
        rows += ['## 剪辑引用顺序', '', '按已有 playback 引用，区间仍是原段局部秒数：', '']
        for index, clip in enumerate(plan['playback'], 1):
            rows.append(f'{index}. {cell(clip["segment_id"])}：{fmt(clip["start"])}–{fmt(clip["end"])}秒')
        rows.append('')
    return '\n'.join(rows)
