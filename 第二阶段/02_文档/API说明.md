# API 说明

当前API为0.11.0。列表传page/page_size返回分页对象；新增history-page与按来源版本的usages分页接口；统计增加value_page，见 [分页契约及恢复流程](关键逻辑说明.md)。旧列表不传page的数组响应保留兼容。expected_version取edit_version，不取内容number；竞争或不可恢复内容冲突409，连接异常503，重新加载补齐同一预留，不自动覆盖重试。

第二阶段0.10.0在题库、版本、共享和引用基础上增加跨问卷统计。入口、完整请求示例、三项指标、权限及合并排除原因见 [题库跨问卷统计](关键逻辑说明.md)。原功能说明见 [题库版本共享与引用](关键逻辑说明.md)。旧题迁移仍是显式命令 `python -m scripts.migrate_question_library --database 目标库 [--apply]`，默认只读预检，不随启动自动执行。

基础地址 `http://127.0.0.1:8000`，JSON 请求，UTC 或带时区 ISO 日期。具体输入模型见 `/docs` 与 `/openapi.json`。字段说明以 app/models.py 为准；当前API版本为0.11.0。

新增 `GET /api/library/questions/{template_id}/statistics` 逐版本汇总；`POST /api/library/questions/{template_id}/statistics/merge` 检查后合并，输入 `version_ids`、`base_version_id`、可选 `option_mappings` 和 `unit_confirmations`。都要求登录，题目使用权限不等于问卷结果权限。数字题增加可选 `unit` 字段（非空白，最多64字符），旧版本不回填；无有效回答的平均值仍为null。

API模型的机器可读导出在01_可运行代码/examples/openapi.json。

## 第二阶段新增接口

以下都需要Bearer登录，所有者取自会话；客户端不能指定owner。题库是私有归属，接收方只能读取明确获共享的版本。

| 方法 | 路径（前缀 /api） | 输入与行为 |
| --- | --- | --- |
| GET / POST | /question-banks | 列出本人题库；创建输入 `{name}`，201返回题库 |
| GET / POST | /library/questions | GET可按bank_id筛选本人题目；POST输入 `{question}`，201创建独立题目及v1 |
| PUT / DELETE | /question-banks/{bank}/questions/{template} | 幂等加入/移出，仅改变归属；不删除题目和版本 |
| GET | /library/shared | 当前账号获共享的题目，latest仅指可访问的最高版本 |
| GET | /library/questions/{template} | can_edit、edit_version、可见版本、分组差异、来源和逐实例引用明细 |
| GET | /library/questions/{template}/versions | 本人全部已提交版本，或接收方获共享的版本列表 |
| POST | /library/questions/{template}/versions | `{expected_version, base_version_id, question}`；201追加，不覆盖原版本；仅原所有者 |
| POST | /library/questions/{template}/fork | `{source_version_id, question}`；201创建本人独立新题并记录来源；原作者或该版本接收方 |
| POST | /library/questions/{template}/restore | `{expected_version, source_version_id}`；201在当前头版本后追加恢复版本；仅原所有者 |
| GET / POST | /library/questions/{template}/versions/{version}/shares | 原作者查授权；POST输入 `{username}` 共享给已注册账号 |
| DELETE | /library/questions/{template}/versions/{version}/shares/{grantee} | 请求体 `{expected_version: 授权revision}`；撤销后停止新的使用准入 |
| POST | /surveys/{survey}/imports | `{expected_version, template_id, template_version_id}`；返回问卷及新version，追加一个新UUID实例 |
| POST | /surveys/{survey}/instances/{instance}/version | `{expected_version, template_version_id}`；保持实例ID和排序，只替换该实例并重验规则，仅草稿 |

`question`复用原Question结构：id、type、title、required、options（选择题）、validation。库内内容不保存输入中的问卷局部id；网页自动生成输入id，问卷导入时后端重新生成实例ID。题型和所有限制与原问卷编辑一致。

注意三种并发计数：问卷接口expected_version对应survey.version；版本保存/恢复对应template.edit_version；撤销共享对应grant.revision。它们都必须是严格整数，不能拿版本展示号v3代替管理计数，也不能传true。

问卷来源见响应question_sources：instance_id、template_id、template_version_id、version_number、title、content_hash、normalized_hash、modified。草稿的available_update只提示当前账号有权使用的新版；已发布时为null。来源由服务端维护，题目整组保存不能伪造此字段。本地改写保留历史来源，并标记modified；整组删除题目会一起移除当前在线引用。

422表示定义或规则不合法，409表示版本/状态冲突；无权限资源通常返回404。最终共享准入被撤销返回403；已在预检前撤销则404。数据库临时失败503后应重新加载确认结果，不能假定写入未发生。追加版本的预留可在重载时恢复，旧版本不覆盖。新旧版本、共享与引用的完整请求样例见自动测试 `tests/test_library_api.py` 和 `tests/test_library_versions.py`。

页面：`/library`管理题库，`/library/new`新建题目，`/library/{template}`历史/共享/引用，`/library/{template}/edit?version=版本ID`编辑指定版本。普通使用通过页面按钮完成。

## 已实现接口

| 方法 | 路径 | 输入与输出 | 权限 |
| --- | --- | --- | --- |
| GET | /health | 数据库在线返回 status、database、stage | 无 |
| POST | /api/auth/register | username、password → 201 用户公开字段 | 无 |
| POST | /api/auth/login | username、password → access_token、token_type、user | 无 |
| GET | /api/auth/me | 当前用户 id、username、created_at | 登录 |
| POST | /api/auth/logout | 撤销当前会话 → 204 | 登录 |
| POST | /api/surveys | title、description、allow_anonymous、allow_multiple、deadline → 201 完整草稿 | 登录 |
| GET | /api/surveys | 本人所有状态的问卷列表；当前无分页 | 登录 |
| GET | /api/surveys/{survey_id} | 本人问卷详情 | 所有者 |
| PUT | /api/surveys/{survey_id}/questions | expected_version、questions → 200 更新后的完整问卷，version 加一 | 所有者且为草稿 |
| PUT | /api/surveys/{survey_id}/rules | expected_version、rules → 校验并保存规则，version 加一 | 所有者且为草稿 |
| POST | /api/surveys/{survey_id}/publish | expected_version → 检查后发布，冻结定义，200 | 所有者且为草稿 |
| POST | /api/surveys/{survey_id}/close | expected_version → 关闭问卷，200 | 所有者且为已发布 |
| PATCH | /api/surveys/{survey_id}/settings | expected_version、deadline → 更新截止时间，200 | 所有者，草稿或已发布 |
| GET | /api/fill/{share_id} | 实时检查后返回题目、规则、期限和 survey_version | 登录且问卷可访问 |
| POST | /api/fill/{share_id}/responses | survey_version、submission_key、anonymous、answers → 201 保存；200 原请求重放回执 | 登录，按真实路径校验 |
| GET | /api/surveys/{survey_id}/statistics | 可选 question_id → 整卷或单题实时统计，200 | 所有者，任意状态 |
| GET | /api/me/responses | limit=20（1–100）、offset=0（0–10000）→ items、total、limit、offset；按时间和ID倒序 | 当前填写者本人 |
| GET | /api/me/responses/{response_id} | 已保存答卷的标题、版本、提交时间、匿名状态、path及含answer的访问题目 | 仅该答卷填写者本人，包含匿名 |

鉴权头为 `Authorization: Bearer <access_token>`。用户名 3–40 位字母数字下划线，统一小写；密码 8–128 字符，以随机盐 PBKDF2-SHA256 保存，绝不返回哈希。会话默认 24 小时，数据库仅保存 token 的 SHA256 摘要。

以上长度与会话时长是本项目实现选择，非教师硬性规定。创建和修改的截止时间可为空；非空需带时区且晚于当前时间。settings 中 deadline 必须显式提交，null 表示清除期限；不能提交过去时间立即停止收集，应使用 close。

`/survey/{share_id}` 已实现四种题型逐题填写、校验提示、规则跳转、上一题、确认页和真实保存回执。返回改答会清理不再访问的旧答案；草稿、关闭、过期或不存在时不返回题目定义。发布者右上角直接进入当前问卷统计。`/statistics/{survey_id}` 为整卷/单题统计页，`/response/{response_id}` 为本人答案详情页，工作台通过view=history查看历史。规则配置页为/rules/{survey_id}，工作台及编辑页可直接进入，普通使用无需Swagger。

## 错误约定

| HTTP 状态 | 含义 |
| --- | --- |
| 401 | 缺少登录、密码错误、会话无效或过期 |
| 404 | 问卷不存在或不属于当前用户；避免暴露他人问卷存在性 |
| 409 | 用户名重复；管理版本冲突/冻结；内容版本错误、禁止重复填写或同提交键不同内容 |
| 410 | 问卷已关闭或已截止，定义读取和新提交拒绝；已保存请求可查询原回执 |
| 422 | 输入/配置/逐题答案/真实路径不合法，匿名不被允许或超出 BSON 保存范围 |
| 503 | 运行过程中数据库不可用 |

错误体为 `{"detail": ...}`；Pydantic 校验错误的 detail 为数组。数据库不可用时应用启动直接失败，避免看似启动成功但不能保存。

请求体校验失败时，detail 数组只包含 type、loc、msg，不回显非法原始 input 或异常 ctx。Submission.answers 保留原始 JSON 类型，包含 true/false 而不转换为 1/0；任何深度的 NaN、Infinity、-Infinity 或溢出指数 1e999 被拒绝并安全返回 422。数字字符串仍是字符串。逐题或路径错误 detail 为 `{question_id,message}`，便于页面定位；客户端不能提交 path、respondent_id 或 owner_id 等额外字段。

## 草稿示例

```json
{"title":"校园调查","description":"约需两分钟","allow_anonymous":true,"allow_multiple":false,"deadline":null}
```

题目输入示例见 examples/questions.json。JumpRule 中 source_question_id 是来源题；eq用于单选，contains_any/contains_all用于多选，数字用gt/gte/lt/lte/eq/between；文本支持eq/contains，条件及答案裁剪两端空白后区分大小写比较，条件不能为空。between 采用闭区间两项数字数组。目标只能向后或 END，禁止悬空、自跳和回跳；END 为保留题号。运行时按规则数组顺序取首条匹配，无匹配则顺序继续；未回答的可选题不触发规则。服务端从首题重建路径，只检查所访问题的必答与其他限制。

## 发布状态与期限

发布、关闭、修改期限均要求 JSON 请求体中的 expected_version，原子匹配 owner_id、version 和当前 status 并加一。发布只接受有合法题目的草稿，并重新校验存储中的所有题目及规则、必答题是否可作答、deadline 是否未来；发布条件写入再检查 deadline，和并发题目编辑最多一个成功。关闭只接受 published（含已过期），已关闭不可恢复。

version 是管理修订号；发布时记录 published_version，作为冻结内容版本。期限变更不改变 published_version；GET /api/fill 返回该值作为 survey_version。旧版 published 文档若缺字段，读取回退为原 version，首次管理变更补写该基线，避免期限变更使旧填写内容版本失效。

所有者列表/详情包含实际 status、派生 availability（draft/open/expired/closed）及 server_time。过期条件为 deadline <= 当前 UTC 时间，不依赖状态字段定时更新。GET 和 POST 都重新读 MongoDB 并重新检查，前一次加载成功不构成后一次提交授权。已过期 published 可设置未来期限或 null 恢复访问；closed 不允许修改期限或重新发布。

示例：发布请求 `{"expected_version":2}`；成功后若 version=3，更新期限请求 `{"expected_version":3,"deadline":"2026-10-23T23:00:00+08:00"}`，或用 null 清除。始终使用实际最新版本，不能照抄示例版本。

## 题目保存语义

`PUT /api/surveys/{id}/questions` 用完整 questions 数组替换题目定义，数组顺序即题序；允许空数组清空草稿，最多 200 题。每题有稳定 id，选择题每个选项有稳定 id，后端原样保留，不能根据下标或文字重建 ID。排序只移动数组元素；删除即从数组移除。题干和选项内容去除首尾空白后不能为空，非法输入返回 422 且不写数据库。

expected_version 必须是 JSON 整数（不是布尔值或字符串），来自最近一次 GET 或成功保存返回的 version。数据库原子条件为 `_id + owner_id + status=draft + version=expected_version`，同一次更新写入 questions、updated_at 并 `$inc version:1`，不使用 upsert。成功返回数据库更新后的问卷。旧版本、已发布或已关闭均返回 409；越权或不存在返回 404；不修改问卷其他字段。

若已有跳转规则，保存题目不会清空规则，并保护现有规则引用：拒绝删除引用题目、把目标排到来源之前、更改来源题型或移除来源已有选项。编辑器遇到 409 保留本地内容并停止保存，用户明确重新加载后采用新版本，不能只更新 expected_version 然后盲目重发旧内容。

## 聚合统计

整卷与单题统一返回 `{survey_id, survey_version, total_submissions, questions}`；传 question_id 时 questions 仅含该题。survey_version 为 published_version（旧文档或草稿回退为 version），只统计同问卷、同内容版本的已保存答卷。期限修改或关闭不改变统计口径；已截止、已关闭仍可查。未登录 401，越权/不存在 404；所有者查询未知或空 question_id 返回 404，不提前读取答卷。

questions 保持题目定义顺序，每题包含 id、title、type、answered_count；单选和多选另有 options（id、label、count，保持定义顺序并补齐 0）；多选有 selection_count（全部选项的选择次数总和）；文本和数字有 values（按 submitted_at、_id 升序排列）；数字另有 average，零回答时为 null。无答卷时总数和所有计数为 0，values 为 []。

total_submissions 统计匹配的所有答卷数量，并非不同账号数；重复提交被允许时各份分别统计。answered_count 仅统计 path 中访问过、有有效非空答案的答卷；各题的回答数可不同。数字 0/0.0 正常参与，缺失/null/跳题不补 0。历史错误类型、未知或重复选项、配置约束外答案和非有限数排除出该题指标，但不减少整卷提交数。文本按既有校验语义裁剪首尾空白；纯空白不计入回答数。

实际计算通过 Repository 中一次 MongoDB `$match + $sort + $facet` 完成，服务层只关联题目元数据及补零，不在 Python 遍历答卷计算。返回内容不含填写者身份或内部幂等键。完整示例、手算表和运行方法见 [统计使用与手算核对](../03_测试用例/测试用例.md)。0.7.0统计页可见时每10秒请求同一接口，也支持手动刷新。

## 提交与重试契约

```json
{"survey_version":4,"submission_key":"demo_submission_000001","anonymous":false,"answers":{"q1":"a","q4":0}}
```

每次新的填写生成 UUID 或其他匹配 `[A-Za-z0-9_-]{16,100}` 的新键，不要反复照抄示例键；同一次 HTTP 重试保留相同输入和键。题号、选项及 survey_version 取自实际加载结果。answers 不接受未知题号和路径外答案，即使该答案是 null。有效可选遗漏从保存答案中移除，真实 path 仍保留该题，合法 0 保留。

首次插入成功返回 201：`{id,survey_id,survey_version,submitted_at,path,replayed:false}`；相同账号、同问卷、相同键与相同请求（对象键顺序可不同）返回同 id/时间/路径，200 且 replayed=true；同键不同内容返回 409，数字 0 与 0.0 也视为不同原始请求。已保存回执可在关闭/截止后重放，这不新增答卷。失败或回执不明时使用原键重试，不生成新键。

先验证账号，使用 MongoDB app_settings 的持久密钥计算问卷内 HMAC；匿名仅置空 respondent_id，不改变 respondent_key，因此切换匿名不能绕过禁止重复。禁止重复时 attempt_key=once；允许重复时用本次 submission_key。唯一索引保证并发限制，客户端不能决定这些内部键。

新提交检查状态、期限、冻结版本、匿名设置和服务端路径，BSON 预编码检查有符号 64 位整数范围、有效文本和单文档 16MiB 限制。最后对 surveys 做带状态、冻结版本、期限及 MongoDB $$NOW 条件的原子准入写入，再插入 responses。准入先于关闭的请求允许随后完成保存；关闭先于准入则拒绝。submission_sequence 只用于写入排序，不代表答卷数，不改变管理 version。不宣称两集合事务，插入失败返回 503 而非成功；客户端重试可恢复已保存但未收到的回执。详细流程见 [填写与提交使用说明](系统说明.md)。

0.5.0 的占位、195 项测试和 Word 报告保留为历史记录；0.6.0 已替换 next_question、validate_path 和提交服务的占位。expected_version 与 survey_version 仍严格要求正整数，其他配置字段不宣称全部采用严格模式。

## 本人历史查询契约

列表items只含id、survey_id、survey_version、title、share_id、submitted_at、anonymous。详情questions只列path中实际经过的题目，answer为保存后的原始类型和规范化值；选择题附选项定义，便于显示文字。不会返回respondent_key、respondent_id、attempt_key、submission_key、request_hash和secret。匿名提交仍可由本人查看；问卷所有者无权读取他人个人详情。未登录401，非本人/不存在404，内容版本缺失或不匹配409。

历史接口不按当前问卷开放状态拦截，因此结束后仍能回看；也不支持修改已提交答案。沿用持久HMAC识别旧记录，详情从冻结定义解释答案，不清库、不向匿名记录添加明文身份。参见统计与填写记录使用说明.md。



## API

都要求 `Authorization: Bearer <token>`。

`GET /api/library/questions/{template_id}/statistics` 返回 `versions[]`；每项有 `id`、`number`、`content`、`definition_errors`、`metrics`、`included_instances`、`excluded_instances`。顶层有 `policy`、`generated_at`、`source_history_available` 及全部有权显示的排除明细。无权查看的版本不会出现在结果中。

`POST /api/library/questions/{template_id}/statistics/merge` 示例：

```json
{
  "version_ids": ["v1-uuid", "v2-uuid"],
  "base_version_id": "v1-uuid",
  "option_mappings": {"v2-uuid": {"x": "a", "y": "b"}},
  "unit_confirmations": {}
}
```

`v1-uuid` 等是实际版本ID，不是版本序号字符串。数字旧版则用 `unit_confirmations: {"v1-uuid":"kg","v2-uuid":"kg"}`，不传选项映射。单版本展示无需POST。

合并返回 `performed`、`message`、`included_versions`、`excluded_versions[].reasons`、`included_instances`、`excluded_instances`、`metrics`、`unit`、`configuration`、`generated_at` 和 `policy`。通过版本有 `mapping`、`warnings`、`unit_source`；单位来源区分“版本声明”和“本次明确确认”。兼容检查未通过仍返回200和明确排除原因；无可执行合并时 `performed=false, metrics=null`。未登录401，不可访问题目/版本404，请求结构错误422。重复版本、未选择的基准、超限、额外配置键、空白单位都被拒绝。

`metrics` 包含上述三个计数、`type`、`excluded_answer_counts`；选择题有 `options[{id,label,count}]`，多选另有 `selection_count`；文本/数字有 `values`、`values_limit`、`values_truncated`，数字另有 `average`，无有效回答时为null。排除回答只给原因汇总，不给身份。返回数据不包含填写者账号、HMAC或个人身份。

## 分页及API

| 页面和接口 | 参数及返回 | 实际实现 |
| --- | --- | --- |
| 题库列表 `/api/question-banks` | `page=1&page_size=20`，返回items/page/has_more | Mongo排序created_at降序、ID升序，取size+1判断下一页。 |
| 题目列表 `/api/library/questions`、`/api/library/shared` | 同上，可附bank_id；题目返回page_size | Mongo按updated_at降序、ID升序分页；列表只预览最新可见版本，其他版本进入历史页。 |
| 历史 `/api/library/questions/{id}/history-page` | `page_size=10&before=版本号`，返回versions/next_before和管理令牌 | 内容编号唯一且不可变，使用number小于游标，避免历史深页skip；上一页由浏览器保存游标栈。 |
| 引用 `/api/library/questions/{id}/versions/{version}/usages` | `page=1&page_size=20`，返回items/page/page_size/has_more | 先按所有者和来源版本筛问卷，再聚合旧/新引用、按实例展开，排序问卷ID及题序后分页。重复实例分别显示。 |
| 统计GET及合并POST | 增加`value_page=1` | 每页100个文本/数字有效值；values_page、values_has_more控制内容按钮；计数和平均值不分页。合并时继续带同一明确配置。 |

page范围1～10000，page_size范围1～100，before必须正数；不合法422。历史内容最多每页100版。原列表不传page仍保留0.9兼容数组契约，旧详情/versions接口供既有调用；当前题库和历史页面已切到分页入口，不用兼容全量接口来伪装分页。

列表和引用目前是offset分页，深页仍有skip成本；不是所有查询都变成O(页大小)。历史使用游标。统计内容分页仍重算聚合，按问卷ID、答卷提交时间/ID及冻结实例顺序展示；有新提交或管理列表更新时不承诺跨请求快照，必要时从首页刷新。全部查询每次重新鉴权，不缓存他人权限。

