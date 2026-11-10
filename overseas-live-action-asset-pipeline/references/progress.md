# 按图记录与续跑

scripts/asset_state.py 只管理内部进度，不调用出图接口、不导出表格。所有记录基于真实工具结果和实际查看，不把“请求已提交”当成图片完成。不需要用户操作这些 JSON。

续跑先按 [上下文控制](context-control.md#精简进度摘要与恢复) 读取工作区 run-summary.md，再定向核对真实 state 和平台任务记录；摘要不能替代原状态或触发重复生成。

## 仅出图分流，前后步骤共用

asset_workflow 在仅表格／提示词时可为 null；图片请求缺省归一为 jumeng，明确 codex 保留。剧梦项目名齐备后各图片类型进入 preflight/generate 或参考依赖等待队列。旧状态若返回 choose_image_workflow，补默认 jumeng 后重新 init；不询问平台。已有结果继续原检查和保存，text_only 不出图。

首次出图前，按共用流程完成所选资产表的完整计划、检查和本地保存；仅图片请求不强加表格。需要图片但未指定平台时默认剧梦，明确 Codex 时沿用，不重复询问平台。更新 request 并 init，相同设计保留完成记录。attempts 记录实际提交时的 asset_workflow，切换当前选择不改写旧图片来源；历史缺项不补造。

图片已返回则继续原有 inspect/save/deliver，不因平台准备而阻塞后处理。已有 generated 或明确终止事件继续接收；新图片提交支持 Codex 和剧梦已选人物、三视图、场景及道具；剧梦先按 jumeng-workflow.md 完成项目名、浏览器、项目和资产剧集准备，再执行当前对应步骤。命名、待审、正式归档和表格覆盖不分叉。

## 地域模式与恢复

request.region_mode 须先由用户选择，state 记录同一归一模式。未选时不能 init；首次为旧请求补选海外可保留相同设计的原有合格图。国内／海外切换须核对并提交完整 plan，不用 --partial 省略旧项；模式改变使旧模式的图片验收失效，保留尝试历史和文件，不把旧图自动视为新模式成品。先接收在途结果后再切换，表格按更新后的完整提示词覆盖原文件。

国内的具体亚洲面貌设计、中文内容与位置记录于现有 preflight；按 [国内校验](domestic-mode.md#校验与记录) 复核，不增加种族识别或虚假的语言识别分数。下文非亚洲面貌偏好仅用于海外，场景道具的文字排除按所选模式执行。

## 初始化

从已经通过正文核对的 resolved_assets 制作 plan.json。默认仅表格，先完成全部可入表项的完整计划；图片开启后，每项 generate_image 决定是否出图，未开启项返回 text_only，但仍须补齐表格所需提示词。需要表格时先写齐可入表项并保存表格；明确仅图片时可先写 1–2 个已设计完成的基准项；每个提供的 ID 恰好一项，提示词完整且能独立使用：

~~~json
{"assets":[{"id":"匹配结果的ID","prompt":"完整的最终中文提示词","reference_ids":[],"reference_paths":[],"preflight":{"body_basis":"实际正文场次和证据行","continuity":"当前状态与应保持的不变量","framing":"本项比例、边界和留白安排","exclusions":"本项容易误入的物件或环境"}}]}
~~~

人物 role_priority、outfit_role、design_only 从索引经 resolved_assets 自动传入 state，不仅写在 plan 里。主要常服与场合专用服的事实依据及美术补充区别写入 preflight；调整排序元数据后重新建索引并 init，不改变未修改图片的设计及通过状态。

reference_ids 只能引用 resolved_assets 内资产，reference_paths 只用本次提供或明确指定的已有图绝对路径。三视图 parent_id 自动加入引用；换装／状态图引用基准照。助手拒绝额外项、重复 ID、未知引用和循环依赖。

场景按 [空间区分与防重复](visual-specs.md#空间区分与防重复) 优先建立关联图片依赖：本次生成的关联空间用 `reference_ids`，用户提供且已确认适用的已有图用 `reference_paths`。从合格基准图向相邻空间延伸，后续空间可引用更直接关联的合格图；多个分支可共用一张基准图，无须全部串成单链。已选参考仍在生成或未通过检查时保留等待依赖，先推进其它独立项，不为凑并发跳过引用或构造互相等待。提交前实际查看源图，将 `next --include-prompt` 返回的可读引用路径传入图像工具；有主路径采用已归档图，无主路径采用已合格交付且可读的源图。参考选择、继承细节及延伸关系写入已有 `preflight.continuity`；换用其它适用参考时先更新计划及依据。仅提示词／表格模式不为建立图像参考额外启动生图。

~~~text
python scripts/asset_state.py init state.json request.json plan.json --partial
python scripts/asset_state.py next state.json --actions generate retry_generation --limit 2 --include-prompt --no-summary
~~~

`--partial` 只允许图片模式：所有已选 ID 都保留在 state，缺少提示词且启用出图的新项标为 design、禁止提交；图片关闭项为 text_only，其缺失提示词仍由 pending_design 和表格导出检查发现；省略的已有项保留原设计和真实进度。补充提示词后再次 init --partial，不能修改仍在途的设计。可累积完整 plan 或仅提交新增项，交付前从 state 核对所需提示词已齐备：有表格时覆盖全部可入表项，图片覆盖已启用项；不要把首批 plan 当最终完整表格源。不开 --partial 时仍要求全量计划一一对应，纯提示词／表格模式用此方式。

next 默认返回全范围动作数量，过滤只缩短明细；常规补位用 `--no-summary` 配合 `--limit`，足够条目后停止扫描，`counts_complete=false` 时数量仅代表 `scanned_assets` 内的结果，不可用于宣布全量完成。收尾省略 --no-summary 核对总数。`--include-prompt` 附完整提示词、引用 ID 与实际引用路径；有归档要求时使用父图已正式保存路径。同次不重打全量 JSON。preflight 也可随后单独提交；完整提示词已写但检查未齐时返回 preflight，不能出图。纯文字／表格模式不要求出图前检查。

state 是唯一有效设计源，plan 只是 init 的输入。返修先用 init --partial 更新完整提示词、实际引用和 preflight，再提交下一次生成；已经发生的尝试、提示词、参考图哈希与失败预算保留。实际编辑指令可在 generating 的 tool_prompt 中另存，不能用一条修改指令替代完整最终提示词。表格不得另用 prompt-final 文件或字符串替换维护不同版本。

state.json 留在工作区，每次事件原子写入并增加 revision；由一个协调者串行提交状态事件，图片可并发。剧梦按实际分镜分别维护最多 5 个在途请求，允许多个分镜已提交任务同时生成；当前分镜满额时等待其空位，不跨分镜补交。记录写入串行不代表生成串行。分镜标识与任务句柄映射留在编排记录，不捏造脚本字段。未完成参考图仅阻塞依赖项，不阻塞其他独立资产；详见 [多分镜调度](jumeng-workflow.md#多分镜并发调度)。锁冲突时等待实际写入者结束；异常遗留 .lock 须先确认无写入者再清理。

## 仅生成三视图时的已有原照

人物原照关闭、三视图开启时，原照条目仍保留作表格与父图关系，不进入生成队列。优先复用同一 state 中已检查合格且文件可读的原照；其本次无需再次保存或交付。也可实际查看用户明确指定的已有原照，核对对应角色、服装／状态、单人、画面规格、原创设定及适用风险记录后，在原照的 plan 条目加入：

~~~json
{"reference_image":{"path":"已有原照的真实绝对路径","single_person":true,"note":"实际查看结论：对应角色、服装／状态、单人及适用检查依据"}}
~~~

这不是生成结果，不补造 attempts、review 或交付记录。脚本检查文件、几何和哈希；视觉结论须实际查看，不能只为解锁依赖填 true。`reference_image=null` 可清除该绑定；不能在原照出图同时开启时新增该字段。三视图提示词及 parent_id 照常保留，实际引用路径由 next 返回，图片变化后重新检查并 init，受影响三视图须更新设计。缺图时三视图为 wait_for_reference；说明缺项，不为它自动生成原照，表格和其它独立项继续。

## 下一步

| action | 执行 |
| --- | --- |
| design | 本项仍在本次范围内；补全提示词及引用后 init --partial，不可生成或算完成 |
| preflight | 对当前设计完成四项实际检查，用 preflight 事件或 init 提交，不能跳过后直接生成 |
| text_only | 本项图片未开启，仍按表格范围补齐提示词；不调用图片工具、不计入未完成图片 |
| choose_image_workflow | 兼容旧状态：图片请求补默认 jumeng 后重新 init，保留原成果；缺项目名再询问 |
| choose_jumeng_project | 询问并记录必填 jumeng_project_name，重新 init；不阻塞表格和已有图片后处理 |
| generate / retry_generation | 检查依赖后提交该项一次独立出图 |
| wait | 用现有句柄接收结果；等待超时不重发 |
| wait_for_reference | 先完成／定位引用图；文件哈希改变时重新 init 核对设计 |
| replan_references | 引用内容已变，重新 init，仅重做受影响的后续图片 |
| inspect | 读取真实源图并查看，执行比例、像素与视觉检查 |
| save | 合格源图已在，只执行单图保存，不重新出图 |
| deliver | 原生显示合格图片，记录交付 |
| complete | 文件、设计及引用有效，复用 |
| skipped_multiple_people | 原照检查确认为多人，跳过其三视图，不裁切拆分、不计生成失败 |
| paused | 同项在 generation 或 quality 阶段连续两次尝试失败，错误码不同仍计入该阶段；继续独立项 |

“无路径、已交付、未归档”是有效完成状态；本次新生成的单人原照无路径时须已交付且源文件可读，有路径时须先归档成功，再解锁三视图。原照关闭、仅引用已有原照时按上述已有原照规则复用。缺失源图时可用哈希一致的已保存文件恢复，不因临时文件清理而重生。

## 事件

直接使用已有助手，不另写 event_record.py。事件可作为一个对象或顺序数组写入 event.json，一次调用；短事件也可通过 `--json` 传入对象（注意当前 shell 的引号规则）。每个事件仍独立验证并落盘：

~~~text
python scripts/asset_state.py record state.json event.json
~~~

通常结果返回立即按 [返回即保存待审副本](runtime.md#返回即保存待审副本) 调用 receive_asset.py，一次接收 generated 并完成有主路径时的待审保存；随后实际查看并用 finish_asset.py 处理检查和正式归档。待审副本以 pending 记录，优先作为可读检查源；它不是 review、saved 或 delivered，不得解锁后续引用。已有多个真实事件可顺序提交，不能为了合并命令延后图片绑定或保存。数组中某项失败返回 committed 与从 0 开始的 failed_index，修复后只补未提交部分。checked 自带几何检查，不再另跑 image_check.py 重复检查。

每次出图提交前分配唯一 attempt_id，并在后续 generated/checked/failed/saved/delivered 中持续携带。generating 的 handle 是实际工具句柄或本次唯一提交标识；等待句柄和资产 ID 绑定在工具编排中保存。相同 attempt_id 的同内容提交可安全重放，不能拿旧编号发新请求。可选 event_id 用于命令重放，同编号不同内容拒绝。脚本兼容旧事件并会为缺失编号的开始事件分配编号，新执行始终显式提供，避免迟到结果误绑。每条事件可附带时区的实际发生时间 at，另自动记录 recorded_at；未提供时采用当前时刻，不在全部完成后伪装实时记录。

以下均须包含当前资产 id 和 event，附加字段如下：

| event | 附加字段与使用条件 |
| --- | --- |
| preflight | checks：body_basis、continuity、framing、exclusions 四项具体结论；必须对应当前提示词 |
| generating | attempt_id、handle；可选 tool_prompt 保存实际工具指令。提交与记录连续，先记在途后处理结果；自动快照完整提示词及实际引用路径／哈希 |
| generated | attempt_id、source_path：该请求返回的真实绝对源路径，立即记录返回时刻。拒绝与当前编号不同的旧结果，不按目录时间排序猜路径 |
| staged | attempt_id、target_path：receive_asset.py 实际返回的待审副本路径，须在本次主路径的待审目录内且内容与返回源图一致；不是视觉验收或正式归档。可带助手实测 timing |
| checked | approved：布尔值；note：实际查看结论。通过时自动检查可读性、方向、比例、最低像素；人物原照另需 single_person 布尔值。未能判断人数时暂不通过 |
| saved | target_path：单图保存返回的实际正式目标；核对在本次主路径内、位于待审区之外且哈希与合格源图一致。必须先有真实 saved/reused 结果；timing 仅采用保存助手返回的 copy_seconds、verify_seconds、save_seconds |
| delivered | 图片实际原生显示后记录，无附加字段 |
| failed | stage：generation/save/quality；code：稳定错误码；message：实际原因。生成失败另须 terminal=true 且原请求明确结束；等待超时不算终止 |
| retry | 仅对 paused 项；note 说明已修复条件或用户明确重试要求。清理失败计数，不删除源图，不无限自动恢复重试 |
| verify_legacy_prompt | 仅用于没有逐次日志的已检查旧图；prompt 为已对照原工具记录和图片核对的完整最终提示词，note 说明依据。不补造历史尝试或提交时间 |

checked 几何检查失败时保留源图，记录 failed 的 quality 错误以安排返修；视觉不合格可直接 checked、approved=false 并给出 code/note。同一张失败图只计一次，不能为达到暂停阈值又记一次 failed；脚本会按尝试去重。两次不同质量问题也会暂停，不能换错误码绕过预算。修改提示词不会清空失败计数，暂停项须有明确修正依据才能 retry。人物人数与身份、三视图姿态等结论必须实际查看。保存失败只重试保存；不得解锁依赖。

手机仍用普通道具任务，在 `approved=true` 前按 [手机道具检查](visual-specs.md#手机道具正背面与手机壳) 核对正背面完整性、同一机身与壳体一致性、角色适配设计和所有露出位置的品牌标识，实际结论写入现有 `checked.note`。缺面或两面不一致用 `phone_view_mismatch`，无剧情依据缺壳或设计不符用 `phone_case_mismatch`，出现品牌标识用 `phone_brand_mark_visible`，并记录 `approved=false`；沿用同项连续两次质量失败上限，不新增字段或自动翻倍图片任务。不能用尺寸、哈希检查替代视觉检查，用户明确另定构图时按本次约定验收。

场景在 `approved=true` 前按 [场景提示词](visual-specs.md#场景提示词) 检查必要装饰道具和生活陈设是否落实、主要位置是否连续、是否存在不合理堆砌或遮挡，以及需独立使用的剧情动作道具有无误入；同时核对画面天色与照明是否符合原文明示或剧情推断确定的时段，推断依据沿用已有 preflight 文字。在现有 `checked.note` 记录实际观察。必要装饰遗漏、误清空或时段表现不符时按现有质量流程返修，沿用同项连续两次失败的上限，不新增脚本字段；不把“场景中出现道具”或“时间来自合理推断”一概判为不合格。

场景还须按 [空间区分与防重复](visual-specs.md#空间区分与防重复) 对照关联参考及同地点其它空间的实际图片，在现有 `checked.note` 记录对照资产 ID／参考来源、已继承的可见建筑细节与连接关系、目标空间的可见区别；无参考的首张只记录自身设计落实情况，不虚构跨图检查。不同空间出现相同画面或缺少可辨认区分点时，用 `approved=false`、`code=scene_space_not_distinct`；延伸图破坏已确定的建筑细节或可见衔接关系时，用 `code=scene_connection_mismatch` 并拒绝批准。记录具体原因，更新该空间完整提示词及引用后返修，沿用同项连续两次质量失败的上限。同一房间昼夜版布局一致和跨空间保留共享结构属于连续性，不按跨房间重复处理；不新增脚本字段，不把尺寸或哈希检查说成空间视觉识别。

同次场景审核按 [影视光影检查](scene-lighting.md#出图检查与表格同步) 检查光源、投影、选用效果、关键布局可读性与关联空间光照连续性，结果并入现有 `checked.note`。明显不符时记录具体可见问题，以 `approved=false`、`code=scene_lighting_mismatch` 返修；不以术语数量为验收条件，不增加生成轮次、事件字段或质量失败预算，也不延迟返回结果的待审落盘。

已启用拟人动物都市题材的资产，按 [动物题材逐项检查](animal-city.md#逐项检查与交付) 核对物种结构、毛色、服装、相对尺度和动画材质，替换下文仅适用于真人的面孔、族裔及皮肤年龄检查；社会年龄仍按该分支沿用既有映射。动物居民沿用人物任务，`single_person` 表示仅一名拟人角色，所选三视图照常依赖合格单角色原照。风格和核验依据写入现有 `preflight.continuity`／`checked.note`，不新增状态字段；有自然人要素时再执行对应肖像初筛，纯动物不得虚报已完成人脸识别或全球授权。所有保存、几何与失败预算规则继续适用。

真人角色的原照、换装／状态图、群像和三视图在 `approved=true` 前须完成 [原创设计复核](character-originality.md)，在现有 `note` 中记录实际可见的五官设定与辨识点是否落实，不新增必填事件字段。原创设计未落实用 `approved=false`、`code=originality_design_mismatch`；脸部细节不足以检查用 `code=face_details_uncheckable`（真人三视图以左侧未打码特写作面部检查，右侧全身面部按模板打码不触发此失败码；另核对打码范围及自然腰线上下段比例），都沿用现有质量失败及返修上限。未检查不得批准，不以几何检查通过代替原创设计检查；记录不得声称识别了某位明星或已排除所有名人相似性。

海外模式的人物生成前还须按 [人物面貌偏好](visual-specs.md#人物面貌偏好) 在已有 `preflight.continuity` 中落实默认非亚洲人物设计方向或本次明确指定，以及可核对的具体面部特征；群像逐人处理。出图后在现有 `checked.note` 记录五官设定及基准面容是否落实，具体设计明显偏离时沿用 `originality_design_mismatch` 和既有质量失败预算返修，不另建族裔识别事件，不根据参考图或生成图判定族裔或计算“亚洲概率”。

人物还须执行 [年龄范围检查](visual-specs.md#人物年龄范围)：生成前在已有 `preflight.body_basis` 记录正文年龄依据，在 `preflight.continuity` 核对“正文年龄 → 对应区间 → 确定外观年龄”；只按原始剧情年龄映射一次。未明确年龄时记录设计依据，同剧情时段的派生图沿用基准外观年龄。不得因正文为 30–50 岁或合法映射结果为 30–35 岁而按旧规则暂停。出图后在 `note` 记录大致年龄感是否符合最终外观设计，不能要求映射后的图恢复正文年龄感。明显不符用 `approved=false`、`code=appearance_age_mismatch`，无法观察用 `code=face_details_uncheckable`，沿用质量返修上限。该记录不是精确年龄测定，也不得填入未经执行的“名人相似度≤70%”结论。

人物的 [肖像及形象权风险初筛](portrait-rights-risk.md) 在批准前进行，不阻塞内部待审副本的保存。可验收项把初筛结论、来源及授权依据、使用范围和法域遗漏写入已有 `checked.note`。高风险或关键授权待核项将事实和缺项留在工作区内部审查记录中，保留源图及待审副本、暂不提交通过事件或调用 `finish_asset.py`，继续其它项；不要把授权待核记成 `quality` 失败并自动重生，也不添加助手不支持的事件或字段。法律风险结论与图像技术验收分别说明，不以 `approved` 冒充全球商业许可。

~~~json
{"id":"本次资产ID","attempt_id":"本次唯一请求编号","event":"checked","approved":true,"single_person":true,"note":"已查看：单人，面容与服装符合计划，无裁切"}
~~~

## 请求变更与恢复

先停止旧范围的新任务提交，保留在途句柄，读取 next 与实际文件；不要丢弃仍在运行的请求。用户更改请求后仍允许接收原在途 generated 或明确终止的 generation 失败事件，但不允许继续旧计划的新生成、检查或保存；接收完成后更新计划、重新 init，再按新范围执行。

- 只改归档路径：重新 init 会保留合格源图、清除旧目标作为“本次保存”的标记，下一步为 save；不把旧目录冒充新目录。
- 只改图片选择、表格开关或只加三视图：重新 init；相同资产 ID、提示词及引用保留历史成果和尝试，关闭项返回 text_only，不生成、不重新归档，也不删除文件。只新增已开启项；只加三视图复用原照。旧请求缺省 output_mode 现在按表格解释，只有本次明确沿用的图片授权才写成显式 image_types，不能按旧默认补开图片。
- 提示词、参考图内容或最低像素变化：重置对应设计；实际引用哈希变化会使依赖结果失效，无关项仍复用。只改命名或场次记录不修改视觉提示词。纯同义措辞可沿用既有最终提示词，不能为格式润色触发重生。
- 剧本变更：重建正文索引并重新匹配；过期请求／索引不能继续生产。

记录保留所有尝试的完整提示词、实际工具指令、参考图、源文件、检查结论及提交／返回／检查／归档时间，通过图片以 accepted_attempt_id 绑定。`python scripts/asset_state.py metrics state.json` 输出实际阶段耗时，历史缺失为 null；旧版记录可复用已有成果，但建表前需核对最终提示词，不能假定初始提示词就是返修后的版本。生成耗时含排队，并行区间不能累加冒充总历时；没有可靠计时不推算速度，不额外生成测试图片，不把内部进度写进 Excel。
