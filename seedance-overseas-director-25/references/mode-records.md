# 模式记录与统一校验

四模式使用同一套真人剧渲染、机位、动作、连续性与交付检查程序；旁白增量合入同一数据结构，不需维护第二份计划或使用旧技能程序。

## 顶层模式与默认值

`mode`必须是用户明确选择的四个中文名称之一：`3D漫推文`、`3D漫真人剧`、`真人剧`、`推文`。脚本不能证明用户已选择，助手须在入口确认；脚本公共命令拒绝缺失／未知mode，风格或文体不能推断mode。历史内部回归函数保留旧数据兼容，仅用于维护，不是制作入口默认。

| 项目 | 两种剧模式 | 两种推文模式 |
| --- | --- | --- |
| sources.kind／speech.kind | dialogue、VO、OS | dialogue、narration、inner |
| 完整一集默认最低时长 | 0，无下限 | 50秒；节选／分析不适用 |
| 实际镜头低于1秒 | 满足共同短镜规则可用short_shot | 默认不允许；只有用户明确改变，记录非空narrative_short_shot_basis，并仍填本镜short_shot与可读性 |
| 原始旁白清单 | 不新增独立叙事旁白 | narration_sources必填；无旁白为[]并写narration_absence_basis |
| 声音标签 | 台词参考／VO 参考／OS 参考 | 台词参考／旁白参考；后者明确叙事旁白或内心声 |

共同每段96英文词上限、30秒上限、整数段长、静态≤2秒；两种推文另要求每段至少4秒，先连续剧情分组再计声锁时，见 [分组与预算](narrative-budget.md)。limits仅用于用户明确更改制作约定，并附limits_basis，不能伪造用户授权或绕过两种推文4–30秒区间；推文50秒默认由mode派生，不需伪写用户修改依据。

3D模式另填`style_id`（目录当前为`zootopia`或`generic-3d`），按本模式风格选择机制确定后记录。每段presentation.整体风格以“风格标签：目录label；”开头并写本段具体特征；渲染器按mode给出3D或真人规格和对应声音限制，不从presentation猜制作模式。无需另造美术JSON，角色映射等继续在项目记录中保存。实际素材与造型稳定性仍须人工审阅。

## 旁白来源与声音

`narration_sources`保持 [旁白专题](narration.md#先登记后分配最后核对) 的id、episode、source_ref、original_text、source_language、status与source_id／omission_basis。keep一对一指向narration类别的sources；omit不进入发声sources／speech，不能只从清单删除。来源无法确认时不得从成稿倒推。inner和dialogue按普通sources保留。

speech.performance写引号外说明；narration须明确中性／平稳，人物情绪不套给叙述者。渲染器为narration／inner生成明确声音类别、独立声轨说明与“画外叙事｜原身份／内心声｜原人物”行首；不要在performance重复自动说明或要求画内人物朗读／对口型。内部speaker保持原值。音色默认同speaker；用户指定借用时在source与相应speech都填voice_reference，source另填voice_reference_basis记录实际依据。校验分别核对声源及参考音色，不把借音色当作换人。逐镜无声表演与实际对白区间由现有speech派生，见 [旁白口型检查](narration-mouth-isolation.md)，不增加必填记录。

同一句旁白在同一生成段内连续发声时保留一条完整speech，允许跨正式分镜；声音行放在起声分镜，后续画面由speech_carry_prose明确承接，不因换编号拆记录。确需真实停顿或跨生成段时才分片，同一source_id拼回原文；有真实停顿填写pause_before，必要的无标点语义边界或原文明示打断另按 [旁白断句记录](narration-flow-validation.md) 填boundary_before。不能为切镜、反应镜头或凑整数添加停顿，也不能换source_id逃避同句检查。

## 长旁白视觉记录

按 [旁白画面信息覆盖](narration-coverage.md) 维护 `segment.narration_coverage`，累计旁白超过6秒时必填；短旁白同样按原文检查画面。完整原声可覆盖多个镜头，每个承载叙事旁白的实际镜头严格小于3秒，按全镜计时。旧 `narration_visual` 仍可读取并校验内容，但不批准超时长镜；新制作不用填写旧长镜理由。详细口径见 [连续旁白切镜](narration-editing.md)。

## 跨类别同期记录

仅推文模式有来源依据时填写：

```json
{"overlap_basis":"原文明示叙述者继续讲述时玛雅同时喊话","frame_voice_exceptions":{"分镜1":{"basis":"原文明示同期叠声","source_ids":["N1","D1"],"lip_sync_speakers":["玛雅"]}}}
```

引用必须覆盖该分镜全部实际声音来源，不接受用同一记录包住普通顺序对话；每个参与声音须与另一类别实际重叠。lip_sync_speakers仅列dialogue说话者，口型是否在画面可见仍由逐镜内容决定。剧模式不接受这个推文例外，同类多人继续用原frame_speaker_exceptions。

## 校验入口

成稿按 [四模式成稿检查](final-mode-validation.md) 分别执行。最终文本的整体风格、画面规格和声音限制必须与记录一致；整体风格须在presentation明确填写，规格和声音限制可用mode默认值或显式定制值。实际语气检查仅约束推文两模式的叙事旁白，不限制人物对白、内心声及剧模式VO／OS的剧情情绪。摘要一致不能替代这些检查。

先按rendering.md生成并审阅实际final.txt，再用`python scripts/validate_plan.py plan.json --final-prompt final.txt`；路径必须指本次工作文件，不覆盖技能。公共入口要求mode，timing-only同样先记录模式。旁白、3D与模式检查自动合入统一校验，不再使用旧--check-narration或--final-text命令。没有实际视频时不声称检验成片。
