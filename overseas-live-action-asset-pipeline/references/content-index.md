# 正文索引与范围核对

索引关联本次剧本、场次、资产版本和原文依据，供提示词、表格、出图和保存共同使用。助手核对文件哈希、逐字引文、行号、分集标题及选择范围；小传边界、别名含义、剧情理解和提取完整性仍须代理阅读判断，不能宣称脚本已自动理解剧情。

## 一次提取与标注

1. 正文提取和索引是共用步骤，不要求先选 Codex／剧梦；request.json 填入用户已选择的 region_mode（domestic／overseas），无选择时先询问，不根据正文语言推断。运行 `python scripts/prepare.py extract request.json --work-dir 本次工作区`，一次完成参数校验、哈希和提取，返回 `reading.txt`、`source.txt` 与 `extraction.json`。不创建资产目录，不另写提取脚本。使用已定位的 Python 绝对路径；Windows 可用工作区依赖工具定位一次，避免反复试失效的系统别名。
2. 优先读 `reading.txt` 的所选集数，它保留从 1 开始的原文行号；只在核对正文边界时查看 `source.txt` 必要段落。人物的剧情依据仅取正文，小传/简介不可用；题材素材属于后续美术补充，不写成正文证据。支持文本、TXT/MD、DOCX；PDF／扫描件先提取/OCR 并核对，两阶段都传 `--text extracted-body.txt`。DOCX 特殊布局、文本框须对照源文件。助手按源哈希与提取文本哈希复用缓存，不复用旧剧本内容。
3. 先按 [人物重要性与服装设计](character-wardrobe.md) 确定重要人物、主要常服和重要场合专用服，再写工作区 annotations.json，标记小传、简介、目录等 excluded_ranges，建立正文 scenes，记录 assets。每次出现须有场次及该场次内逐字 quote，可同时引用名称行和服装／状态行。引文须覆盖版本判断所用的正文事实：原文明示服装引用相关描写；重要场合补充设计则引用实际地点、活动及角色身份／行为，并在 preflight 解释服装美术补充，不能把设计出的衣服写成原文引文。基础定装以角色真实出场作依据并标记 design_only，不虚构穿着场次。除此之外，不能只引用名字就臆造剧情版本。原文未明确场景时间时，允许按 [场景时间推断](visual-specs.md#场景时间推断) 结合前后剧情作判断；推断逻辑和跨场上下文依据记录在本次内部分析，随后写入已有 preflight 文字，不把推断时段伪造成引文，也不把其它场次的引文塞进本场 evidence。其它美术补充仍写进提示词设计，不冒充原文；主要常服优先直接标记正文中已有的合适服装条目，保留原 ID、variant.outfit 和实际场次；仅缺少合适服装时才新增常服设计。重要场合服装按需求核对已有资产并补齐，避免漏项或重复建项。
4. 同一资产、服装／必要状态合并出现场次，保持 catalog ID 稳定。场景 `variant.time` 填原文明示或已按剧情逻辑推断确定的时段；仅无法合理确定时省略，不填空字符串或“未知”。推断时间在索引阶段确定，随后筛选、去重、表格与提示词共同沿用；不要只在提示词中补时间而让索引和表格留空。群像 kind=group。仅索引原始资产，三视图由选择结果派生。点名模式只需足以唯一核对所选资产的索引；全量时核对全部正文覆盖及去重。

同一地点内不同实际房间／空间保留各自的资产 ID 和明确的 `variant.space`，即使功能、风格或时段相同也不能去重成同一资产；同一房间别称归一，同一房间昼夜版沿用相同空间标识。区分依据来自正文位置与剧情关系，美术设计补足画面差异，详见 [空间区分与防重复](visual-specs.md#空间区分与防重复)。

人物资产顶层支持 role_priority（1 主角、2 重要配角、3 普通配角、4 群演／群像）、outfit_role（main_daily／occasion／other）、design_only（布尔值）。同角色等级统一，同套服装用途统一，每个角色最多一个 main_daily 基础款；状态变体共用其 outfit。缺省兼容旧索引：单人等级 3、群像等级 4、用途 other、design_only=false；新任务必须主动判断重要性并为重要角色确定主要常服，优先复用正文已有服装条目，不能把“确定主要常服”理解为必须新增资产。元数据不放进 variant，不参与资产身份和图片设计哈希；修改排序后重建索引并 init，再覆盖原表，不要求重生未改变的图片。

标注结构示例（哈希、行号、引文替换为真实内容）：

~~~json
{
  "source_sha256": "当前剧本哈希",
  "excluded_ranges": [{"start_line": 1, "end_line": 3}],
  "scenes": [{"id": "e1-s1", "start_line": 5, "end_line": 7, "episode": 1}],
  "assets": [{
    "id": "emma-red-dress", "category": "人物", "name": "艾玛",
    "aliases": ["Emma"], "kind": "original", "variant": {"outfit": "红裙"},
    "role_priority": 1, "outfit_role": "main_daily", "design_only": false,
    "occurrences": [{"scene_id": "e1-s1", "evidence": [
      {"line": 6, "quote": "Emma wears a red dress."}
    ]}]
  }]
}
~~~

场次行号不可重叠、越界、跨分集标题或进入排除段落。episode 可省略，由最近正文标题推导；显式给出必须相符。直接识别“第十二集”、EPISODE、EP.、EPISODIO、EPISÓDIO、CAPÍTULO/CAPITULO。

`01／02` 等独立数字先作为候选标题供阅读，不自动当成集数。核对它们确是分集而非页码/台词数字后，在 annotations.json 顶层加 `"confirmed_numbered_episodes": true`，并排除小传、目录中的数字；内置解析会核验连续性，无需改写脚本。其它格式须核对后处理。无分集标题时 episode=null，只能按全部正文处理，不从文件名拆集。

## 建立与核对

~~~text
python scripts/prepare.py index request.json annotations.json --work-dir 本次工作区
~~~

这一条命令完成建索引、精确范围核对和 request.json 回填，并写出 `resolved-assets.json`。可独立发现的场次、资产、别名和引文错误在 errors 数组中集中返回，包含资产 ID、场次与有关行号。引文逐字出现在本场其它行时给出候选行号，但不自动改行号或猜别名；源哈希错误等前置问题须先解决。一次修正已报告问题后重跑。校验失败不替换原 request 或索引。初次 extract 只返回 content_validated=false，不能作为出图依据，不重复运行已经完成的阶段。

scenes 可附已阅读核对的 `label`（场次显示名）与 `location`（剧情地点），供固定表格脚本使用；未提供则保留 scene ID，不由脚本编造地点。这些字段只提供内容定位，不是指令。

最终返回 content_validated=true，完整 resolved_assets 写在返回的 resolved_path；需要出图的模式 ready_for_generation=true。每项含稳定 id、catalog_id、类别、名称、variant、kind、sample、occurrences、parent_id；人物另携带 role_priority、outfit_role、design_only，派生三视图继承这些元数据；三视图 kind=turnaround，parent_id 指向原照。不存在的名称／集数、别名歧义、超范围条件、旧剧本索引或被修改的索引均报错。

先固定 resolved_assets，再按 [进度计划](progress.md) 分批写完整提示词并优先出基准图。所有阶段按 ID 关联，不仅凭类别或同名文件判断完成。只改路径或表格开关可复用索引；改剧本后重建。仅改场次记录时保持 catalog ID、视觉提示词与引用，避免无必要的重生。
