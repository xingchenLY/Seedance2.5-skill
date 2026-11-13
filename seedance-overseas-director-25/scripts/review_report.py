"""Group existing content-review findings without changing checks or the prompt.

Run after structural validation. Raw findings and cross-shot order are retained;
report-local IDs are navigation aids, never cached review receipts.
"""
from copy import deepcopy
import hashlib
import json
from narrative_framing import review_narrative_framing
from live_visual_pacing import review_live_visual_pacing
from narrative_visual_pacing import review_narrative_visual_pacing, KINDS
from timing_overlap_review import review_timing_overlap, gap_summary
from shoulder_axis_review import review_shoulder_axis
from scene_presence_review import review_scene_presence
from shot_pacing import fmt
from shot_sequence_review import review_shot_sequence

TOPICS = {
    'shot_sequence': ('Q', '镜头排列：核对相似切点、返回构图的新变化及双人镜观看重点；正常反打可以保留，不能以改写机位或重复反应过检。'),
    'scene_presence': ('P', '在场未入画：核对既定站位与真实取景／遮挡；应可见则补回已有画面，合理画外可保留，不强制同框、移人、补镜或加时。'),
    'shoulder_axis': ('O', '过肩疑点：核对人物自身肩侧、画面侧与真实轴线；移动／镜面等按实际几何复核，不自动左右互换。'),
    'broad_narrative_run': ('B', '连续偏宽：核对空间／身体任务与情绪细节，不设近景配额。'),
    'narrative_task_readability': ('D', '信息可读性：结合原文证据核对实际取景。'),
    'live_visual_pacing': ('L', '真人纯画面：短镜同查，3秒不是默认镜长或硬上限。'),
    'narrative_visual_pacing': ('N', '推文全镜：短镜同查正常动作／阅读／情绪落点及后续内容，不慢放、重演或提前揭露填时。'),
    'timing_overlap': ('T', '同期：未覆盖不等于可删；核对任务与先后依据，保留必要沉默／情绪／接触／阅读。'),
}


def collect_review_report(plan):
    """Run each original locator once; return lossless items and a shot index."""
    topics = (
        ('shot_sequence_review.py', review_shot_sequence(plan)),
        ('scene_presence_review.py', review_scene_presence(plan)),
        ('shoulder_axis_review.py', review_shoulder_axis(plan)),
        ('narrative_framing.py', review_narrative_framing(plan)),
        ('live_visual_pacing.py', review_live_visual_pacing(plan)),
        ('narrative_visual_pacing.py', review_narrative_visual_pacing(plan)),
        ('timing_overlap_review.py', review_timing_overlap(plan)),
    )
    items, by_shot = [], {}
    for source, findings in topics:
        for finding in findings:
            code = finding.get('code', source.removesuffix('.py').removesuffix('_review'))
            if source in ('narrative_framing.py', 'shoulder_axis_review.py', 'scene_presence_review.py', 'shot_sequence_review.py'):
                locations = [(s['segment_id'], s['start']) for s in finding['shots']]
            elif source == 'timing_overlap_review.py':
                locations = [(finding['segment_id'], start)
                             for gap in finding['uncovered_ranges'] for start in gap['shot_starts']]
            else:
                locations = [(finding['segment_id'], finding['shot_start'])]
            # Only index duplicate links once; never deduplicate distinct findings.
            locations = list(dict.fromkeys(locations))
            item = dict(id=f'R{len(items)+1}', code=code, source=source,
                        finding=deepcopy(finding), locations=locations)
            raw = json.dumps({k: v for k, v in item.items() if k != 'id'},
                             ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            item['key'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]
            items.append(item)
            for location in locations:
                by_shot.setdefault(location, []).append(item['id'])
    groups = [dict(segment_id=s['id'], start=shot['start'], end=shot['end'],
                   items=by_shot[(s['id'], shot['start'])])
              for s in plan['segments'] for shot in s['shots']
              if (s['id'], shot['start']) in by_shot]
    return dict(items=items, groups=groups)


def finding_text(item):
    """Keep meaningful ranges and source evidence while removing repeated boilerplate."""
    data, code = item['finding'], item['code']
    if code == 'shot_sequence':
        chain = ' → '.join(f"{s['segment_id']}@{fmt(s['start'])}–{fmt(s['end'])}秒" for s in data['shots'])
        return chain + '；' + data['reason']
    if code == 'scene_presence':
        chain = ' → '.join(f"{s['segment_id']}@{fmt(s['start'])}–{fmt(s['end'])}秒" for s in data['shots'])
        return (f"{'、'.join(data['characters'])}在本次可检查区间的在场表中持续存在，但无可见记录；"
                f"场景：{data['space']}；{chain}。先核原文与前段状态，再判断遗漏或合理画外；不把无戏份当作不入画依据。")
    if code == 'shoulder_axis':
        chain = ' → '.join(f"{s['segment_id']}@{fmt(s['start'])}–{fmt(s['end'])}秒" for s in data['shots'])
        return f"{chain}；{'；'.join(data['reasons'])}；原记录：{' ／ '.join(data['evidence'])}。"
    if code in ('broad_narrative_run', 'narrative_task_readability'):
        chain = ' → '.join(f"{s['segment_id']}@{fmt(s['start'])}–{fmt(s['end'])}秒"
                           f"({s['start_scale']}→{s['end_scale']})" for s in data['shots'])
        if code == 'broad_narrative_run':
            return f'连续偏宽：{chain}；核对空间／身体任务及遗漏的情绪细节。'
        return (f"{chain}；来源{data['source_id']}的{data['task']}任务“{data['focus']}”；"
                f"证据：{data['evidence']}；核对实际可读性。")
    if code == 'live_visual_pacing':
        kind = '全镜无发声' if data['whole_shot'] else '镜内无发声'
        longer = '；超过3秒，核对持续任务' if data['longer_review'] else ''
        return f"{kind}{fmt(data['start'])}–{fmt(data['end'])}秒{longer}。"
    if code == 'narrative_visual_pacing':
        voices = '、'.join(f"{KINDS[v['kind']]}{fmt(v['start'])}–{fmt(v['end'])}秒"
                          for v in data['voice_ranges'])
        return f'原声交集：{voices}。'
    if code == 'timing_overlap':
        gaps = '；'.join(gap_summary(g) for g in data['uncovered_ranges'])
        return (f"段长{fmt(data['duration'])}秒，"
                f"发声记录覆盖{fmt(round(data['speech_covered_seconds'], 7))}秒；未覆盖：{gaps}。")
    raise ValueError(f'Unmapped review finding: {code}')


def print_review_report(plan):
    """One report in shot order; every finding appears once with linked locations."""
    report = collect_review_report(plan)
    if not report['items']:
        return
    print(f"REVIEW: {len(report['items'])}项候选；R编号仅本次有效。")
    items = {item['id']: item for item in report['items']}
    codes = {item['code'] for item in report['items']}
    for code, (short, note) in TOPICS.items():
        if code in codes:
            print(f'REVIEW [{code}] {short}：{note}')
    shown = set()
    previous_segment = None
    for group in report['groups']:
        if group['segment_id'] != previous_segment:
            print(f"REVIEW {group['segment_id']}：")
            previous_segment = group['segment_id']
        entries = []
        for ref in group['items']:
            item = items[ref]
            short = TOPICS[item['code']][0]
            if ref in shown:
                entries.append(f'关联：{ref}/{short}（本镜同查）')
            else:
                scope = '（跨镜）' if len(item['locations']) > 1 else ''
                entries.append(f'{ref}/{short}{scope} key={item["key"]} {finding_text(item)}')
                shown.add(ref)
        print(f"  {fmt(group['start'])}–{fmt(group['end'])}秒：" + '；'.join(entries))
    print('REVIEW: 最终交付须在 delivery_review.findings 逐项绑定当前 key、处理依据及全部相关镜头证据；未处理不能通过。'
          '真人剧保留无声区间另填 timing_overlap，修改同步原声、音效和后镜。'
          '无提示仍须其他内容复核及最终校验；报告不自动改稿、裁时或验证音视频。')
